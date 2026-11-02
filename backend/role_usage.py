"""Tokens spent by each team role since the shared measurement start, for the main screen's Total page.

Every worker run (context_run.py) appends its counted tokens to a small private ledger. Worker runs
are ephemeral (no session file), so the Chief's consumption is what the usage writer measured from the
session transcripts since the same persisted start (usage_start.py). Everything is in one unit
(total input + output); what cannot be measured is unknown (None), never zero.
"""

import errno
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import usage_start
import usage_summary
from i18n import tr

ROLES = ('jefe', 'plan', 'code', 'review', 'simple')
WORKERS = usage_start.WORKERS


def ledger():
    return usage_start.default_state_dir() / 'role-usage.jsonl'


def _now():
    return datetime.now(timezone.utc)


def _entries(agents, *, since, directory):
    """Valid ledger entries of the selected accounts at or after ``since``, streamed from the whole file
    of the already validated state ``directory`` (a descriptor the caller keeps open until it is consumed)."""
    selected = {agent.get('id') for agent in agents if agent.get('enabled')}
    return (entry for entry in usage_start.iter_ledger_entries(ledger().name, dir_fd=directory)
            if entry.agent in selected and entry.at >= since)


def record(role, agent, counters):
    """Append the tokens of one worker run; nothing is written when the counters are unknown."""
    inputs, output = counters.get('total_input'), counters.get('output')
    if role not in WORKERS or type(inputs) is not int or type(output) is not int:
        return
    try:
        usage_start.get_or_create_start()  # the start exists before the first managed entry; a failure never loses the run
    except (usage_start.UsageStartError, OSError):
        pass
    path = ledger()
    try:
        directory = usage_start._open_state_dir(path.parent, create=True)  # validated here too: a corrupt start must not excuse an unsafe folder
    except usage_start.UsageStartError as error:
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES)) from error
    try:
        line = json.dumps({'at': _now().isoformat(), 'role': role, 'agent': agent, 'tokens': inputs + output}) + '\n'
        descriptor = os.open(path.name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                return
            if info.st_mode & 0o077:
                os.fchmod(descriptor, stat.S_IMODE(info.st_mode) & 0o600)  # only ever narrows: group/other bits go, nothing is added
            os.write(descriptor, line.encode())
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def _share(amount, total):
    if amount is None or total is None:
        return None
    return round(amount / total, 4) if total else 0


def summary(agents):
    """``{since, total, roles: [{role, name, tokens, share}]}``; None only when no supported account is selected.

    ``since`` is the persisted start (ISO instant). ``total``, ``tokens`` and ``share`` are None when unknown:
    no (or no valid) start, an account without a block measured from that start, or an unreadable ledger.
    Only reads: it never creates the start nor the state directory.
    """
    if not any(agent.get('id') in usage_summary.SHORT_NAMES and agent.get('enabled') for agent in agents):
        return None
    directory, since = None, None
    amounts = dict.fromkeys(ROLES)
    try:  # read-only: a missing start stays unknown, only the writers (record, snapshots) create it
        try:
            directory = usage_start._open_state_dir(usage_start.default_state_dir(), create=False)
            since = usage_start._read_start(directory)
        except (usage_start.UsageStartError, OSError):
            since = None
        if since is not None:
            amounts['jefe'] = usage_summary.total_tokens_since(agents, since)
            try:
                workers = dict.fromkeys(WORKERS, 0)
                for entry in _entries(agents, since=since, directory=directory):
                    workers[entry.role] += entry.tokens
                amounts.update(workers)
            except OSError:
                pass
    finally:
        if directory is not None:
            os.close(directory)
    total = None if None in amounts.values() else sum(amounts.values())
    names = {'jefe': tr('Jefe'), 'plan': tr('Planificar'), 'code': tr('Programar'), 'review': tr('Revisar'), 'simple': tr('Sencillas')}
    return {'since': since.isoformat() if since else None, 'total': None if total is None else usage_summary.tokens_text(total),
            'roles': [{'role': role, 'name': names[role], 'share': _share(amounts[role], total),
                       'tokens': None if amounts[role] is None else usage_summary.tokens_text(amounts[role])} for role in ROLES]}
