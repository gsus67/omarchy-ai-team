"""Native terminal launch and opt-in application entries; no shell or permissions override."""

from i18n import tr

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import native_plugins


class LauncherError(native_plugins.PluginError):
    pass


def applications_dir():
    base = Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))
    if not base.is_absolute():
        raise LauncherError(tr('XDG_DATA_HOME debe ser una ruta absoluta.'))
    folder = base/'applications'
    if folder.is_symlink():
        raise LauncherError(tr('La carpeta de aplicaciones es un enlace; se conserva sin modificar.'))
    return folder


def desktop_quote(value):
    if any(c in value for c in '\n\r\0'):
        raise LauncherError(tr('La ruta del lanzador no es válida para un acceso de escritorio.'))
    quoted = ''.join('\\'+c if c in '\\"`$' else c for c in value)
    # Desktop string decoding precedes Exec quoting; %% is a literal percent.
    return '"'+quoted.replace('\\', '\\\\').replace('%', '%%')+'"'


def desktop_text(agent, name, script):
    command = ' '.join(desktop_quote(v) for v in (sys.executable, str(script), 'launch', agent))
    return ('[Desktop Entry]\nType=Application\nVersion=1.0\n'
            f'Name={name} (AI Team)\nComment={tr("Abrir el perfil con sus integraciones habilitadas")}\n'
            f'Exec={command}\nIcon=utilities-terminal\nTerminal=false\nCategories=Development;\n'
            'X-AI-Team-Managed=launcher-v1\n')


def _read(path):
    if path.is_symlink():
        raise LauncherError(tr('Un acceso del menú es un enlace; se conserva sin modificar.'))
    return path.read_bytes() if path.exists() else None


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _entries(config, agents, script):
    folder = applications_dir(); receipts = config.get('launcherDesktop', {})
    entries = []
    for agent, name in agents.items():
        path = folder/('ai-team-'+agent+'.desktop')
        content = desktop_text(agent, name, script).encode()
        original = _read(path)
        if original is not None and original != content and _digest(original) != receipts.get(agent):
            raise LauncherError(tr('El acceso {nombre} contiene cambios ajenos; se conserva. No se modificó el menú.', nombre=path.name))
        desired = config.get('launcherMenuEnabled', False) and config.get('agents', {}).get(agent, False)
        entries.append((agent, path, original, content if desired else None))
    return entries


def inspect_menu(config, agents, script):
    enabled = config.get('launcherMenuEnabled', False)
    try:
        ready = all(original == desired for _, _, original, desired in _entries(config, agents, script))
        return {'enabled': enabled, 'verified': ready,
                'detail': tr('Accesos de los perfiles seleccionados preparados.') if enabled and ready else
                          tr('Accesos del menú desactivados.') if ready else tr('Cambios del menú pendientes. Pulsa Aplicar menú.')}
    except (OSError, ValueError, LauncherError) as error:
        return {'enabled': enabled, 'verified': False, 'detail': str(error)}


def sync_menu(config, agents, script):
    entries = _entries(config, agents, script)  # Preflight all paths before writing any.
    receipts = {}
    for agent, path, original, desired in entries:
        if original != desired:
            if desired is None:
                if _read(path) != original:
                    raise LauncherError(tr('El acceso cambió durante la operación; se conserva.'))
                path.unlink()
            else:
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.ai-team-', delete=False) as output:
                        temporary = Path(output.name); os.chmod(temporary, 0o644)
                        output.write(desired); output.flush(); os.fsync(output.fileno())
                    if _read(path) != original:
                        raise LauncherError(tr('El acceso cambió durante la operación; se conserva.'))
                    os.replace(temporary, path)
                finally:
                    if temporary:
                        temporary.unlink(missing_ok=True)
        if desired is not None:
            receipts[agent] = _digest(desired)
    config['launcherDesktop'] = receipts


def launch(binary, agent, home, *, arguments=()):
    omarchy = shutil.which('omarchy')
    if not omarchy:
        raise LauncherError(tr('Omarchy no está disponible para abrir la terminal.'))
    # uwsm may rebuild the environment. Pass the selected profile inside the
    # terminal command.
    args = [omarchy, 'launch', 'terminal', '/usr/bin/env',
            '-u', 'CODEX_HOME', '-u', 'CLAUDE_CONFIG_DIR']
    if agent == 'pi':
        args+=['-u','PI_CODING_AGENT_DIR','PI_CODING_AGENT_DIR='+str(home)]
    elif agent != 'claude':
        args.append('CODEX_HOME='+str(home))
    elif os.environ.get('CLAUDE_CONFIG_DIR'):
        args.append('CLAUDE_CONFIG_DIR='+str(home))
    args += [binary, *arguments]
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def authenticate(binary, agent, home):
    if agent not in ('claude', 'codex', 'codex-2'):
        raise LauncherError(tr('Usa la autenticación nativa del proveedor desde el menú de Pi.'))
    launch(binary, agent, home, arguments=('auth','login','--claudeai') if agent == 'claude' else ('login',))
