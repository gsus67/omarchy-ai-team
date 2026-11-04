"""Reuse Omarchy's mise packages; never run its lazy agent wrapper to install."""

from i18n import tr

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess

import native_plugins


class AgentError(native_plugins.PluginError):
    pass


def managed(name, tool, inventory):
    rows = inventory.get(name, [])
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get('installed') or not row.get('active'):
            continue
        root = Path(str(row.get('install_path', '')))
        if root.is_absolute() and tool.get('binary') and Path(tool['binary']).is_relative_to(root.resolve()):
            return True
    return False


def fingerprint(binary):
    value = hashlib.sha256()
    with Path(binary).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def check_version(name, tool):
    if tool.get('installation') != 'installed':
        raise AgentError(tr('El gestor no confirmó un ejecutable instalado; sigue pendiente.'))
    result = subprocess.run([tool['binary'], '--version'], cwd=Path.home(), capture_output=True, text=True, timeout=15)
    pattern = r'^codex-cli\s+(\d+\.\d+\.\d+)\b' if name == 'codex' else r'^(\d+\.\d+\.\d+)\s+\(Claude Code\)'
    if name == 'pi':pattern=r'^(\d+\.\d+\.\d+)\s*$'
    if name == 'muse':pattern=r'^Muse Code (\d+\.\d+\.\d+)'
    match = re.search(pattern, result.stdout, re.MULTILINE)
    if result.returncode or not match or (tool.get('version') and match[1] != tool['version']):
        raise AgentError(tr('La CLI no confirmó la versión que declara su gestor; no se marcará comprobada.'))
    return match[1]


def operate(name, action, inventory, detect, refresh):
    if name == 'muse':
        # Its help documents no mise package or update channel; installing is left to its own method.
        raise AgentError(tr('Muse Code no se instala ni actualiza desde AI Team; usa su método oficial.'))
    if name not in ('claude', 'codex', 'pi') or action not in ('install', 'update', 'uninstall'):
        raise AgentError(tr('Operación de agente no admitida.'))
    current = detect(name, inventory)
    exists = current['installation'] == 'installed'
    if action == 'uninstall':
        if not exists:
            raise AgentError(tr('El agente no está instalado.'))
        if not managed(name, current, inventory):
            raise AgentError(tr('La CLI pertenece a otro gestor. Se conserva su instalación y sus preferencias.'))
        mise = shutil.which('mise')
        if not mise:
            raise AgentError(tr('mise no está disponible; usa primero la instalación oficial de Omarchy.'))
        try:
            result = subprocess.run([mise, 'unuse', '--global', name], cwd=Path.home(), capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired as error:
            raise AgentError(tr('mise no terminó a tiempo. Comprueba el estado antes de reintentar; no se cerraron sesiones.')) from error
        if result.returncode:
            raise AgentError(tr('mise no completó la operación oficial; se conserva la selección anterior.'))
        inventory, warning = refresh()
        if warning:
            raise AgentError(warning)
        if detect(name, inventory)['installation'] == 'installed':
            raise AgentError(tr('mise no confirmó la desinstalación; sigue pendiente.'))
        return {'outcome': 'removed', 'changed': True, 'previousVersion': current.get('version', '')}
    if action == 'update' and not exists:
        raise AgentError(tr('El agente no está instalado; Actualizar no instala otro componente.'))
    if exists and action == 'install':
        return {'outcome': 'current', 'version': check_version(name, current), 'changed': False}
    if exists and not managed(name, current, inventory):
        raise AgentError(tr('La CLI pertenece a otro gestor. Se conserva su instalación y sus preferencias.'))
    mise = shutil.which('mise')
    if not mise:
        raise AgentError(tr('mise no está disponible; usa primero la instalación oficial de Omarchy.'))
    previous = fingerprint(current['binary']) if exists else ''
    args = [mise, 'upgrade', '--global', '--no-prune', name] if action == 'update' else [mise, 'use', '--global', '--quiet', name]
    try:
        result = subprocess.run(args, cwd=Path.home(), env={**os.environ, 'MISE_MINIMUM_RELEASE_AGE': '0'},
                                capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as error:
        raise AgentError(tr('mise no terminó a tiempo. Comprueba el estado antes de reintentar; no se cerraron sesiones.')) from error
    if result.returncode:
        raise AgentError(tr('mise no completó la operación oficial; se conserva la selección anterior.'))
    inventory, warning = refresh()
    if warning:
        raise AgentError(warning)
    installed = detect(name, inventory)
    version = check_version(name, installed)
    if not managed(name, installed, inventory):
        raise AgentError(tr('mise no confirmó una instalación gestionada del agente; sigue pendiente.'))
    if exists and (not Path(current['binary']).is_file() or fingerprint(current['binary']) != previous):
        raise AgentError(tr('El gestor no conservó el ejecutable anterior; no se declara una actualización segura.'))
    digest = fingerprint(installed['binary'])
    changed = not exists or version != current.get('version') or digest != previous
    return {'outcome': 'installed' if not exists else 'updated' if changed else 'current',
            'version': version, 'changed': changed, 'digest': digest, 'previousVersion': current.get('version', '')}
