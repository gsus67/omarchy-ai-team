"""Five-hour and weekly usage of each selected account, for the main screen.

Reads the records the Omarchy agents panel already maintains in
~/.local/state/omarchy/agents/usage/ (read-only; nothing is collected here).
"""

import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

from i18n import tr

# Only these accounts have limit windows; the short names fit a 380 px screen.
SHORT_NAMES = {'claude': 'Claude', 'claude-2': 'Claude 2', 'codex': 'Codex', 'codex-2': 'Codex 2'}
MAX_BYTES = 1024 * 1024


def _directory():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'omarchy/agents/usage'


def _read_record(path):
    """The parsed record, or None for anything that is not a small regular JSON file."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)  # never follow links or wait on a pipe
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        data = b''
        while len(data) <= MAX_BYTES:
            chunk = os.read(descriptor, MAX_BYTES + 1 - len(data))
            if not chunk:
                break
            data += chunk
        if len(data) > MAX_BYTES:
            return None
        record = json.loads(data)
    except (OSError, ValueError, RecursionError):
        return None
    finally:
        os.close(descriptor)
    return record if isinstance(record, dict) else None


def _seconds_left(limit):
    reset = limit.get('resetsAt')
    if not isinstance(reset, str):
        return None
    try:
        moment = datetime.fromisoformat(reset.replace('Z', '+00:00'))
    except ValueError:
        return None
    return ((moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)) - datetime.now(timezone.utc)).total_seconds()


def duration_text(seconds):
    if seconds < 60:
        return '<1 min'
    minutes = int(seconds // 60)
    if minutes >= 24 * 60:
        return '%d d %d h' % (minutes // (24 * 60), minutes % (24 * 60) // 60)
    if minutes >= 60:
        return '%d h %d min' % (minutes // 60, minutes % 60)
    return '%d min' % minutes


def tokens_text(count):
    for size, suffix in ((1_000_000_000, 'B'), (1_000_000, 'M'), (1_000, 'K')):
        if count >= size:
            return '%.1f%s' % (count / size, suffix)
    return str(int(count))


def money_text(usd):
    if usd is None:
        return '—'
    if usd == 0:
        return '$0.00'
    return '<$0.01' if usd < 0.01 else '$%.2f' % usd


def _label(name):
    """Placeholder names the breakdown writes in Spanish are translated; real names pass through."""
    if name == '(sin proyecto)':
        return tr('(sin proyecto)')
    if name == '(solo texto)':
        return tr('(solo texto)')
    return name


def short_path(path):
    if not path.startswith('/'):
        return _label(path)
    home = str(Path.home())
    prefix, rest = '', path
    if path == home:
        return '~'
    if path.startswith(home + '/'):
        prefix, rest = '~/', path[len(home) + 1:]
    parts = [part for part in rest.split('/') if part]
    if len(parts) > 3:
        return '…/' + '/'.join(parts[-2:])
    return prefix + '/'.join(parts) if prefix else '/' + '/'.join(parts)


def _percent(limit):
    value = limit.get('percent')
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        return None
    left = _seconds_left(limit)
    if left is not None and left <= 0:
        return 0  # the window already reset; the record just has not been rewritten yet
    return round(value * 100)


def _window(limits, pattern):
    for limit in limits:
        if isinstance(limit, dict) and isinstance(limit.get('label'), str) and re.search(pattern, limit['label']):
            return _percent(limit)
    return None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _windows(limits):
    result = []
    for limit in limits:
        if not isinstance(limit, dict) or not isinstance(limit.get('label'), str):
            continue
        percent = _percent(limit)
        if percent is None:
            continue
        label = limit['label']
        if re.search(r'^(Session \(5|5h)', label):
            label = tr('5 h')
        elif re.search(r'^Weekly \(7-day\)$', label):
            label = tr('Semanal')
        left = _seconds_left(limit)
        result.append({'label': label[:40], 'percent': percent,
                       'reset': tr('Reinicia en {tiempo}', tiempo=duration_text(left)) if left is not None and left > 0 else ''})
    return result


def _models(record):
    usage = record.get('modelUsage')
    rows_ = []
    for name, bucket in (usage.items() if isinstance(usage, dict) else []):
        if isinstance(bucket, dict):
            total = sum(value for key, value in bucket.items() if _number(value) and key.endswith('Tokens'))
            rows_.append((total, str(name)[:40]))
    rows_.sort(key=lambda item: (-item[0], item[1]))
    return [{'name': name, 'tokens': tokens_text(total)} for total, name in rows_[:4]]


def _valid_breakdown(data):
    def rows_ok(rows_, key):
        return isinstance(rows_, list) and all(isinstance(row, dict) and isinstance(row.get(key), str) and _number(row.get('total'))
                                              and (row.get('costUsd') is None or _number(row.get('costUsd'))) for row in rows_)
    kinds = data.get('byKind') if isinstance(data, dict) else None
    return (isinstance(data, dict) and data.get('schemaVersion') == 1 and isinstance(kinds, dict)
            and all(_number(kinds.get(kind)) for kind in ('input', 'output', 'cacheRead', 'cacheWrite'))
            and rows_ok(data.get('byProject'), 'path') and rows_ok(data.get('byTool'), 'name')
            and (data.get('costUsd') is None or _number(data.get('costUsd')))
            and isinstance(data.get('unpricedModels'), list) and all(isinstance(model, str) for model in data['unpricedModels']))


def _spend(agent_id):
    data = _read_record(_directory().parent / 'breakdown' / (agent_id + '.json'))
    if not _valid_breakdown(data):
        return None
    kinds = data['byKind']
    return {'cost': money_text(data.get('costUsd')),
            'kinds': [{'name': tr('Entrada'), 'tokens': tokens_text(kinds['input'])},
                      {'name': tr('Salida'), 'tokens': tokens_text(kinds['output'])},
                      {'name': tr('Caché leída'), 'tokens': tokens_text(kinds['cacheRead'])},
                      {'name': tr('Caché escrita'), 'tokens': tokens_text(kinds['cacheWrite'])}],
            'projects': [{'name': short_path(row['path'])[:60], 'tokens': tokens_text(row['total']), 'cost': money_text(row.get('costUsd'))}
                         for row in data['byProject'][:5]],
            'tools': [{'name': _label(row['name'])[:60], 'tokens': tokens_text(row['total']), 'cost': money_text(row.get('costUsd'))}
                      for row in data['byTool'][:5]],
            'unpriced': ', '.join(data['unpricedModels'])[:120]}


def rows(agents):
    """One row per selected account with a readable record: bar percentages plus the detail shown on click."""
    result = []
    for agent in agents:
        agent_id = agent.get('id')
        if agent_id not in SHORT_NAMES or not agent.get('enabled'):
            continue
        record = _read_record(_directory() / (agent_id + '.json'))
        limits = record.get('limits') if record else None
        if not isinstance(limits, list):
            continue
        five = _window(limits, r'^(Session \(5|5h)')
        weekly = _window(limits, r'^Weekly \(7-day\)$')
        if five is None and weekly is None:
            continue
        tier = record.get('tierLabel')
        result.append({'id': agent_id, 'name': SHORT_NAMES[agent_id], 'tier': tier[:30] if isinstance(tier, str) else '',
                       'five': five, 'weekly': weekly, 'windows': _windows(limits), 'models': _models(record),
                       'spend': _spend(agent_id)})
    return result
