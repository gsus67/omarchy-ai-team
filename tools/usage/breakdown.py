"""Where the tokens go: totals per kind, model, project and tool from local transcripts.

Reads the Claude and Codex session files read-only and returns the body of a
breakdown record (see ``scan_claude`` / ``scan_codex``). A message with several
tool calls splits its tokens and cost equally between them: an estimate, not a
measurement. A model without a price contributes tokens but no cost.
"""

import json
import os
import re
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

    def body(self, measurement=None):
        if not self.messages and measurement is None:
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
        if measurement is not None:
            body['measurement'] = measurement
        return body


def _window_start(since, now, total_days):
    """The cut of the selected rolling window, ``max(since, now - total_days)`` in UTC; None when the start is after
    ``now`` (a clock gone back) or the arithmetic is not representable: the interval is then unknown, not empty."""
    try:
        since, now = since.astimezone(timezone.utc), now.astimezone(timezone.utc)
        return None if since > now else max(since, now - timedelta(days=total_days))
    except OverflowError:
        return None


class _Measure:
    """The two partitions of the selected interval, as ``total_input`` and ``output`` counters per bucket."""

    def __init__(self):
        self.counters = {'jefe': [0, 0], 'otros': [0, 0]}
        self.limited = False

    def add(self, bucket, limited, total_input, output):
        self.counters[bucket][0] += total_input
        self.counters[bucket][1] += output
        self.limited = self.limited or (limited and total_input + output > 0)  # a limitation only matters where there is usage

    def block(self, start, since, total_days):
        return {'start': start.isoformat(), 'since': since.isoformat(), 'windowDays': total_days,
                'jefe': {'total_input': self.counters['jefe'][0], 'output': self.counters['jefe'][1]},
                'otros': {'total_input': self.counters['otros'][0], 'output': self.counters['otros'][1]},
                'classificationLimited': self.limited}


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


_CLAUDE_ENTRYPOINTS = ('cli', 'sdk-cli')


def _claude_origin(entry):
    """The native origin of one line: ``(entrypoint, isSidechain)``, or None if either field is missing or invalid."""
    entrypoint, sidechain = entry.get('entrypoint'), entry.get('isSidechain')
    if entrypoint in _CLAUDE_ENTRYPOINTS and isinstance(sidechain, bool):
        return entrypoint, sidechain
    return None


def _claude_bucket(origins, managed_runs):
    """``(bucket, limited)`` for the origins of all the lines of one message, combined before summing.

    Interactive ``cli`` (not sidechain) is Jefe; a ``cli`` sidechain is Otros. ``sdk-cli`` is Otros unless managed runs
    exist for this engine in the window (then it may be ours: Jefe, limited). Missing, invalid or conflicting origins are
    Jefe, limited: nothing is dropped, so Jefe + Otros always equals the canonical measurement."""
    if len(origins) != 1 or None in origins:
        return 'jefe', True
    (entrypoint, sidechain), = origins
    if entrypoint == 'sdk-cli':
        return ('jefe', True) if managed_runs else ('otros', False)
    return ('otros', False) if sidechain else ('jefe', False)


def _claude_measure(lifetime, cut, now, managed_runs=True):
    """The partitions of every message first seen in ``[cut, now]``, or None if a message has no valid timestamp,
    or one inside the interval lacks a valid counter (unknown, not zero). Messages outside it are not measured."""
    if any(life['at'] is None for life in lifetime.values()):
        return None
    relevant = [life for life in lifetime.values() if cut <= life['at'] <= now]
    if any(life['invalid'] or len(life['seen']) < len(KIND_NAMES) for life in relevant):
        return None
    measure = _Measure()
    for life in relevant:
        kinds = life['kinds']
        bucket, limited = _claude_bucket(life['origins'], managed_runs)
        measure.add(bucket, limited, kinds['input'] + kinds['cacheRead'] + kinds['cacheWrite'], kinds['output'])
    return measure


def scan_claude(projects_dir, prices, days, now, *, since=None, total_days=7, managed_runs=True):
    """Breakdown body for ``<claude dir>/projects`` over the last ``days`` days, or None without messages.

    Claude writes each content block of one message as its own line with the same
    message id: usage is the largest seen and the tool calls of all its lines are merged.
    With ``since`` (the common start, aware) the same pass also adds ``measurement``: the tokens of the messages whose
    earliest timestamp is in ``[max(since, now - total_days), now]``, whatever the file's mtime; the 30-day detail is unchanged.
    """
    cutoff = now - timedelta(days=days)
    since = since.astimezone(timezone.utc) if since is not None else None
    cut = _window_start(since, now, total_days) if since is not None else None
    measuring = cut is not None
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
            if measuring:
                life = lifetime.setdefault(key, {'at': None, 'kinds': dict.fromkeys(KIND_NAMES, 0), 'seen': set(), 'invalid': False, 'origins': set()})
                if moment is not None and (life['at'] is None or moment < life['at']):
                    life['at'] = moment
                life['origins'].add(_claude_origin(entry))
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
    if not measuring:
        return totals.body()
    if not _is_dir(projects_dir):
        return None  # no source to measure: unknown, not a zero
    measure = _claude_measure(lifetime, cut, now, managed_runs)
    return totals.body(None if measure is None else measure.block(since, cut, total_days))


_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
_CODEX_MAX_HOPS = 8


def _codex_parent(payload):
    """``(parent, invalid)`` from the two native parent fields of a ``session_meta`` payload: the nested
    ``source.subagent.thread_spawn.parent_thread_id`` and the direct ``parent_thread_id``. Absent or null is no declared link;
    a non-null value that is not a UUID, or two different parents, make the link ``invalid`` (never silently prefer one)."""
    source = payload.get('source')
    spawn = source.get('subagent') if isinstance(source, dict) else None
    spawn = spawn.get('thread_spawn') if isinstance(spawn, dict) else None
    declared = [value for value in (spawn.get('parent_thread_id') if isinstance(spawn, dict) else None,
                                    payload.get('parent_thread_id')) if value is not None]
    if not all(isinstance(value, str) and _UUID.fullmatch(value) for value in declared) or len(set(declared)) > 1:
        return None, True
    return (declared[0] if declared else None), False


def _is_spawned(source):
    """Whether ``source`` has the recognised sub-agent shape ``{'subagent': {'thread_spawn': {...}}}`` (possibly empty)."""
    spawn = source.get('subagent') if isinstance(source, dict) else None
    return isinstance(spawn, dict) and isinstance(spawn.get('thread_spawn'), dict)


def _codex_bucket(rollout, by_id, managed_runs):
    """``(bucket, limited)`` of one rollout from its native ``source``.

    Own identity is the first ``session_meta``, whose id must match the file name (and be unique); a later one with a
    foreign id makes the file a fork. A root is Jefe if ``cli`` and follows the managed gate if ``exec``; a sub-agent follows
    its chain of parents to the root (``cli`` root: Otros). Whatever cannot be confirmed (fork, identity, unknown source,
    missing or contradictory parent, cycle, more than 8 hops) stays Jefe, limited. ``originator`` and agent roles are ignored."""
    seen, current = set(), rollout
    for hops in range(_CODEX_MAX_HOPS + 1):
        if not current['own'] or current['fork'] or len(by_id[current['id']]) != 1 or id(current) in seen:
            return 'jefe', True
        seen.add(id(current))
        parent, invalid = current['parent'], current['parent_invalid']
        if invalid or (parent is not None and not _is_spawned(current['source'])):
            return 'jefe', True
        if parent is None:
            if current['source'] == 'exec':
                return ('jefe', True) if managed_runs else ('otros', False)
            if current['source'] == 'cli':
                return ('otros', False) if hops else ('jefe', False)
            if current['source'] == 'vscode' and not hops:
                return 'jefe', False
            return 'jefe', True
        candidates = by_id.get(parent)
        if not candidates:
            return 'jefe', True
        current = candidates[0]
    return 'jefe', True


def scan_codex(sessions_dir, prices, days, now, extra_dirs=(), *, since=None, total_days=7, managed_runs=True):
    """Breakdown body for ``<codex home>/sessions`` over the last ``days`` days, or None without turns.

    Only ``token_count`` events carry tokens (``token_usage_record`` repeats them). Codex counts cached
    tokens inside ``input_tokens``, so fresh input is what is left after cached and cache-write tokens.
    The tool calls seen since the previous ``token_count`` share that event's tokens.
    ``extra_dirs`` are scanned too (Codex moves archived conversations to ``archived_sessions``).
    With ``since`` (the common start, aware) the same pass also adds ``measurement``: ``input_tokens`` (already
    including the cache) and ``output_tokens`` of the events in ``[max(since, now - total_days), now]``, whatever the mtime.
    """
    cutoff = now - timedelta(days=days)
    since = since.astimezone(timezone.utc) if since is not None else None
    cut = _window_start(since, now, total_days) if since is not None else None
    measuring = cut is not None
    totals = _Totals(prices, days)
    measure, unknown = _Measure(), False
    rollouts, by_id = [], {}
    paths = [path for root in (sessions_dir, *extra_dirs) for path in _recent_files(root, cutoff if since is None else None)]
    for path in paths:
        project = model = last_total = last_lifetime_total = None
        pending = []
        rollout = {'file': _UUID.fullmatch(path.stem[-36:]), 'id': None, 'own': False, 'fork': False, 'source': None, 'parent': None, 'parent_invalid': False,
                   'counted': [0, 0], 'meta': False}
        rollouts.append(rollout)
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
                if payload.get('forked_from_id') is not None:
                    rollout['fork'] = True  # an explicit fork: the file may replay another conversation
                if not rollout['meta']:
                    rollout.update(meta=True, id=payload.get('id'), source=payload.get('source'))
                    rollout['parent'], rollout['parent_invalid'] = _codex_parent(payload)
                    rollout['own'] = rollout['file'] is not None and payload.get('id') == rollout['file'][0]
                    if rollout['own']:
                        by_id.setdefault(rollout['id'], []).append(rollout)
                elif payload.get('id') != rollout['id']:
                    rollout['fork'] = True  # foreign metadata inside the file: it replays another conversation
            elif kind == 'response_item' and payload.get('type') in ('function_call', 'custom_tool_call'):
                if isinstance(payload.get('name'), str):
                    pending.append(payload['name'])
            elif kind == 'event_msg' and payload.get('type') == 'token_count':
                names, pending = pending, []
                info = payload.get('info')
                usage = info.get('last_token_usage') if isinstance(info, dict) else None
                moment = _moment(entry.get('timestamp'))
                if measuring and isinstance(usage, dict):
                    total = info.get('total_token_usage')
                    repeated = isinstance(total, dict) and total == last_lifetime_total
                    last_lifetime_total = total if isinstance(total, dict) else last_lifetime_total
                    if not repeated:  # the repeat check comes before any date filter
                        if moment is None:
                            unknown = True
                        elif cut <= moment <= now:
                            counters = [_measurement_number(usage.get(field)) for field in ('input_tokens', 'output_tokens')]
                            if 'cached_input_tokens' in usage:
                                counters.append(_measurement_number(usage['cached_input_tokens']))
                            if None in counters:
                                unknown = True  # a consumption without a valid counter: no total, not a zero
                            else:
                                rollout['counted'][0] += counters[0]
                                rollout['counted'][1] += counters[1]
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
    if not measuring:
        return totals.body()
    if not any(_is_dir(root) for root in (sessions_dir, *extra_dirs)):
        return None  # no source to measure: unknown, not a zero
    for rollout in rollouts:  # the partitions are resolved in memory from the same pass
        bucket, limited = _codex_bucket(rollout, by_id, managed_runs)
        measure.add(bucket, limited, *rollout['counted'])
    return totals.body(None if unknown else measure.block(since, cut, total_days))
