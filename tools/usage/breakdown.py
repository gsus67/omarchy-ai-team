"""Where the tokens go: totals per kind, model, project and tool from local transcripts.

Reads the Claude and Codex session files read-only and returns the body of a
breakdown record (see ``scan_claude`` / ``scan_codex``). A message with several
tool calls splits its tokens and cost equally between them: an estimate, not a
measurement. A model without a price contributes tokens but no cost.
"""

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import prices as price_table

NO_PROJECT = '(sin proyecto)'
TEXT_ONLY = '(solo texto)'
TOP = 8
KIND_NAMES = ('input', 'output', 'cacheRead', 'cacheWrite')


def _number(value):
    try:
        return max(0, round(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _measurement_number(value):
    """A counter that was really measured: an int in [0, 10**15). Anything else is unknown (None), never 0."""
    return value if type(value) is int and 0 <= value < 10 ** 15 else None


def _moment(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (ValueError, OverflowError):  # an instant UTC cannot represent is an unknown date
        return None


class _Group:
    def __init__(self):
        self.total = 0.0
        self.cost = None

    def add(self, tokens, cost):
        self.total += tokens
        if cost is not None:
            self.cost = (self.cost or 0.0) + cost


class _Totals:
    """Accumulates messages; ``body`` renders the breakdown record."""

    def __init__(self, prices, days):
        self.prices = prices
        self.days = days
        self.kinds = dict.fromkeys(KIND_NAMES, 0)
        self.models = {}
        self.projects = {}
        self.tools = {}
        self.unpriced = set()
        self.messages = 0

    def add(self, model, project, tools, kinds):
        tokens = sum(kinds.values())
        cost = price_table.cost_usd(self.prices, model, kinds)
        self.messages += 1
        for kind in KIND_NAMES:
            self.kinds[kind] += kinds[kind]
        row = self.models.setdefault(model, {'kinds': dict.fromkeys(KIND_NAMES, 0), 'group': _Group()})
        for kind in KIND_NAMES:
            row['kinds'][kind] += kinds[kind]
        row['group'].add(tokens, cost)
        if cost is None:
            self.unpriced.add(model)
        self.projects.setdefault(project or NO_PROJECT, _Group()).add(tokens, cost)
        names = tools or [TEXT_ONLY]
        share = len(names)
        for name in names:
            self.tools.setdefault(name, _Group()).add(tokens / share, None if cost is None else cost / share)

    @staticmethod
    def _top(groups, key):
        rows = [{key: name, 'total': round(group.total), 'costUsd': group.cost} for name, group in groups.items()]
        rows.sort(key=lambda row: (-row['total'], row[key]))
        return rows[:TOP]

    def body(self, since_start=None):
        if not self.messages and since_start is None:
            return None
        by_model = [{'model': model, **row['kinds'], 'total': round(row['group'].total), 'costUsd': row['group'].cost}
                    for model, row in self.models.items()]
        by_model.sort(key=lambda row: (-row['total'], row['model']))
        priced = [row['costUsd'] for row in by_model if row['costUsd'] is not None]
        body = {
            'windowDays': self.days,
            'byKind': dict(self.kinds),
            'byModel': by_model,
            'byProject': self._top(self.projects, 'path'),
            'byTool': self._top(self.tools, 'name'),
            'costUsd': sum(priced) if priced else None,
            'unpricedModels': sorted(self.unpriced),
        }
        if since_start is not None:
            body['sinceStart'] = since_start
        return body


def _lines(path):
    """JSON objects of a JSONL file; unreadable files raise (a partial scan must not look complete)."""
    try:
        handle = path.open('r', encoding='utf-8', errors='replace')
    except FileNotFoundError:
        return  # rotated away while scanning
    with handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                yield entry


def _is_dir(root):
    """Whether ``root`` is a directory; only an absent one is False, any other access error is raised."""
    try:
        return stat.S_ISDIR(os.stat(root).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False


def _recent_files(root, cutoff):
    """Transcripts modified since ``cutoff``; without a cutoff, all of them (a start can predate any mtime).

    The whole tree is enumerated before the first file is returned and any access error is raised (also one
    classifying an entry, which ``os.walk`` would swallow), so a partially readable source can never pass for a
    complete one. A file that vanishes meanwhile is skipped; symlinks are never followed.
    """
    found, pending = [], [root] if _is_dir(root) else []
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry.path)
                elif entry.name.endswith('.jsonl'):
                    try:
                        info = os.lstat(entry.path)
                    except FileNotFoundError:
                        continue
                    if stat.S_ISREG(info.st_mode) and (cutoff is None or datetime.fromtimestamp(info.st_mtime, timezone.utc) >= cutoff):
                        found.append(Path(entry.path))  # symlinks are skipped: an alias could pull in (or duplicate) files from outside the profile
    yield from sorted(found)


_CLAUDE_FIELDS = (('input', 'input_tokens'), ('output', 'output_tokens'),
                  ('cacheRead', 'cache_read_input_tokens'), ('cacheWrite', 'cache_creation_input_tokens'))


def _raise_to_maxima(kinds, usage):
    for kind, field in _CLAUDE_FIELDS:
        kinds[kind] = max(kinds[kind], _number(usage.get(field)))


def _measure_claude(life, usage):
    """Fold one usage block into a message's lifetime: valid counters raise the maxima, an invalid one poisons it."""
    for kind, field in _CLAUDE_FIELDS:
        if field not in usage:
            continue  # another line of the same message may carry it
        number = _measurement_number(usage[field])
        if number is None:
            life['invalid'] = True
        else:
            life['kinds'][kind] = max(life['kinds'][kind], number)
            life['seen'].add(kind)


def _claude_since_start(lifetime, since):
    """``sinceStart`` from every message ever seen, or None if a message has no valid timestamp, or one at or
    after the start lacks a valid counter (unknown, not zero). Messages before the start are not measured."""
    if any(life['at'] is None for life in lifetime.values()):
        return None
    relevant = [life for life in lifetime.values() if life['at'] >= since]
    if any(life['invalid'] or len(life['seen']) < len(KIND_NAMES) for life in relevant):
        return None
    counted = [life['kinds'] for life in relevant]
    return {'since': since.isoformat(),
            'total_input': sum(k['input'] + k['cacheRead'] + k['cacheWrite'] for k in counted),
            'output': sum(k['output'] for k in counted)}


def scan_claude(projects_dir, prices, days, now, *, since=None):
    """Breakdown body for ``<claude dir>/projects`` over the last ``days`` days, or None without messages.

    Claude writes each content block of one message as its own line with the same
    message id: usage is the largest seen and the tool calls of all its lines are merged.
    With ``since`` (an aware instant) the same pass also adds ``sinceStart``: the tokens of the messages whose
    earliest timestamp is at or after it, whatever the window or the file's mtime; the 30-day detail is unchanged.
    """
    cutoff = now - timedelta(days=days)
    since = since.astimezone(timezone.utc) if since is not None else None
    messages, lifetime = {}, {}
    for path in _recent_files(projects_dir, cutoff if since is None else None):
        for number, entry in enumerate(_lines(path), 1):
            message = entry.get('message')
            if entry.get('type') != 'assistant' or not isinstance(message, dict):
                continue
            usage = message.get('usage')
            if not isinstance(usage, dict):
                continue
            moment = _moment(entry.get('timestamp'))
            key = message.get('id') or '%s:%d' % (path, number)
            if since is not None:
                life = lifetime.setdefault(key, {'at': None, 'kinds': dict.fromkeys(KIND_NAMES, 0), 'seen': set(), 'invalid': False})
                if moment is not None and (life['at'] is None or moment < life['at']):
                    life['at'] = moment
                _measure_claude(life, usage)
            if moment is None or moment < cutoff:
                continue
            record = messages.setdefault(key, {
                'model': str(message.get('model') or 'unknown'), 'project': None,
                'kinds': dict.fromkeys(KIND_NAMES, 0), 'tools': {}})
            if isinstance(entry.get('cwd'), str):
                record['project'] = entry['cwd']
            _raise_to_maxima(record['kinds'], usage)
            content = message.get('content')
            for position, block in enumerate(content if isinstance(content, list) else []):
                if isinstance(block, dict) and block.get('type') == 'tool_use' and isinstance(block.get('name'), str):
                    record['tools'][block.get('id') or (block['name'], position)] = block['name']
    totals = _Totals(prices, days)
    for record in messages.values():
        totals.add(record['model'], record['project'], list(record['tools'].values()), record['kinds'])
    if since is None:
        return totals.body()
    if not _is_dir(projects_dir):
        return None  # no source to measure: unknown, not a zero
    return totals.body(_claude_since_start(lifetime, since))


def scan_codex(sessions_dir, prices, days, now, extra_dirs=(), *, since=None):
    """Breakdown body for ``<codex home>/sessions`` over the last ``days`` days, or None without turns.

    Only ``token_count`` events carry tokens (``token_usage_record`` repeats them). Codex counts cached
    tokens inside ``input_tokens``, so fresh input is what is left after cached and cache-write tokens.
    The tool calls seen since the previous ``token_count`` share that event's tokens.
    ``extra_dirs`` are scanned too (Codex moves archived conversations to ``archived_sessions``).
    With ``since`` (an aware instant) the same pass also adds ``sinceStart``: ``input_tokens`` (already
    including the cache) and ``output_tokens`` of the events at or after it, whatever the window or mtime.
    """
    cutoff = now - timedelta(days=days)
    since = since.astimezone(timezone.utc) if since is not None else None
    totals = _Totals(prices, days)
    lifetime = {'unknown': False, 'total_input': 0, 'output': 0}
    paths = [path for root in (sessions_dir, *extra_dirs) for path in _recent_files(root, cutoff if since is None else None)]
    for path in paths:
        project = model = last_total = last_lifetime_total = None
        pending = []
        for entry in _lines(path):
            payload = entry.get('payload')
            if not isinstance(payload, dict):
                continue
            kind = entry.get('type')
            if kind == 'turn_context':
                project = payload['cwd'] if isinstance(payload.get('cwd'), str) else project
                model = payload['model'] if isinstance(payload.get('model'), str) else model
            elif kind == 'session_meta':
                project = project or (payload['cwd'] if isinstance(payload.get('cwd'), str) else None)
            elif kind == 'response_item' and payload.get('type') in ('function_call', 'custom_tool_call'):
                if isinstance(payload.get('name'), str):
                    pending.append(payload['name'])
            elif kind == 'event_msg' and payload.get('type') == 'token_count':
                names, pending = pending, []
                info = payload.get('info')
                usage = info.get('last_token_usage') if isinstance(info, dict) else None
                moment = _moment(entry.get('timestamp'))
                if since is not None and isinstance(usage, dict):
                    total = info.get('total_token_usage')
                    repeated = isinstance(total, dict) and total == last_lifetime_total
                    last_lifetime_total = total if isinstance(total, dict) else last_lifetime_total
                    if not repeated:  # the repeat check comes before any date filter
                        if moment is None:
                            lifetime['unknown'] = True
                        elif moment >= since:
                            counters = [_measurement_number(usage.get(field)) for field in ('input_tokens', 'output_tokens')]
                            if 'cached_input_tokens' in usage:
                                counters.append(_measurement_number(usage['cached_input_tokens']))
                            if None in counters:
                                lifetime['unknown'] = True  # a consumption without a valid counter: no total, not a zero
                            else:
                                lifetime['total_input'] += counters[0]
                                lifetime['output'] += counters[1]
                if not isinstance(usage, dict) or moment is None or moment < cutoff:
                    continue
                total = info.get('total_token_usage')
                if isinstance(total, dict):
                    if total == last_total:
                        continue  # Codex repeats the event when only the rate limits changed
                    last_total = total
                cached = _number(usage.get('cached_input_tokens'))
                written = _number(usage.get('cache_write_input_tokens'))
                kinds = {'input': max(0, _number(usage.get('input_tokens')) - cached - written),
                         'output': _number(usage.get('output_tokens')), 'cacheRead': cached, 'cacheWrite': written}
                totals.add(model or 'unknown', project, names, kinds)
    if since is None:
        return totals.body()
    if not any(_is_dir(root) for root in (sessions_dir, *extra_dirs)):
        return None  # no source to measure: unknown, not a zero
    if lifetime.pop('unknown'):
        return totals.body()
    return totals.body({'since': since.isoformat(), **lifetime})
