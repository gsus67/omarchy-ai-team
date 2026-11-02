"""Tokens spent by each team role, for the main screen's Total page.

Every worker run (context_run.py) appends its counted tokens to a small private ledger. Worker runs
are ephemeral (no session file), so the breakdown the usage writer keeps from the session transcripts
is the Chief's own consumption; the total is the Chief plus the workers.
"""

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import usage_summary
from i18n import tr

DAYS = 30
ROLES = ('jefe', 'plan', 'code', 'review', 'simple')
WORKERS = ROLES[1:]
MAX_READ = 1024 * 1024


def ledger():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'omarchy-ai-team' / 'role-usage.jsonl'


def _now():
    return datetime.now(timezone.utc)


def _entries(agents):
    """Valid entries of the last DAYS days from the selected accounts (the newest MAX_READ bytes); anything else is ignored."""
    selected = {agent.get('id') for agent in agents if agent.get('enabled')}
    try:
        descriptor = os.open(ledger(), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)  # a FIFO must never block the panel
    except OSError:
        return []
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return []
        os.lseek(descriptor, max(0, info.st_size - MAX_READ), os.SEEK_SET)
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
            role, tokens = entry['role'], entry['tokens']
        except (ValueError, TypeError, KeyError):
            continue
        if (role in WORKERS and entry.get('agent') in selected and type(tokens) is int and 0 <= tokens < 10 ** 15
                and moment.tzinfo and moment >= cutoff):
            result.append({'at': entry['at'], 'role': role, 'tokens': tokens})
    return result


def record(role, agent, counters):
    """Append the tokens of one worker run; nothing is written when the counters are unknown."""
    inputs, output = counters.get('total_input'), counters.get('output')
    if role not in WORKERS or type(inputs) is not int or type(output) is not int:
        return
    path = ledger()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    line = json.dumps({'at': _now().isoformat(), 'role': role, 'agent': agent, 'tokens': inputs + output}) + '\n'
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, line.encode())
    finally:
        os.close(descriptor)


def summary(agents):
    """``{since, total, roles: [{role, name, tokens, share}]}`` or None when no account has a breakdown."""
    chief = usage_summary.total_tokens(agents)
    if chief is None:
        return None
    entries = _entries(agents)
    amounts = {'jefe': chief, **dict.fromkeys(WORKERS, 0)}
    for entry in entries:
        amounts[entry['role']] += entry['tokens']
    total = sum(amounts.values())
    names = {'jefe': tr('Jefe'), 'plan': tr('Planificar'), 'code': tr('Programar'), 'review': tr('Revisar'), 'simple': tr('Sencillas')}
    return {'since': min((entry['at'] for entry in entries), default='')[:10], 'total': usage_summary.tokens_text(total),
            'roles': [{'role': role, 'name': names[role], 'tokens': usage_summary.tokens_text(amounts[role]),
                       'share': round(amounts[role] / total, 4) if total else 0} for role in ROLES]}
