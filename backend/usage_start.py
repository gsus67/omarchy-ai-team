"""The one persisted instant from which the Chief and the workers are measured, and the worker ledger reader.

The start is created once (from now, never from older ledger rows) and never recomputed, moved or
repaired: a corrupt, linked or unreadable file is an error, not a reason to invent another start.
"""

import contextlib
import errno
import json
import os
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKERS = ('plan', 'code', 'review', 'simple')
MAX_START_BYTES = 4096
WINDOW_DAYS = (7, 30)
DEFAULT_WINDOW_DAYS = 7


class UsageStartError(Exception):
    """The start is unreadable, unsafe or invalid (different from a missing file); carries no text, only its cause."""


@dataclass(frozen=True)
class UsageEntry:
    at: datetime
    role: str
    agent: str
    tokens: int
    cache: int = 0


def default_state_dir():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'omarchy-ai-team'


def _utc(value):
    """An ISO 8601 string with a zone as an aware UTC datetime; ValueError/TypeError otherwise."""
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if moment.tzinfo is None:
        raise ValueError
    return moment.astimezone(timezone.utc)


def _entry(line):
    try:
        row = json.loads(line)
        role, agent, tokens, cache = row['role'], row['agent'], row['tokens'], row.get('cache', 0)
        moment = _utc(row['at'])
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        return None
    if (role in WORKERS and isinstance(agent, str) and agent and type(tokens) is int and 0 <= tokens < 10 ** 15
            and type(cache) is int and 0 <= cache < 10 ** 15):
        return UsageEntry(moment, role, agent, tokens, cache)
    return None


def iter_ledger_entries(path, *, dir_fd=None):
    """Every valid entry of the whole ledger, streamed; a missing file is empty, an access error or a
    non-regular object (directory, FIFO, socket) is an OSError, so it can never pass for an empty ledger.

    With ``dir_fd`` the ledger is resolved inside that already validated directory; the caller owns it."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=dir_fd)  # a FIFO must never block the panel
    except FileNotFoundError:
        return
    try:  # until fdopen succeeds the raw descriptor is ours to close
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))
        handle = os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise
    with handle:  # owns the descriptor now: closed at EOF, on a read error or when the generator closes
        for line in handle:
            entry = _entry(line.decode('utf-8', errors='replace'))
            if entry:
                yield entry


START_NAME = 'usage-start.json'


def _private(info):
    """Owned by this user and without any group/other or special bit: nobody else may have written or read it."""
    return info.st_uid == os.geteuid() and not info.st_mode & 0o7077


def _open_state_dir(folder, *, create):
    """A validated descriptor of the state directory (the caller closes it); nothing is repaired or widened.

    Opened with O_NOFOLLOW and checked with fstat (a directory of this user without group/other/special bits),
    so a symlinked, foreign or public directory is a UsageStartError and later operations use the descriptor,
    never the path. A missing directory is created 0700 only when ``create``; otherwise FileNotFoundError.
    """
    folder = Path(folder)
    if create:
        folder.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(folder, 0o700)
        except FileExistsError:
            pass
    try:
        descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise UsageStartError from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or not _private(info):
            raise UsageStartError
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _read_start(directory):
    """The persisted start in the validated ``directory`` descriptor, None if it does not exist;
    anything unsafe (links, special files, public or foreign modes) or invalid raises UsageStartError."""
    try:
        descriptor = os.open(START_NAME, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise UsageStartError from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not _private(info):
            raise UsageStartError
        data = os.read(descriptor, MAX_START_BYTES + 1)
    except OSError as error:
        raise UsageStartError from error
    finally:
        os.close(descriptor)
    try:
        record = json.loads(data) if len(data) <= MAX_START_BYTES else None
        if not isinstance(record, dict) or record.get('schemaVersion') != 1:
            raise ValueError
        return _utc(record['since'])
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError) as error:
        raise UsageStartError from error


def _publish(directory, moment, *, replace=False):
    """Write the start to a private temporary and link it into place; it never replaces an existing file
    unless ``replace``, which swaps it atomically and syncs the directory entry."""
    temporary = '.usage-start.%s' % secrets.token_hex(8)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            handle.write(json.dumps({'schemaVersion': 1, 'since': moment.isoformat()}) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, START_NAME, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
            return
        try:
            os.link(temporary, START_NAME, src_dir_fd=directory, dst_dir_fd=directory)
        except FileExistsError:
            pass  # another writer won: its value is read below
        except OSError as error:
            raise UsageStartError from error
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=directory)


def get_or_create_start(*, now=None, state_dir=None):
    """The persisted start as an aware UTC datetime; created once from ``now``."""
    directory = _open_state_dir(Path(state_dir) if state_dir else default_state_dir(), create=True)
    try:
        existing = _read_start(directory)
        if existing:
            return existing
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        _publish(directory, moment)
        published = _read_start(directory)
        if published is None:
            raise UsageStartError
        return published
    finally:
        os.close(directory)


def reset_start(*, now=None, state_dir=None):
    """Replace the persisted start with ``now`` (aware UTC); an unsafe or invalid one is not repaired."""
    if now is not None and (not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None):
        raise ValueError(now)  # only None (the clock) or an aware datetime; never a falsy or naive stand-in
    directory = _open_state_dir(Path(state_dir) if state_dir else default_state_dir(), create=False)
    try:
        _read_start(directory)  # raises on anything unsafe or invalid; a missing start is simply written
        moment = (now if now is not None else datetime.now(timezone.utc)).astimezone(timezone.utc)
        _publish(directory, moment, replace=True)
        return moment
    finally:
        os.close(directory)


def window_days(value):
    """The selected rolling window in days: exactly the int 7 or 30 (no bool, float or text); ValueError otherwise."""
    if type(value) is not int or value not in WINDOW_DAYS:
        raise ValueError(value)
    return value


def effective_since(start, as_of, days):
    """The cut of the rolling window, ``max(start, as_of - days)``, as an aware UTC instant.

    ValueError when the inputs are naive, the window is not 7/30, the start is after ``as_of`` (a clock gone back:
    the start is kept, the interval is unknown) or the arithmetic is not representable."""
    days = window_days(days)
    if start.tzinfo is None or as_of.tzinfo is None:
        raise ValueError
    try:
        start, as_of = start.astimezone(timezone.utc), as_of.astimezone(timezone.utc)
        if start > as_of:
            raise ValueError
        return max(start, as_of - timedelta(days=days))
    except OverflowError as error:
        raise ValueError from error


def managed_engines(since, until, *, path=None):
    """Engines (the agent id before its first dash, so claude-2 is claude) with managed worker runs in the closed
    ``[since, until]``. A missing ledger is empty; an unreadable one raises OSError (the caller treats it as every engine)."""
    path = path or default_state_dir() / 'role-usage.jsonl'
    return frozenset(entry.agent.split('-')[0] for entry in iter_ledger_entries(path) if since <= entry.at <= until)
