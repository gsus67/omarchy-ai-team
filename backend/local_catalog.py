"""Local CLI capabilities and project preferences; no task or model routing."""

from i18n import tr

import copy
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

import native_plugins
import native_tools
import agent_runtime
import project_scope


CATALOG = {
    'git': {'name':'Git','category':tr('Código'),'binary':'git','license':'GPL-2.0-only','source':'https://git-scm.com',
            'purpose':tr('Historial, diferencias y worktrees mediante Git existente.'),'usage':'git status --short; git diff --stat; acota después a archivos concretos.','pattern':r'git version (\d[^\s]*)'},
    'rg': {'name':'ripgrep','category':tr('Búsqueda'),'binary':'rg','tool':'ripgrep','license':'MIT OR Unlicense','source':'https://github.com/BurntSushi/ripgrep',
           'purpose':tr('Búsquedas acotadas respetando exclusiones del proyecto.'),'usage':'rg -n PATTERN PATH; usa rg --files para localizar archivos.','pattern':r'ripgrep (\d[^\s]*)','mise':'ripgrep'},
    'jq': {'name':'jq','category':tr('Datos'),'binary':'jq','license':'MIT','source':'https://jqlang.org',
           'purpose':tr('Seleccionar campos de JSON sin enviar salidas enteras al modelo.'),'usage':'Usa filtros jq explícitos; conserva exit codes y errores necesarios.','pattern':r'jq-(\d[^\s]*)','mise':'jq'},
    'yq': {'name':'yq','category':tr('Datos'),'binary':'yq','license':'MIT','source':'https://github.com/mikefarah/yq',
           'purpose':tr('Seleccionar campos de YAML y TOML sin enviar archivos enteros al modelo.'),'usage':'Usa yq con expresiones explícitas sobre docker-compose, CI o configuraciones; conserva exit codes y errores.','pattern':r'(\d+\.\d+\.\d+)','mise':'yq'},
    'python': {'name':'Python','category':tr('Pruebas'),'binary':'python3','tool':'python','license':'PSF-2.0','source':'https://python.org',
               'purpose':tr('Parsers y pruebas que ya use el proyecto.'),'usage':'Reutiliza los scripts y unittest del proyecto; no añadas pytest por defecto.','pattern':r'Python (\d[^\s]*)'},
    'node': {'name':'Node','category':tr('Pruebas'),'binary':'node','license':'MIT','source':'https://nodejs.org',
             'purpose':tr('Ejecutar los scripts y pruebas JavaScript existentes.'),'usage':'Lee package.json y usa las comprobaciones pertinentes; conserva los permisos nativos.','pattern':r'v(\d[^\s]*)','mise':'node'},
    'qmllint': {'name':'qmllint','category':tr('Código'),'binary':'qmllint','path':'/usr/lib/qt6/bin/qmllint','license':'GPL-3.0-only / LGPL-3.0-only / Qt-GPL-exception-1.0','source':'https://doc.qt.io/qt-6/qtqml-tooling-qmllint.html',
                'purpose':tr('Comprobar QML con los imports reales de Qt/Omarchy.'),'usage':'Ejecuta qmllint con rutas de imports del proyecto; no copies Qt al repositorio.','pattern':r'(\d+\.\d+\.\d+)'},
    'ruff': {'name':'Ruff','category':tr('Formato'),'binary':'ruff','license':'MIT','source':'https://github.com/astral-sh/ruff',
             'purpose':tr('Lint/formato Python cuando sea pertinente al proyecto.'),'usage':'Empieza por ruff check; usa ruff check --select S para reglas de seguridad. No apliques --fix o formato si no corresponde a la tarea.','pattern':r'ruff (\d[^\s]*)','mise':'ruff'},
    'shellcheck': {'name':'ShellCheck','category':tr('Código'),'binary':'shellcheck','license':'GPL-3.0-or-later','source':'https://github.com/koalaman/shellcheck',
                   'purpose':tr('Análisis estático de scripts shell.'),'usage':'shellcheck ARCHIVOS; atiende los errores relevantes sin ejecutar los scripts.','pattern':r'version: (\d[^\s]*)','mise':'shellcheck'},
    'ast-grep': {'name':'ast-grep','category':tr('Búsqueda'),'binary':'ast-grep','license':'MIT','source':'https://github.com/ast-grep/ast-grep',
                 'purpose':tr('Búsqueda estructural cuando una búsqueda textual no baste.'),'usage':'Usa ast-grep scan/run con patrones acotados; nunca confundas sg de util-linux con ast-grep.','pattern':r'ast-grep (\d[^\s]*)','mise':'ast-grep'},
    'gitleaks': {'name':'Gitleaks','category':tr('Seguridad'),'binary':'gitleaks','license':'MIT','source':'https://github.com/gitleaks/gitleaks',
                 'purpose':tr('Detectar secretos en el código y en el historial Git.'),'usage':'Usa gitleaks detect/git sobre el repositorio; informa hallazgos sin imprimir valores secretos.','pattern':r'(\d+\.\d+\.\d+)','mise':'gitleaks'},
    'osv-scanner': {'name':'OSV-Scanner','category':tr('Seguridad'),'binary':'osv-scanner','license':'Apache-2.0','source':'https://github.com/google/osv-scanner',
                    'purpose':tr('Detectar dependencias vulnerables en Python, npm y Go.'),'usage':'Usa osv-scanner scan --recursive sobre los lockfiles del proyecto.','pattern':r'(\d+\.\d+\.\d+)','mise':'osv-scanner'},
    'hadolint': {'name':'hadolint','category':tr('Seguridad'),'binary':'hadolint','license':'GPL-3.0-only','source':'https://github.com/hadolint/hadolint',
                 'purpose':tr('Análisis estático de Dockerfiles.'),'usage':'Ejecuta hadolint sobre los Dockerfiles modificados.','pattern':r'(\d+\.\d+\.\d+)','mise':'hadolint'},
    'semgrep': {'name':'Semgrep','category':tr('Seguridad'),'binary':'semgrep','license':'LGPL-2.1-only','source':'https://github.com/semgrep/semgrep',
                'purpose':tr('Análisis de seguridad opcional en TypeScript, Go y Python.'),'usage':'Usa semgrep scan solo con configuraciones locales o explícitas, sin login ni subidas.','pattern':r'(\d+\.\d+\.\d+)','mise':'semgrep','requires':'uv'},
}
BRIEFS = {'rg': tr('Busca texto en el código muy rápido, sin leer archivos enteros.'), 'jq': tr('Extrae solo los campos que hacen falta de un JSON.'), 'yq': tr('Lo mismo que jq para YAML y TOML (Docker, CI, configuraciones).'), 'ast-grep': tr('Busca por la estructura del código, no solo por texto.'), 'shellcheck': tr('Detecta errores comunes en scripts de shell.'), 'ruff': tr('Revisa errores típicos y estilo en Python.'),
          'gitleaks': tr('Detecta contraseñas, tokens y claves en el código y en Git.'),
          'osv-scanner': tr('Avisa de dependencias vulnerables (Python, npm, Go…).'),
          'hadolint': tr('Revisa errores y malas prácticas en Dockerfiles.'),
          'semgrep': tr('Seguridad del código en TS, Go y Python. Pesado: instala uv.')}
GROUPS = {'tokens': ('rg', 'jq', 'yq', 'ast-grep'), 'security': ('gitleaks', 'osv-scanner', 'shellcheck', 'ruff', 'hadolint', 'semgrep')}
OPTIONAL = frozenset({'semgrep'})
MODES = ('auto', 'on', 'off')


class CatalogError(native_plugins.PluginError):
    pass


def validate(config):
    if not isinstance(config,dict):raise CatalogError(tr('La configuración local no es un objeto válido.'))
    value = config.get('localToolsSettings', {})
    if not isinstance(value, dict) or set(value) - {'defaults','projects','probes'}:
        raise CatalogError(tr('Las preferencias de herramientas locales no son compatibles.'))
    value = {key:copy.deepcopy(value.get(key, {})) for key in ('defaults','projects','probes')}
    if any(not isinstance(row,dict) for row in value.values()):
        raise CatalogError(tr('El formato de preferencias locales no es válido.'))
    for choices in (value['defaults'], value['probes'], *value['projects'].values()):
        if isinstance(choices,dict):choices.pop('agentshield',None)
    for key in value['projects']:
        if key=='default':raise CatalogError(tr('El ámbito general no es un proyecto.'))
        project_scope.scope_key(key)
    for choices in (value['defaults'], *value['projects'].values()):
        if not isinstance(choices,dict) or set(choices)-set(CATALOG) or any(mode not in MODES for mode in choices.values()):
            raise CatalogError(tr('Selector de herramientas no válido; se conserva.'))
    for key, record in value['probes'].items():
        if key not in CATALOG or not isinstance(record,dict) or any(not isinstance(record.get(field,''),str) for field in ('binary','stamp','version','checkedAt')):
            raise CatalogError(tr('Una comprobación guardada no tiene formato válido.'))
    return value


def preference(config, key, scope='default'):
    if key not in CATALOG:raise CatalogError(tr('Herramienta no admitida.'))
    project_scope.scope_key(scope); value=validate(config)
    default=value['defaults'].get(key,'auto')
    return (value['projects'].get(scope,{}).get(key,default) if scope!='default' else default)


def set_preference(config, key, scope, mode):
    if key not in CATALOG or mode not in (*MODES,'inherit'):
        raise CatalogError(tr('Herramienta o selector no admitido.'))
    project_scope.scope_key(scope); value=validate(config)
    if mode=='inherit':
        if scope=='default':raise CatalogError(tr('La preferencia general debe ser auto, activada o desactivada.'))
        value['projects'].get(scope,{}).pop(key,None)
        if not value['projects'].get(scope):value['projects'].pop(scope,None)
    elif scope=='default':value['defaults'][key]=mode
    else:value['projects'].setdefault(scope,{})[key]=mode
    config['localToolsSettings']=value


def group_state(config: dict) -> dict[str, bool]:
    if not any(group + 'Enabled' in config for group in GROUPS):
        return dict.fromkeys(GROUPS, bool(config.get('local-toolsEnabled')))
    state = {group: config.get(group + 'Enabled', False) for group in GROUPS}
    if any(type(enabled) is not bool for enabled in state.values()):
        raise CatalogError(tr('Las preferencias de los grupos deben ser booleanos explícitos.'))
    return state


def enabled_groups(config: dict) -> tuple[str, ...]:
    return tuple(sorted(group for group, enabled in group_state(config).items() if enabled))


def set_group(config: dict, group: str, enabled: bool) -> tuple[str, ...]:
    if group not in GROUPS or type(enabled) is not bool:
        raise CatalogError(tr('Grupo o activación no admitidos.'))
    state = group_state(config)
    state[group] = enabled
    config.update({key + 'Enabled': value for key, value in state.items()})
    tools = tuple(key for key in GROUPS[group] if not enabled or key not in OPTIONAL)
    for key in tools:
        set_preference(config, key, 'default', 'on' if enabled else 'auto')
    return tools if enabled else ()


def bridge_wanted(config: dict) -> bool:
    value = validate(config)
    return bool(enabled_groups(config) or any(
        mode == 'on' for choices in (value['defaults'], *value['projects'].values()) for mode in choices.values()))


def inspect(key, inventory, receipt=None):
    row=CATALOG[key]
    if row.get('path'):
        path=Path(row['path'])
        tool={'installation':'installed' if path.is_file() and os.access(path,os.X_OK) else 'missing','binary':str(path) if path.is_file() else '', 'version':''}
    else:
        tool=native_tools.detect_tool(row['binary'],inventory,row.get('tool',row['binary']))
    stamp=''
    if tool['installation']=='installed':
        path=Path(tool['binary']);stat=path.stat()
        stamp=hashlib.sha256(f'{path}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:'.encode()).hexdigest()
    receipt=receipt or {}
    verified=bool(stamp and stamp==receipt.get('stamp') and tool['binary']==receipt.get('binary'))
    return {**tool,'stamp':stamp,'probeVerified':verified,'version':receipt.get('version','') if verified else tool['version'],
            'checkedAt':receipt.get('checkedAt','') if verified else ''}


def probe(key, inventory):
    tool=inspect(key,inventory)
    if tool['installation']!='installed':
        raise CatalogError(tr('No está instalada; comprobar no instala el catálogo.'))
    with tempfile.TemporaryDirectory(prefix='ai-team-tool-probe-') as directory:
        # Wrappers such as semgrep exec sibling helpers by name, as mise shims allow.
        path=os.pathsep.join([str(Path(tool['binary']).parent),os.environ.get('PATH','')])
        result=subprocess.run([tool['binary'],'--version'],cwd=directory,capture_output=True,text=True,timeout=10,
                              env={**os.environ,'PATH':path})
    match=re.search(CATALOG[key]['pattern'],result.stdout+'\n'+result.stderr)
    if result.returncode or not match or inspect(key,inventory)['stamp']!=tool['stamp']:
        raise CatalogError(tr('La herramienta no confirmó su versión/identidad; sigue pendiente.'))
    return {'binary':tool['binary'],'stamp':tool['stamp'],'version':match[1][:80], 'checkedAt':datetime.now(timezone.utc).isoformat()}


def installed_versions(inventory, name):
    return {item['version'] for item in inventory.get(name, [])
            if item.get('installed') and isinstance(item.get('version'), str)}


def install(key, on_install=None):
    row=CATALOG.get(key,{})
    if not row.get('mise'):
        raise CatalogError(tr('Esta utilidad forma parte de la base de Omarchy; no se instalará desde una fuente alternativa.'))
    steps = ([(row['requires'], row['requires'])] if row.get('requires') else [])
    for name, binary in [*steps, (row['mise'], row['binary'])]:
        before, warning = native_tools.mise_inventory()
        if warning:raise CatalogError(warning)
        result = native_tools.ensure_mise(name, binary)
        after, warning = native_tools.mise_inventory()
        if warning:raise CatalogError(warning)
        versions = installed_versions(after, name) - installed_versions(before, name)
        if versions and on_install:
            on_install(name, sorted(versions))
    return result


def managed(key, tool, inventory):
    name=CATALOG[key].get('mise')
    rows=inventory.get(name,[]) if name else []
    if not isinstance(rows,list):return False
    return bool(tool.get('binary') and any(
        isinstance(item,dict) and item.get('active') and item.get('installed')
        and Path(str(item.get('install_path',''))).is_absolute()
        and Path(tool['binary']).is_relative_to(Path(item['install_path']).resolve())
        for item in rows))


def update(key):
    row=CATALOG[key]; inventory,warning=native_tools.mise_inventory()
    if warning:raise CatalogError(warning)
    current=inspect(key,inventory)
    if not row.get('mise') or current['installation']!='installed':
        raise CatalogError(tr('Actualizar requiere una utilidad instalada y gestionada con mise.'))
    if not managed(key,current,inventory):
        raise CatalogError(tr('La utilidad pertenece a otro gestor; su instalación se conserva.'))
    previous=agent_runtime.fingerprint(current['binary'])
    mise=native_tools.shutil.which('mise')
    if not mise:raise CatalogError(tr('mise no está disponible.'))
    result=subprocess.run([mise,'upgrade','--global','--no-prune',row['mise']],cwd=Path.home(),
                          capture_output=True,text=True,timeout=180)
    if result.returncode:raise CatalogError(tr('mise no completó la actualización; comprueba antes de reintentar.'))
    inventory,warning=native_tools.mise_inventory()
    if warning:raise CatalogError(warning)
    receipt=probe(key,inventory)
    if not managed(key,receipt,inventory):
        raise CatalogError(tr('El gestor no confirmó el nuevo ejecutable dentro de su instalación.'))
    if not Path(current['binary']).is_file() or agent_runtime.fingerprint(current['binary'])!=previous:
        raise CatalogError(tr('No se conservó el ejecutable anterior; no se declara actualización segura.'))
    changed=agent_runtime.fingerprint(receipt['binary'])!=previous or receipt['version']!=current.get('version')
    return {'component':key,'changed':changed,'outcome':'updated' if changed else 'current',
            'version':receipt['version'],'receipt':receipt}


def uninstall_deps(config, key):
    records=config.get('catalogInstalls',{})
    used={dep for other,record in records.items() if other!=key for dep in record['deps']}
    return [dep for dep in records.get(key,{}).get('deps',[]) if dep not in used]


def unuse(name, versions=()):
    mise=native_tools.shutil.which('mise')
    if not mise:raise CatalogError(tr('mise no está disponible.'))
    result=subprocess.run([mise,'unuse','--global','--no-prune',name],cwd=Path.home(),
                          capture_output=True,text=True,timeout=180)
    if result.returncode:raise CatalogError(tr('mise no completó la desinstalación de {name}; comprueba antes de reintentar.', name=name))
    if not versions:return
    inventory, warning = native_tools.mise_inventory()
    if warning:raise CatalogError(warning)
    # A retry must not let mise resolve an absent exact version as a prefix.
    for version in sorted(set(versions) & installed_versions(inventory, name)):
        result=subprocess.run([mise,'uninstall',f'{name}@{version}'],cwd=Path.home(),
                              capture_output=True,text=True,timeout=180)
        if result.returncode:raise CatalogError(tr('mise no completó la desinstalación de {name}@{version}; comprueba antes de reintentar.', name=name, version=version))


def snapshot(config, scope='default', bridge_verified=False):
    value=validate(config);inventory,_=native_tools.mise_inventory();rows=[]
    for key, data in CATALOG.items():
        error=''
        try:
            tool=inspect(key,inventory,value['probes'].get(key))
        except (OSError,ValueError,KeyError,TypeError,native_plugins.PluginError) as problem:
            tool={'installation':'error','binary':'','version':'','stamp':'','probeVerified':False,'checkedAt':''}
            error=str(problem)
        mode=preference(config,key,scope)
        public={name:data[name] for name in ('name','category','license','source','purpose')}
        public['brief']=BRIEFS.get(key,'')
        owned = key in config.get('catalogInstalls', {})
        external = tool['installation'] == 'installed' and not owned
        public['status'] = (tr('Del sistema') if external else tr('Listo') if tool['probeVerified'] else tr('Pendiente'))
        public['ownershipDetail'] = tr('No se puede desinstalar aquí: es del sistema y otros programas pueden necesitarla') if external else ''
        available=bridge_verified and tool['probeVerified'] and mode!='off'
        detail=(error if error else tr('Integración desactivada; instalación conservada.') if mode=='off' else
                tr('Disponible en la integración; la ejecución conserva permisos nativos.') if available else
                tr('Binario comprobado; conexión con el agente pendiente.') if tool['probeVerified'] else
                tr('Instalado; comprobación pendiente.') if tool['installation']=='installed' else
                tr('No instalado; Automático no instala herramientas.'))
        rows.append({'id':key,**public,**tool,'mode':mode,'available':available,'detail':detail,
                     'optional':key in OPTIONAL,
                     'installSupported':bool(data.get('mise')),
                     'updateSupported':managed(key,tool,inventory),
                     'uninstallItems':([data['name']]+[CATALOG.get(dep,{}).get('name',dep) for dep in uninstall_deps(config,key)]
                                       if key in config.get('catalogInstalls',{}) else [])})
    return rows


def context(config, cwd, bridge_verified=False):
    scope=project_scope.project_key(cwd)
    rows=snapshot(config,scope,bridge_verified)
    # This is a capability list. The native agent chooses relevance, not this code.
    return {'project':scope,'tools':[{'id':row['id'],'mode':row['mode'],'command':row['binary'],
                                    'usage':CATALOG[row['id']]['usage']} for row in rows if row['available']],
            'detail':tr('Solo integración gestionada. Desactivar no bloquea ejecutables fuera de los permisos nativos.')}
