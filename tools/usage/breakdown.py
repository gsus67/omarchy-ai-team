"""Where the tokens go: totals per kind, model, project and tool from local transcripts.

Reads the Claude and Codex session files read-only and returns the body of a
breakdown record (see ``scan_claude`` / ``scan_codex``). A message with several
tool calls splits its tokens and cost equally between them: an estimate, not a
measurement. A model without a price contributes tokens but no cost.
"""

import json
from datetime import datetime, timedelta, timezone

import prices as price_table

NO_PROJECT = '(sin proyecto)'
TEXT_ONLY = '(solo texto)'
TOP = 8
KIND_NAMES = ('input', 'output', 'cacheRead', 'cacheWrite')


def _number(value):
    try:
        return max(0, round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _moment(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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

    def body(self):
        if not self.messages:
            return None
        by_model = [{'model': model, **row['kinds'], 'total': round(row['group'].total), 'costUsd': row['group'].cost}
                    for model, row in self.models.items()]
        by_model.sort(key=lambda row: (-row['total'], row['model']))
        priced = [row['costUsd'] for row in by_model if row['costUsd'] is not None]
        return {
            'windowDays': self.days,
            'byKind': dict(self.kinds),
            'byModel': by_model,
            'byProject': self._top(self.projects, 'path'),
            'byTool': self._top(self.tools, 'name'),
            'costUsd': sum(priced) if priced else None,
            'unpricedModels': sorted(self.unpriced),
        }


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


def _recent_files(root, cutoff):
    for path in sorted(root.rglob('*.jsonl')) if root.is_dir() else []:
        if path.is_symlink():
            continue  # an alias could pull in (or duplicate) files from outside the profile
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) >= cutoff:
                yield path
        except FileNotFoundError:
            continue


def scan_claude(projects_dir, prices, days, now):
    """Breakdown body for ``<claude dir>/projects`` over the last ``days`` days, or None without messages.

    Claude writes each content block of one message as its own line with the same
    message id: usage is the largest seen and the tool calls of all its lines are merged.
    """
    cutoff = now - timedelta(days=days)
    messages = {}
    for path in _recent_files(projects_dir, cutoff):
        for number, entry in enumerate(_lines(path), 1):
            message = entry.get('message')
            if entry.get('type') != 'assistant' or not isinstance(message, dict):
                continue
            usage = message.get('usage')
            moment = _moment(entry.get('timestamp'))
            if not isinstance(usage, dict) or moment is None or moment < cutoff:
                continue
            record = messages.setdefault(message.get('id') or '%s:%d' % (path, number), {
                'model': str(message.get('model') or 'unknown'), 'project': None,
                'kinds': dict.fromkeys(KIND_NAMES, 0), 'tools': {}})
            if isinstance(entry.get('cwd'), str):
                record['project'] = entry['cwd']
            for kind, field in (('input', 'input_tokens'), ('output', 'output_tokens'),
                                ('cacheRead', 'cache_read_input_tokens'), ('cacheWrite', 'cache_creation_input_tokens')):
                record['kinds'][kind] = max(record['kinds'][kind], _number(usage.get(field)))
            content = message.get('content')
            for position, block in enumerate(content if isinstance(content, list) else []):
                if isinstance(block, dict) and block.get('type') == 'tool_use' and isinstance(block.get('name'), str):
                    record['tools'][block.get('id') or (block['name'], position)] = block['name']
    totals = _Totals(prices, days)
    for record in messages.values():
        totals.add(record['model'], record['project'], list(record['tools'].values()), record['kinds'])
    return totals.body()


def scan_codex(sessions_dir, prices, days, now, extra_dirs=()):
    """Breakdown body for ``<codex home>/sessions`` over the last ``days`` days, or None without turns.

    Only ``token_count`` events carry tokens (``token_usage_record`` repeats them). Codex counts cached
    tokens inside ``input_tokens``, so fresh input is what is left after cached and cache-write tokens.
    The tool calls seen since the previous ``token_count`` share that event's tokens.
    ``extra_dirs`` are scanned too (Codex moves archived conversations to ``archived_sessions``).
    """
    cutoff = now - timedelta(days=days)
    totals = _Totals(prices, days)
    paths = [path for root in (sessions_dir, *extra_dirs) for path in _recent_files(root, cutoff)]
    for path in paths:
        project = model = last_total = None
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
    return totals.body()
