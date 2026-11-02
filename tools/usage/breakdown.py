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
    try:
        with path.open('r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    yield entry
    except OSError:
        return


def _recent_files(root, cutoff):
    for path in sorted(root.rglob('*.jsonl')) if root.is_dir() else []:
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) >= cutoff:
                yield path
        except OSError:
            continue


def scan_claude(projects_dir, prices, days, now):
    """Breakdown body for ``<claude dir>/projects`` over the last ``days`` days, or None without messages."""
    cutoff = now - timedelta(days=days)
    totals = _Totals(prices, days)
    seen = set()
    for path in _recent_files(projects_dir, cutoff):
        for number, entry in enumerate(_lines(path), 1):
            message = entry.get('message')
            if entry.get('type') != 'assistant' or not isinstance(message, dict):
                continue
            usage = message.get('usage')
            moment = _moment(entry.get('timestamp'))
            if not isinstance(usage, dict) or moment is None or moment < cutoff:
                continue
            key = message.get('id') or '%s:%d' % (path, number)
            if key in seen:
                continue
            seen.add(key)
            kinds = {'input': _number(usage.get('input_tokens')), 'output': _number(usage.get('output_tokens')),
                     'cacheRead': _number(usage.get('cache_read_input_tokens')),
                     'cacheWrite': _number(usage.get('cache_creation_input_tokens'))}
            content = message.get('content')
            tools = [block['name'] for block in content if isinstance(block, dict)
                     and block.get('type') == 'tool_use' and isinstance(block.get('name'), str)] if isinstance(content, list) else []
            totals.add(str(message.get('model') or 'unknown'), entry.get('cwd') if isinstance(entry.get('cwd'), str) else None,
                       tools, kinds)
    return totals.body()
