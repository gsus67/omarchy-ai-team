"""Tokens spent by each team role, for the main screen's Total page.

Every worker run (context_run.py) appends its counted tokens to a small private ledger. The Chief
has no run of its own: it is the rest of the selected accounts' total over the same window
(the breakdown the usage writer keeps), so it can include older work from before the ledger started.
"""

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import atomic_files
import usage_summary
from i18n import tr

DAYS = 30
ROLES = ('jefe', 'plan', 'code', 'review', 'simple')
WORKERS = ROLES[1:]
PRUNE_AT = 256 * 1024
MAX_READ = 1024 * 1024


def ledger():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'omarchy-ai-team' / 'role-usage.jsonl'


def _now():
    return datetime.now(timezone.utc)


def _entries(path=None):
    """Valid entries of the last DAYS days; anything else in the file is ignored."""
    path = path or ledger()
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except OSError:
        return []
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return []
        data = os.read(descriptor, MAX_READ)
    except OSError:
        return []
    finally:
        os.close(descriptor)
    cutoff = _now() - timedelta(days=DAYS)
    result = []
    for line in data.decode('utf-8', errors='replace').splitlines():
        try:
            entry = json.loads(line)
            moment = datetime.fromisoformat(entry['at'])
        except (ValueError, TypeError, KeyError):
            continue
        tokens = entry.get('tokens') if isinstance(entry, dict) else None
        if (entry['role'] in WORKERS and type(tokens) is int and 0 <= tokens < 10 ** 15
                and moment.tzinfo and moment >= cutoff):
            result.append({'at': entry['at'], 'role': entry['role'], 'tokens': tokens})
    return result


def record(role, counters):
    """Append the tokens of one worker run; nothing is written when the counters are unknown."""
    inputs, output = counters.get('total_input'), counters.get('output')
    if role not in WORKERS or type(inputs) is not int or type(output) is not int:
        return
    path = ledger()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = json.dumps({'at': _now().isoformat(), 'role': role, 'tokens': inputs + output}) + '\n'
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.write(descriptor, line.encode())
        size = os.fstat(descriptor).st_size
    finally:
        os.close(descriptor)
    if size > PRUNE_AT:
        atomic_files.atomic_write(path, ''.join(json.dumps(entry) + '\n' for entry in _entries(path)))


def summary(agents):
    """``{since, total, roles: [{role, name, tokens, share}]}`` or None when no account has a breakdown."""
    total = usage_summary.total_tokens(agents)
    if total is None:
        return None
    entries = _entries()
    spent = dict.fromkeys(WORKERS, 0)
    for entry in entries:
        spent[entry['role']] += entry['tokens']
    chief = max(0, total - sum(spent.values()))
    names = {'jefe': tr('Jefe'), 'plan': tr('Planificar'), 'code': tr('Programar'), 'review': tr('Revisar'), 'simple': tr('Sencillas')}
    amounts = {'jefe': chief, **spent}
    return {'since': min((entry['at'] for entry in entries), default='')[:10], 'total': usage_summary.tokens_text(total),
            'roles': [{'role': role, 'name': names[role], 'tokens': usage_summary.tokens_text(amounts[role]),
                       'share': round(amounts[role] / total, 4) if total else 0} for role in ROLES]}
