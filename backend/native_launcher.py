"""Native terminal launch and opt-in application entries; no shell or permissions override."""

from i18n import tr

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import native_plugins
import context_policy


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


def invocation(agent, *, config=None, role='jefe', context_override=None, cwd=None):
    """Shared per-process builder for terminal and explicit headless recipes."""
    if config is None and context_override is None:
        return [], []
    policy = context_policy.resolve(config if config is not None else {}, role, override=context_override)
    args, environment = context_policy.launch_options(agent, policy)
    if policy['enabled']:
        if agent == 'claude':
            for switch in ('DISABLE_AUTO_COMPACT', 'DISABLE_COMPACT'):
                if os.environ.get(switch, '').lower() in ('1', 'true', 'yes'):
                    raise context_policy.ContextPolicyError(tr('Compactación nativa desactivada por {switch}; resuélvelo o desactiva la política para esta ejecución.', switch=switch))
        import context_state
        capsule = context_state.instance(agent, role, cwd or terminal_cwd(), policy)
        extra_args, extra_env = context_state.launch_options(capsule)
        args += extra_args
        environment += extra_env
    return args, environment


def terminal_cwd():
    """Directory Omarchy opens a new terminal in; the backend's own cwd if unknown."""
    tool = shutil.which('omarchy-cmd-terminal-cwd')
    if tool:
        try:
            answer = subprocess.run([tool], capture_output=True, text=True, timeout=3, check=False)
            path = Path(answer.stdout.strip())
            if answer.returncode == 0 and answer.stdout.strip() and path.is_dir():
                return path
        except (OSError, ValueError, subprocess.SubprocessError):
            pass  # unknown terminal directory: keep the backend's own
    return Path.cwd()


def launch(binary, agent, home, *, arguments=(), config=None, role='jefe', context_override=None, cwd=None):
    # Freeze one directory: the context capsule and the terminal must share it.
    cwd = Path(cwd).resolve() if cwd else None
    policy_args, policy_env = invocation(agent, config=config, role=role, context_override=context_override, cwd=cwd)
    omarchy = shutil.which('omarchy')
    if not omarchy:
        raise LauncherError(tr('Omarchy no está disponible para abrir la terminal.'))
    # uwsm may rebuild the environment. Pass the selected profile inside the
    # terminal command.
    args = [omarchy, 'launch', 'terminal', '/usr/bin/env',
            '-u', 'CODEX_HOME', '-u', 'CLAUDE_CONFIG_DIR']
    frozen = next((x.split('=', 1)[1] for x in policy_env if x.startswith('AI_TEAM_CONTEXT=')), None)
    if frozen:
        args[4:4] = ['-C', json.loads(frozen)['cwd']]  # env changes directory before running the CLI
    if agent == 'pi':
        args+=['-u','PI_CODING_AGENT_DIR','PI_CODING_AGENT_DIR='+str(home)]
    elif agent != 'claude':
        args.append('CODEX_HOME='+str(home))
    elif os.environ.get('CLAUDE_CONFIG_DIR'):
        args.append('CLAUDE_CONFIG_DIR='+str(home))
    args += [*policy_env, binary, *policy_args, *arguments]
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def authenticate(binary, agent, home):
    if agent not in ('claude', 'codex', 'codex-2'):
        raise LauncherError(tr('Usa la autenticación nativa del proveedor desde el menú de Pi.'))
    launch(binary, agent, home, arguments=('auth','login','--claudeai') if agent == 'claude' else ('login',))
