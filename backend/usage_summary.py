"""Five-hour and weekly usage of each selected account, for the main screen.

Reads the records the Omarchy agents panel already maintains in
~/.local/state/omarchy/agents/usage/ (read-only; nothing is collected here).
"""

import json
import os
import re
from pathlib import Path

# Only these accounts have limit windows; the short names fit a 380 px screen.
SHORT_NAMES = {'claude': 'Claude', 'claude-2': 'Claude 2', 'codex': 'Codex', 'codex-2': 'Codex 2'}
MAX_BYTES = 1024 * 1024


def _directory():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'omarchy/agents/usage'


def _percent(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 10:
        return None
    return round(value * 100)


def _window(limits, pattern):
    for limit in limits:
        if isinstance(limit, dict) and isinstance(limit.get('label'), str) and re.search(pattern, limit['label']):
            return _percent(limit.get('percent'))
    return None


def rows(agents):
    """[{id, name, five, weekly}] for selected accounts that have a readable record with usable windows."""
    result = []
    for agent in agents:
        agent_id = agent.get('id')
        if agent_id not in SHORT_NAMES or not agent.get('enabled'):
            continue
        path = _directory() / (agent_id + '.json')
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            limits = json.loads(path.read_text()).get('limits')
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(limits, list):
            continue
        five = _window(limits, r'^(Session \(5|5h)')
        weekly = _window(limits, r'^Weekly \(7-day\)$')
        if five is None and weekly is None:
            continue
        result.append({'id': agent_id, 'name': SHORT_NAMES[agent_id], 'five': five, 'weekly': weekly})
    return result
