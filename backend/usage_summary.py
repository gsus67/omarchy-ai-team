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


def _percent(limit):
    value = limit.get('percent')
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        return None
    reset = limit.get('resetsAt')
    if isinstance(reset, str):
        try:
            moment = datetime.fromisoformat(reset.replace('Z', '+00:00'))
        except ValueError:
            moment = None
        if moment is not None and (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)) <= datetime.now(timezone.utc):
            return 0  # the window already reset; the record just has not been rewritten yet
    return round(value * 100)


def _window(limits, pattern):
    for limit in limits:
        if isinstance(limit, dict) and isinstance(limit.get('label'), str) and re.search(pattern, limit['label']):
            return _percent(limit)
    return None


def rows(agents):
    """[{id, name, five, weekly}] for selected accounts that have a readable record with usable windows."""
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
        result.append({'id': agent_id, 'name': SHORT_NAMES[agent_id], 'five': five, 'weekly': weekly})
    return result
