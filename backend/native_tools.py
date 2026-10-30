"""Local Omarchy/mise discovery shared by integrations; detection never installs."""

from i18n import tr

import json
import os
from pathlib import Path
import shutil
import subprocess

import native_plugins


def mise_inventory():
    binary = shutil.which("mise")
    if not binary:
        return {}, tr('mise no está disponible; las versiones gestionadas no se pudieron consultar.')
    try:
        result = subprocess.run([binary, "ls", "--installed", "--json"], cwd=Path.home(), capture_output=True, text=True, timeout=5)
        value = json.loads(result.stdout) if result.returncode == 0 else None
        if not isinstance(value, dict):
            raise ValueError()
        return value, ""
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}, tr('No se pudo consultar mise; no se ejecutó ningún instalador.')


def is_launcher(path):
    """Recognize lazy Omarchy wrappers and mise shims without executing them."""
    if "mise/shims" in path.as_posix() or path.resolve().name == "mise":
        return True
    try:
        with path.open("rb") as source:
            prefix = source.read(4096)
        return b"mise use -g" in prefix or b"omarchy-install-and-launch" in prefix
    except OSError:
        return False


def detect_tool(name, inventory, tool_name=None):
    found = shutil.which(name)
    candidate = Path(found) if found else None
    rows = inventory.get(tool_name or name, [])
    if not isinstance(rows, list):
        rows = []
    active = [row for row in rows if isinstance(row, dict) and row.get("installed") and row.get("active")]
    managed = []
    for row in active:
        directory = Path(str(row.get("install_path", "")))
        if not directory.is_absolute():
            continue
        for path in (directory / "bin" / name, directory / name):
            if path.is_file() and os.access(path, os.X_OK) and not is_launcher(path):
                managed.append((path.resolve(), str(row.get("version", ""))))
                break
        else:
            # Aqua and other backends expose their own bin paths. Ask mise only
            # for a version already confirmed installed; never exec a shim or
            # resolve from the caller's project configuration.
            mise = shutil.which('mise')
            version = str(row.get('version', ''))
            if not mise or not version:
                continue
            try:
                result = subprocess.run([mise, 'which', name, '--tool', f'{tool_name or name}@{version}'],
                                        cwd=Path.home(), capture_output=True, text=True, timeout=5)
                path = Path(result.stdout.strip())
                if (result.returncode == 0 and path.is_absolute()
                        and path.resolve().is_relative_to(directory.resolve())
                        and path.is_file() and os.access(path, os.X_OK) and not is_launcher(path)):
                    managed.append((path.resolve(), version))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
    if candidate and not is_launcher(candidate):
        resolved = candidate.resolve()
        version = next((version for path, version in managed if path == resolved), "")
        return {"installation": "installed", "binary": str(resolved), "version": version}
    if managed:
        return {"installation": "installed", "binary": str(managed[0][0]), "version": managed[0][1]}
    return {"installation": "launcher-only" if candidate else "missing", "binary": "", "version": ""}


def ensure_mise(tool_name, binary_name=None):
    binary_name = binary_name or tool_name
    inventory, _ = mise_inventory()
    current = detect_tool(binary_name, inventory, tool_name)
    if current['installation'] == 'installed':
        return current
    mise = shutil.which('mise')
    if not mise:
        raise native_plugins.PluginError(tr('mise no está disponible para instalar la dependencia necesaria.'))
    try:
        result = subprocess.run([mise, 'use', '--global', '--quiet', f'{tool_name}@latest'], cwd=Path.home(),
                                env={**os.environ, 'MISE_MINIMUM_RELEASE_AGE': '0'},
                                text=True, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired as error:
        raise native_plugins.PluginError(tr('La instalación de la dependencia no terminó a tiempo; comprueba antes de reintentar.')) from error
    if result.returncode:
        raise native_plugins.PluginError(tr('mise no completó la instalación de la dependencia; la integración sigue pendiente.'))
    inventory, warning = mise_inventory()
    current = detect_tool(binary_name, inventory, tool_name)
    if warning or current['installation'] != 'installed':
        raise native_plugins.PluginError(tr('El gestor no confirmó una dependencia instalada; no se simulará el resultado.'))
    return current


def node_binary():
    found = shutil.which('node')
    if found and not is_launcher(Path(found)):
        return str(Path(found).resolve())
    inventory, _ = mise_inventory()
    return detect_tool('node', inventory)['binary']
