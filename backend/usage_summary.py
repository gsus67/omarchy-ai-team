"""Five-hour and weekly usage of each selected account, for the main screen.

Reads the records the Omarchy agents panel already maintains in
~/.local/state/omarchy/agents/usage/ (read-only; nothing is collected here).
Muse Code has no such record, so its two windows come from `muse usage --json`.
"""

import json
import os
import re
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import usage_start
from i18n import tr

# Only these accounts have limit windows; the short names fit a 380 px screen.
SHORT_NAMES = {'claude': 'Claude', 'claude-2': 'Claude 2', 'codex': 'Codex', 'codex-2': 'Codex 2'}
MAX_BYTES = 1024 * 1024
MUSE_TTL = 60  # seconds a `muse usage` answer is reused, so refreshes do not start the binary each time
# The only counters that are usage; a record may also carry capacity metadata such as maxOutputTokens.
USAGE_BUCKETS = ('inputTokens', 'outputTokens', 'cacheReadInputTokens', 'cacheCreationInputTokens')


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
    """A usable count or cost: finite and far below anything that could overflow a format."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) < 1e15  # False for nan, inf and huge ints without converting them


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
            total = sum(bucket[key] for key in USAGE_BUCKETS if _number(bucket.get(key)))
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


MUSE_DAYS = 30
MUSE_FIELDS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens')
MUSE_MAX_FILES, MUSE_MAX_BYTES = 5000, 64 * 1024 * 1024


def _muse_session_tokens(path, since):
    """Tokens of the ``model_completed`` events of one session file recorded since ``since`` (microseconds), or None."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)  # never follow links or wait on a pipe
    except OSError:
        return None
    total = None
    with os.fdopen(descriptor, 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None
        for line in handle.read(MUSE_MAX_BYTES).splitlines():
            try:
                record = json.loads(line)
                event = record['payload']['event']
                recorded, usage = record['recorded_at'], event['usage']
                if event['kind'] != 'model_completed' or type(recorded) is not int or recorded < since or not isinstance(usage, dict):
                    continue
            except (ValueError, KeyError, TypeError, RecursionError):
                continue
            # Each counter counts only as an exact non-negative int; anything else adds nothing.
            total = (total or 0) + sum(value for value in (usage.get(field) for field in MUSE_FIELDS)
                                       if type(value) is int and 0 <= value < 10 ** 15)
    return total


def _muse_tokens():
    """Tokens of every Muse session (subagents included) of the last MUSE_DAYS days, or None without any."""
    base = os.environ.get('XDG_DATA_HOME') or ''
    root = (Path(base) if os.path.isabs(base) else Path.home() / '.local/share') / 'muse' / 'sessions'  # XDG: relative is ignored
    since = int((time.time() - MUSE_DAYS * 86400) * 1_000_000)
    total, seen = None, 0
    for folder, _, names in os.walk(root):  # os.walk does not follow directory links
        if 'session.jsonl' not in names:
            continue
        seen += 1
        if seen > MUSE_MAX_FILES:
            break
        tokens = _muse_session_tokens(os.path.join(folder, 'session.jsonl'), since)
        if tokens is not None:
            total = (total or 0) + tokens
    return total


def total_tokens(agents):
    """Tokens of every kind over the breakdown window, summed across the selected accounts (None without any record)."""
    total = None
    for agent in agents:
        if agent.get('id') == 'muse' and agent.get('enabled'):
            tokens = _muse_tokens()
            if tokens is not None:
                total = (total or 0) + tokens
            continue
        if agent.get('id') not in SHORT_NAMES or not agent.get('enabled'):
            continue
        data = _read_record(_directory().parent / 'breakdown' / (agent['id'] + '.json'))
        if _valid_breakdown(data):
            total = (total or 0) + sum(data['byKind'][kind] for kind in ('input', 'output', 'cacheRead', 'cacheWrite'))
    return total


def _instant(value):
    """An ISO string with a zone as an aware UTC datetime, or None."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return moment.astimezone(timezone.utc) if moment.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _counters(block):
    """total_input + output of a ``{total_input, output}`` block of exact ints in [0, 10**15), else None."""
    if not isinstance(block, dict):
        return None
    values = (block.get('total_input'), block.get('output'))
    if any(type(value) is not int or not 0 <= value < 10 ** 15 for value in values):
        return None
    return sum(values)


def _measurement_block(data):
    """``(start, since, window, generated, jefe, otros, limited)`` of a snapshot's ``measurement`` block, or None."""
    block = data.get('measurement') if isinstance(data, dict) and data.get('schemaVersion') == 1 else None
    if not isinstance(block, dict) or type(block.get('classificationLimited')) is not bool:
        return None
    start, since, generated = _instant(block.get('start')), _instant(block.get('since')), _instant(data.get('generatedAt'))
    jefe, otros = _counters(block.get('jefe')), _counters(block.get('otros'))
    if None in (start, since, generated, jefe, otros):
        return None
    return start, since, block.get('windowDays'), generated, jefe, otros, block['classificationLimited']


def measurement(agents, *, start, window_days):
    """``{since, asOf, jefe, otros, limited}`` of the selected accounts' rolling window, or None.

    None unless every selected account published a block measured from the same ``start`` and ``window_days``,
    cut exactly at ``effective_since(start, T, window_days)`` and generated at the same instant T, with valid
    counters: a mixed generation or another window is unknown, never a partial or a stale sum."""
    try:
        start, window = start.astimezone(timezone.utc), usage_start.window_days(window_days)
    except (ValueError, AttributeError, OverflowError):
        return None
    found = []
    for agent in agents:
        if agent.get('id') not in SHORT_NAMES or not agent.get('enabled'):
            continue
        parsed = _measurement_block(_read_record(_directory().parent / 'breakdown' / (agent['id'] + '.json')))
        if parsed is None:
            return None
        found.append(parsed)
    if not found or any(item[0] != start or item[2] != window or item[3] != found[0][3] or item[1] != found[0][1] for item in found):
        return None
    as_of = found[0][3]
    try:
        expected = usage_start.effective_since(start, as_of, window)
    except ValueError:
        return None
    if found[0][1] != expected:
        return None
    return {'since': expected, 'asOf': as_of, 'jefe': sum(item[4] for item in found), 'otros': sum(item[5] for item in found),
            'limited': any(item[6] for item in found)}


def _muse_percent(window):
    value = window.get('usedPercent') if isinstance(window, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        return None
    return value / 100


def _muse_limit(label, window):
    percent = _muse_percent(window)
    if percent is None:
        return None
    limit = {'label': label, 'percent': percent}
    reset = window.get('resetsAtMs')
    if isinstance(reset, int) and not isinstance(reset, bool) and 0 < reset < 4e12:
        limit['resetsAt'] = datetime.fromtimestamp(reset / 1000, timezone.utc).isoformat()
    return limit


def _muse_cache_file():
    return Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'omarchy-ai-team' / 'muse-usage.json'


def _muse_fetch(binary):
    """Raw `muse usage --json` output: no shell, own process group, short timeout, bounded size."""
    proc = subprocess.Popen([binary, 'usage', '--json'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,  # noqa: S603 - fixed argv, no shell
                            stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, 9)
        proc.communicate()
        return None
    return out[:MAX_BYTES].decode('utf-8', 'replace') if proc.returncode == 0 else None


def _muse_record(binary):
    """`muse usage --json` shaped like a usage record (or None); reused for MUSE_TTL seconds through a cache file,
    because every panel refresh is a new process."""
    path = _muse_cache_file()
    try:
        if time.time() - path.stat().st_mtime < MUSE_TTL:
            cached = _read_record(path)
            if cached and isinstance(cached.get('limits'), list):
                return cached
    except OSError:
        pass
    record = None
    try:
        data = json.loads(_muse_fetch(binary) or 'null')
        allowance = data.get('allowance') if isinstance(data, dict) else None
        if isinstance(allowance, dict):
            limits = [item for item in (_muse_limit('5h', allowance.get('window')),
                                        _muse_limit('Weekly (7-day)', allowance.get('weekly'))) if item]
            tier = allowance.get('tier')
            record = {'limits': limits, 'tierLabel': tier if isinstance(tier, str) else ''}
    except (OSError, ValueError, subprocess.SubprocessError, RecursionError):
        record = None
    if record and record['limits']:
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps(record))
            os.replace(tmp, path)
        except OSError:
            pass
    return record


def rows(agents):
    """One row per selected account with a readable record: bar percentages plus the detail shown on click."""
    result = []
    for agent in agents:
        agent_id = agent.get('id')
        if agent_id == 'muse' and agent.get('enabled'):
            binary = agent.get('binary')
            if agent.get('installation') != 'installed' or not isinstance(binary, str) or not os.path.isabs(binary) or not os.access(binary, os.X_OK):
                continue
            record = _muse_record(binary) or {'limits': [], 'tierLabel': ''}
            result.append({'id': 'muse', 'name': 'Muse', 'tier': record['tierLabel'][:30],
                           'five': _window(record['limits'], r'^5h'), 'weekly': _window(record['limits'], r'^Weekly \(7-day\)$'),
                           'windows': _windows(record['limits']), 'models': [], 'spend': None})
            continue
        if agent_id not in SHORT_NAMES or not agent.get('enabled'):
            continue
        record = _read_record(_directory() / (agent_id + '.json'))
        limits = record.get('limits') if record else None
        if not isinstance(limits, list):
            continue
        five = _window(limits, r'^(Session \(5|5h)')
        weekly = _window(limits, r'^Weekly \(7-day\)$')
        # An account whose limits could not be read this time stays listed with empty bars ("—"): vanishing looked like a removed account.
        tier = record.get('tierLabel')
        result.append({'id': agent_id, 'name': SHORT_NAMES[agent_id], 'tier': tier[:30] if isinstance(tier, str) else '',
                       'five': five, 'weekly': weekly, 'windows': _windows(limits), 'models': _models(record),
                       'spend': _spend(agent_id)})
    return result
