#!/usr/bin/env python3
"""Claude Code and Codex hook: past the usage limit, tell the session to stop and leave a handoff. It must never break a session."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

CACHE_SECONDS = 60
USAGE_GUARD_MESSAGE = ('AI Team: {label} va por el {used} % y tu límite es {limit} %. Deja de avanzar en la tarea ahora y no empieces nada nuevo; '
           'lo que queda es para no perder el trabajo. Guarda con memory_save (kind handoff, en el proyecto de la tarea) un relevo detallado: '
           'qué pidió el usuario, qué quedó hecho y comprobado, qué falta, el siguiente paso exacto, los archivos y la rama, y lo que está a medias. '
           'Debe poder continuarlo otro modelo sin más contexto. Deja los cambios en disco; no los deshagas ni los publiques. '
           'Después avisa al usuario de dónde quedó y detente. Sigue trabajando solo si el usuario te lo pide expresamente.')


def claude_config_dir(event):
    """Config directory of the Claude account running this session; '' for the main account.

    The session's own transcript path is authoritative: hooks of the main profile also run in
    sessions of another account (when the project folder is the home), so the environment alone
    could name the wrong one.
    """
    transcript = event.get('transcript_path')
    directory = ''
    if isinstance(transcript, str) and transcript:
        directory = next((str(ancestor.parent) for ancestor in Path(transcript).parents if ancestor.name == 'projects'), '')
    directory = directory or os.environ.get('CLAUDE_CONFIG_DIR', '')
    return '' if not directory or Path(directory) == Path.home() / '.claude' else directory


def read_limits(agent, codex_home, claude_dir=''):
    root = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'omarchy-ai-team'
    # One reading per account: two Codex profiles have separate limits.
    if agent == 'claude':
        suffix = '-claude-' + hashlib.sha256(os.fsencode(claude_dir)).hexdigest()[:12] if claude_dir else ''
    else:
        suffix = '-codex-' + hashlib.sha256(os.fsencode(codex_home)).hexdigest()[:12]
    cache = root / f'usage-guard{suffix}.json'
    try:
        if time.time() - cache.stat().st_mtime < CACHE_SECONDS:
            return json.loads(cache.read_text())
    except (OSError, ValueError):
        pass
    command = f'omarchy-agent-usage-{agent}'
    tool = shutil.which(command) or f'/usr/share/omarchy/bin/{command}'
    environment = {**os.environ, **({'CODEX_HOME': codex_home} if agent == 'codex' else {})}
    if agent == 'claude':
        environment.pop('CLAUDE_CONFIG_DIR', None)  # the main account must not inherit another account's directory
        if claude_dir:
            environment['CLAUDE_CONFIG_DIR'] = claude_dir
            # The collector reuses its limits from $XDG_CACHE_HOME for a while: keep them per account.
            base = Path.home() / '.cache/omarchy-ai-team/usage'
            private = base / ('guard-' + hashlib.sha256(os.fsencode(claude_dir)).hexdigest()[:12])
            private.mkdir(parents=True, mode=0o700, exist_ok=True)
            base.chmod(0o700)
            private.chmod(0o700)
            environment['XDG_CACHE_HOME'] = str(private)
    result = subprocess.run([tool, '--limits-only'], capture_output=True, text=True, timeout=10, check=True, env=environment)
    limits = json.loads(result.stdout)['limits']
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=root, delete=False) as stream:
        json.dump(limits, stream)
    os.replace(stream.name, cache)
    return limits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--settings', required=True)
    parser.add_argument('--agent', choices=('claude', 'codex'), default='claude')
    parser.add_argument('--codex-home', default='')
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    event = payload['hook_event_name']
    path = Path(args.settings)
    guard = {'enabled': True, 'percent': 90, **(json.loads(path.read_text()).get('usageGuard', {}) if path.exists() else {})}
    if not guard['enabled']:
        return
    for row in read_limits(args.agent, args.codex_home, claude_config_dir(payload) if args.agent == 'claude' else ''):
        used = round(row['percent'] * 100)
        if used >= guard['percent']:
            text = USAGE_GUARD_MESSAGE.format(label=row['label'], used=used, limit=guard['percent'])
            print(json.dumps({'hookSpecificOutput': {'hookEventName': event, 'additionalContext': text}}))
            return


if __name__ == '__main__':
    try:
        main()
    except Exception:  # noqa: BLE001 - a failed reading must not interrupt the user's work.
        pass
