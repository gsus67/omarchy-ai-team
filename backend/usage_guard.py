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


def read_limits(agent, codex_home):
    root = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'omarchy-ai-team'
    # One reading per account: two Codex profiles have separate limits.
    suffix = '' if agent == 'claude' else '-codex-' + hashlib.sha256(os.fsencode(codex_home)).hexdigest()[:12]
    cache = root / f'usage-guard{suffix}.json'
    try:
        if time.time() - cache.stat().st_mtime < CACHE_SECONDS:
            return json.loads(cache.read_text())
    except (OSError, ValueError):
        pass
    command = f'omarchy-agent-usage-{agent}'
    tool = shutil.which(command) or f'/usr/share/omarchy/bin/{command}'
    environment = {**os.environ, **({'CODEX_HOME': codex_home} if agent == 'codex' else {})}
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
    event = json.load(sys.stdin)['hook_event_name']
    path = Path(args.settings)
    guard = {'enabled': True, 'percent': 90, **(json.loads(path.read_text()).get('usageGuard', {}) if path.exists() else {})}
    if not guard['enabled']:
        return
    for row in read_limits(args.agent, args.codex_home):
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
