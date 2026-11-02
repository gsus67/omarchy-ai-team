#!/usr/bin/python3
"""Write the usage records of the second Claude and Codex accounts.

The official agents panel draws every record found in
~/.local/state/omarchy/agents/usage/. Its collectors only read the main
accounts, so this runs the very same collectors against ~/.claude-2 and
~/.codex-2 and stores the result under the ids claude-2 and codex-2.
Sessions of pi and opencode stay with the main accounts: the collectors'
pi/opencode scans are switched off here so nothing is counted twice.

It also writes, for the four accounts, a token breakdown (by kind, project and tool,
with an estimated API cost) to ~/.local/state/omarchy/agents/breakdown/.
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import breakdown
import prices

ACCOUNTS = {
    'claude-2': {'collector': 'omarchy-agent-usage-claude', 'name': 'Claude Code · 2', 'variable': 'CLAUDE_CONFIG_DIR',
                 'home': '.claude-2', 'silenced': {'scan_pi_usage': lambda *_: None, 'scan_opencode_usage': lambda *_: None}},
    'codex-2': {'collector': 'omarchy-agent-usage-codex', 'name': 'Codex · 2', 'variable': 'CODEX_HOME',
                'home': '.codex-2', 'silenced': {'scan_pi_sessions': lambda *_: None, 'scan_opencode_sessions': lambda *_: True}},
}

# Every account whose transcripts get a token breakdown: id -> (scanner, profile folder under HOME).
BREAKDOWN_ACCOUNTS = {
    'claude': ('claude', '.claude'), 'claude-2': ('claude', '.claude-2'),
    'codex': ('codex', '.codex'), 'codex-2': ('codex', '.codex-2'),
}
BREAKDOWN_DAYS = 30
MAX_SETTINGS_BYTES = 1 << 20


def usage_start():
    """The shared start module of the plugin (``backend/usage_start.py``), loaded by path like the price table."""
    path = Path(__file__).resolve().parents[2] / 'backend/usage_start.py'
    spec = importlib.util.spec_from_file_location('ai_team_usage_start', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def state_dir():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'omarchy/agents/usage'


def collector_path(account):
    return Path(os.environ.get('OMARCHY_PATH') or '/usr/share/omarchy') / 'bin' / ACCOUNTS[account]['collector']


def collect(account, flags=()):
    """Run the official collector for one account and return its record, or None."""
    spec = ACCOUNTS[account]
    home = Path.home() / spec['home']
    if not home.is_dir():
        return None
    cache = Path.home() / '.cache/omarchy-ai-team/usage' / account
    cache.mkdir(parents=True, exist_ok=True)
    for private in (cache, cache.parent):  # the collectors write world-readable files: the directory keeps them private
        private.chmod(0o700)
    previous = {name: os.environ.get(name) for name in (spec['variable'], 'XDG_CACHE_HOME')}
    previous_argv = sys.argv
    os.environ[spec['variable']] = str(home)
    os.environ['XDG_CACHE_HOME'] = str(cache)  # limits and scan caches must never mix with the main account
    sys.argv = [spec['collector'], *flags]
    try:
        loader = importlib.machinery.SourceFileLoader('usage_' + account.replace('-', '_'), str(collector_path(account)))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        for name, replacement in spec['silenced'].items():
            if not hasattr(module, name):
                raise RuntimeError('collector changed: ' + name + ' is gone')
            setattr(module, name, replacement)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            module.main()
        record = json.loads(output.getvalue())
    finally:
        sys.argv = previous_argv
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    record['id'] = account
    record['name'] = spec['name']
    return record


def write(account, record, directory):
    """Atomic write; with no record (profile missing) a stale file is removed."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (account + '.json')
    if record is None:
        target.unlink(missing_ok=True)
        return
    descriptor, temporary = tempfile.mkstemp(prefix='.' + account + '.', dir=directory)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            handle.write(json.dumps(record, separators=(',', ':'), sort_keys=True) + '\n')
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def breakdown_dir():
    return state_dir().parent / 'breakdown'


def prices_path():
    return Path.home() / '.config/omarchy/agents/prices.json'


def window_days_setting():
    """The selected measurement window (7 or 30 days) from the plugin settings: 7 when the file or key is absent,
    None when the file or the value is not valid (no measurement is published then). Read-only, strict."""
    path = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'omarchy-ai-team/settings.json'
    module = usage_start()
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return module.DEFAULT_WINDOW_DAYS
    except OSError:
        return None
    try:
        with os.fdopen(descriptor, 'rb') as handle:
            data = json.loads(handle.read(MAX_SETTINGS_BYTES + 1)[:MAX_SETTINGS_BYTES])
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    if 'usageWindowDays' not in data:
        return module.DEFAULT_WINDOW_DAYS
    try:
        return module.window_days(data['usageWindowDays'])
    except ValueError:
        return None


def write_breakdowns(now=None, *, window_days=7):
    """Write where each account's tokens went (and what they would cost); remove stale files.

    Returns the ids whose scan failed; one broken account never hides the others. One capture (start S,
    window N, instant T) is shared by the four scans; if S cannot be resolved nothing is scanned. Before
    publishing, S and N are read again: if either changed the attempt is discarded and nothing is written.
    ``window_days`` None (an invalid preference) publishes the detail without a measurement.
    """
    now = now or datetime.now(timezone.utc)
    module = usage_start()
    try:
        since = module.get_or_create_start(now=now)
    except Exception as error:  # an invalid start is never replaced: the previous snapshots stay as they are
        print('ai-team-usage: usage start failed: ' + type(error).__name__, file=sys.stderr)
        return list(BREAKDOWN_ACCOUNTS)
    engines, measured = frozenset(), window_days is not None
    if measured:
        try:
            cut = module.effective_since(since, now, window_days)
        except ValueError:  # a start after T, or unrepresentable arithmetic: unknown, the start is kept
            measured = False
        else:
            try:
                engines = module.managed_engines(cut, now)
            except OSError:
                engines = frozenset(('claude', 'codex'))  # an unreadable ledger: every engine counts as managed
    table = prices.load_prices(prices_path())
    folder = breakdown_dir()
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    failed, bodies = [], {}
    options = {'since': since if measured else None, 'total_days': window_days or module.DEFAULT_WINDOW_DAYS}
    for account, (kind, profile) in BREAKDOWN_ACCOUNTS.items():
        try:
            root = Path.home() / profile
            managed = kind in engines
            if kind == 'claude':
                body = breakdown.scan_claude(root / 'projects', table, BREAKDOWN_DAYS, now, managed_runs=managed, **options)
            else:
                body = breakdown.scan_codex(root / 'sessions', table, BREAKDOWN_DAYS, now, extra_dirs=(root / 'archived_sessions',),
                                            managed_runs=managed, **options)
            bodies[account] = None if body is None else {'schemaVersion': 1, 'id': account, 'generatedAt': now.isoformat(), **body}
        except Exception as error:
            print('ai-team-usage: breakdown ' + account + ' failed: ' + type(error).__name__, file=sys.stderr)
            failed.append(account)
    try:
        unchanged = module.get_or_create_start(now=now) == since and window_days_setting() == window_days
    except Exception:
        unchanged = False
    if not unchanged:  # the capture is stale: publishing it would mix two generations
        print('ai-team-usage: usage start or window changed; the attempt was discarded', file=sys.stderr)
        return list(BREAKDOWN_ACCOUNTS)
    for account, body in bodies.items():
        try:
            write(account, body, folder)
        except Exception as error:
            print('ai-team-usage: breakdown ' + account + ' failed: ' + type(error).__name__, file=sys.stderr)
            failed.append(account)
    return failed


def main(argv=None):
    arguments = argv if argv is not None else sys.argv[1:]
    flags = [flag for flag in arguments if flag in ('--force', '--limits-only')]
    status = 0
    for account in () if '--breakdowns-only' in arguments else ACCOUNTS:  # --breakdowns-only never runs the official collectors
        try:
            write(account, collect(account, flags), state_dir())
        except Exception as error:  # one broken account must not hide the other
            print('ai-team-usage: ' + account + ' failed: ' + type(error).__name__, file=sys.stderr)
            status = 1
    if write_breakdowns(window_days=window_days_setting()):
        status = 1
    return status


if __name__ == '__main__':
    raise SystemExit(main())
