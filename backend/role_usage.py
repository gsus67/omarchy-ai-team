"""Tokens spent by each team role since the shared measurement start, for the main screen's Total page.

Every worker run (context_run.py) appends its counted tokens to a small private ledger. Worker runs
are ephemeral (no session file), so the Chief's consumption is what the usage writer measured from the
session transcripts since the same persisted start (usage_start.py). Everything is in one unit
(new input + output); cache is reported separately and old ledger entries remain readable.
"""

import errno
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import usage_start
import usage_summary
from i18n import tr

ROLES = ('jefe', 'plan', 'code', 'review', 'simple')
ACCOUNT_ROWS = ROLES + ('muse', 'otros')  # Accounting rows have no settings, model, recipe or ledger entry
WORKERS = usage_start.WORKERS


def ledger():
    return usage_start.default_state_dir() / 'role-usage.jsonl'


def _now():
    return datetime.now(timezone.utc)


def _entries(agents, *, since, until, directory):
    """Valid ledger entries of the selected accounts in the closed interval [``since``, ``until``], streamed from
    the whole file of the already validated state ``directory`` (a descriptor the caller keeps open until consumed)."""
    selected = {agent.get('id') for agent in agents if agent.get('enabled')}
    return (entry for entry in usage_start.iter_ledger_entries(ledger().name, dir_fd=directory)
            if entry.agent in selected and since <= entry.at <= until)


def record(role, agent, counters):
    """Append the tokens of one worker run; nothing is written when the counters are unknown."""
    inputs, output = counters.get('total_input'), counters.get('output')
    new_input, read, created = counters.get('new_input'), counters.get('cached_read'), counters.get('cache_creation')
    valid = lambda value: type(value) is int and 0 <= value < 10 ** 15
    if role not in WORKERS or not valid(inputs) or not valid(output):
        return
    detailed = any(key in counters for key in ('new_input', 'cached_read', 'cache_creation'))
    if detailed and (not valid(new_input) or not valid(read) or (created is not None and not valid(created))):
        return
    if detailed:
        tokens, cache = new_input + output, read + (created or 0)
    else:  # Entries from older callers keep their historical meaning; the file is never migrated.
        tokens, cache = inputs + output, 0
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
        line = json.dumps({'at': _now().isoformat(), 'role': role, 'agent': agent,
                           'tokens': tokens, 'cache': cache}) + '\n'
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


def summary(agents, *, window_days=7):
    """``{since, start, asOf, windowDays, partialWindow, classificationLimited, pending, total, roles}``; None only
    when no supported account is selected.

    One rolling window for every row: ``since`` is E = max(start, T - windowDays), ``asOf`` is T, the instant the
    usage writer published for all selected accounts (the live clock is never read), and the workers are the ledger
    entries in [E, T]. Without a coherent snapshot of that start and window nothing is measured: every figure is None
    (``pending``), never a partial sum. ``total``, ``tokens`` and ``share`` are also None when the ledger is
    unreadable. Only reads: it never creates the start nor the state directory.
    """
    native = [agent for agent in agents if agent.get('id') in usage_summary.SHORT_NAMES and agent.get('enabled')]
    muse_enabled = any(agent.get('id') == 'muse' and agent.get('enabled') for agent in agents)
    if not native and not muse_enabled:
        return None
    directory, start, measured = None, None, None
    amounts = dict.fromkeys(ACCOUNT_ROWS)
    caches = dict.fromkeys(ACCOUNT_ROWS)
    if not muse_enabled:
        amounts['muse'] = caches['muse'] = 0
    full_measurement = None
    muse_missing = False
    try:  # read-only: a missing start stays unknown, only the writers (record, snapshots) create it
        try:
            directory = usage_start._open_state_dir(usage_start.default_state_dir(), create=False)
            start = usage_start._read_start(directory)
        except (usage_start.UsageStartError, OSError):
            start = None
        if start is not None and native:
            full_measurement = usage_summary.measurement(agents, start=start, window_days=window_days)
            measured = full_measurement or usage_summary.partial_measurement(agents, start=start, window_days=window_days)
        if measured is not None:
            amounts['jefe'], amounts['otros'] = measured['jefe'], measured['otros']
            caches['jefe'], caches['otros'] = measured['jefeCache'], measured['otrosCache']
            try:
                workers = dict.fromkeys(WORKERS, 0)
                worker_caches = dict.fromkeys(WORKERS, 0)
                measured_agents = [agent for agent in agents if agent.get('id') in measured['agents']]
                for entry in _entries(measured_agents, since=measured['since'], until=measured['asOf'], directory=directory):
                    workers[entry.role] += entry.tokens
                    worker_caches[entry.role] += entry.cache
                amounts.update(workers)
                caches.update(worker_caches)
            except OSError:
                pass
        if muse_enabled:
            as_of = measured['asOf'] if measured else _now()
            earliest = as_of - timedelta(days=window_days)
            since = measured['since'] if measured else max(start, earliest) if start else earliest
            muse = usage_summary._muse_usage(window_days, since=since, until=as_of)
            if muse is not None:
                amounts['muse'], caches['muse'] = muse
                if not native:
                    for role in (*ROLES, 'otros'):
                        amounts[role] = caches[role] = 0
            elif measured is not None:
                # Missing Muse data does not hide known accounts: the total is explicitly partial.
                muse_missing = True
                amounts['muse'] = caches['muse'] = 0
            else:
                muse_missing = True
    finally:
        if directory is not None:
            os.close(directory)
    total = None if None in amounts.values() else sum(amounts.values())
    cache = None if None in caches.values() else sum(caches.values())
    names = {'jefe': tr('Jefe'), 'plan': tr('Planificar'), 'code': tr('Programar'), 'review': tr('Revisar'),
             'simple': tr('Sencillas'), 'muse': 'Muse', 'otros': tr('Otros')}
    pending = bool(native) and full_measurement is None or muse_missing
    return {'since': measured['since'].isoformat() if measured else None, 'start': start.isoformat() if start else None,
            'asOf': measured['asOf'].isoformat() if measured else None, 'windowDays': window_days,
            'partialWindow': bool(measured) and start > measured['asOf'] - timedelta(days=window_days),
            'classificationLimited': bool(measured) and measured['limited'], 'pending': pending,
            'partialTotal': total is not None and pending,
            'total': None if total is None else usage_summary.tokens_text(total),
            'cache': None if cache is None else usage_summary.tokens_text(cache),
            'roles': [{'role': role, 'name': names[role], 'share': _share(amounts[role], total),
                       'tokens': None if amounts[role] is None else usage_summary.tokens_text(amounts[role]),
                       'cache': None if caches[role] is None else usage_summary.tokens_text(caches[role])}
                      for role in ACCOUNT_ROWS if role != 'muse' or muse_enabled]}
