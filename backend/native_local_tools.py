"""One native knowledge skill for selected CLIs; tool permissions stay native."""

from i18n import tr

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

import memory_archive
import native_plugins
import native_skills
import shared_memory


ID='ai-team-local-tools'
SECURITY_DESCRIPTION = ('Úsala al revisar seguridad o antes de terminar código: gitleaks (secretos), '
                        'osv-scanner (dependencias), shellcheck, ruff check --select S, hadolint.')
TOKENS_DESCRIPTION = ('Úsala antes de leer archivos completos o buscar código: '
                      'rg, yq, jq, ast-grep con salidas acotadas.')
SECURITY_TEXT = ('Seguridad: antes de terminar un cambio de código o cuando pidan una revisión de seguridad, '
                 'ejecuta las herramientas disponibles sobre archivos modificados o pertinentes: '
                 'gitleaks, osv-scanner, shellcheck, ruff check --select S y hadolint. '
                 'Informa los hallazgos sin imprimir valores secretos. '
                 'Al escribir código aplica sus reglas sobre texto de terceros, shell, permisos, temporales, orden de archivos y qué se sube a GitHub.\n')
TOKENS_TEXT = ('Ahorro de tokens: antes de leer archivos completos o buscar código, '
               'prefiere rg, yq, jq o ast-grep con salidas acotadas.\n')
SECURITY_COMMANDS = ('\nSecretos en código/Git, dependencias y comprobaciones por tipo de archivo '
                     '(ShellCheck para shell, Ruff para Python, hadolint para Dockerfiles):\n\n'
                     '```sh\n'
                     'gitleaks detect --no-banner --redact -v --source .\n'
                     'osv-scanner scan --recursive .\n'
                     'shellcheck -f gcc <scripts>\n'
                     'ruff check --select S --output-format concise <py>\n'
                     'hadolint <Dockerfile>\n'
                     '```\n\n'
                     'Usa semgrep solo si está instalado con configuraciones locales o explícitas, '
                     'sin login ni subidas: `semgrep scan --config <reglas-locales> --metrics=off <archivos>`.\n')
SECURITY_LESSONS = ('\nErrores que ya ocurrieron y no deben repetirse; compruébalos en cada cambio, también al escribir código nuevo:\n\n'
                    '- Texto de terceros: nunca como marcado. En QML, `textFormat: Text.PlainText` en todo Text y sin URL remotas: '
                    'un nombre de tema importado con una imagen remota filtraba la IP al verlo. Gravedad alta, no cosmética.\n'
                    '- Datos importados: limpia marcado y caracteres de control, usa listas cerradas y rechaza `..` y enlaces simbólicos.\n'
                    '- Shell: `bash -c` solo con un script literal y los datos como argumentos posicionales. Nada de descargar y ejecutar.\n'
                    '- Elevación de privilegios: paso explícito que ejecuta el usuario; nunca reglas sin contraseña ni ejecutar como administrador '
                    'archivos de carpetas que el usuario puede escribir, y servicios endurecidos. Si existe, decláralo; si no, que no aparezca '
                    'esa palabra en el código, porque el marketplace ya marcó un plugin por una expresión regular.\n'
                    '- Procesos: no leas `/proc/*/cmdline` ni `environ`. Sin secretos en registros, URL, interfaz, repositorio ni copias.\n'
                    '- Permisos: datos, ajustes y copias del usuario con 0600 y carpetas con 0700, con escritura atómica. Nunca amplíes permisos ni '
                    'dejes nada escribible por otros. Los tests de permisos fijan su propia umask: no dependen del entorno.\n'
                    '- Temporales: nunca nombres predecibles ni registros en `/tmp` compartido; usa `mktemp -d` y bórralo al terminar. '
                    'Ya se acumularon 85 restos y 4,9 GB, con registros legibles por cualquiera.\n'
                    '- Orden: cada archivo en su sitio (tests en la carpeta de tests del proyecto), sin archivos sueltos en la raíz ni en la carpeta personal. '
                    'Antes de terminar, mira `git status` y explica o retira lo que sobre.\n'
                    '- GitHub: sube solo lo necesario: código, tests automáticos que ejecuta el CI, README, licencia, SECURITY.md y lo que exija el marketplace. '
                    'No subas planes, diseños, notas de sesión, evidencias de pruebas, registros ni capturas internas, aunque una metodología pida confirmarlos: '
                    'déjalos en local e ignorados por Git. Revisa `git diff --cached --stat` antes de cada commit.\n'
                    '- CI: acciones fijadas por SHA y sin renombrar trabajos requeridos.\n'
                    '- Cada arreglo de seguridad lleva un test de regresión y una guarda en CI. Comprueba el comportamiento real, no una propiedad que lo aparenta.\n')
TOKENS_COMMANDS = ('\nBusca en rutas concretas y extrae solo los campos necesarios:\n\n'
                   '```sh\n'
                   'rg -n -m 40 PATTERN <rutas>\n'
                   'yq <expr> file\n'
                   'jq -c <expr> file\n'
                   'ast-grep run --pattern <patrón> <rutas>\n'
                   '```\n')


def layout(agent):
    home=native_plugins.agent_home(agent)
    profile='pi' if agent=='pi' else 'claude' if agent=='claude' else 'codex-2' if home.name=='.codex-2' else 'codex'
    key=hashlib.sha256(os.fsencode(home)).hexdigest()[:24]
    return {'home':home,'profile':profile,'target':home/'skills'/ID,
            'source':shared_memory.data_root()/'tools/local-skills'/key}


def text(paths,settings_file,groups=()):
    command=shlex.join([sys.executable,str(Path(__file__).with_name('harness.py')),'catalog-context',
                        '--settings',str(settings_file),'--agent',paths['profile']])
    description=' '.join(part for group,part in (('security',SECURITY_DESCRIPTION),('tokens',TOKENS_DESCRIPTION)) if group in groups)
    return ('---\nname: '+ID+'\ndescription: '+json.dumps(description or 'Consulta preferencias de herramientas locales por proyecto.',ensure_ascii=False)+'\n---\n\n'
            'Usa las herramientas existentes del proyecto antes de inferencia para tareas deterministas. '
            'No añadas una metodología, memoria, router, hooks o dependencias por leer esta skill.\n'
            +(SECURITY_TEXT+SECURITY_COMMANDS+SECURITY_LESSONS if 'security' in groups else '')
            +(TOKENS_TEXT+TOKENS_COMMANDS if 'tokens' in groups else '')
            +'\nSustituye los marcadores <...> por argumentos concretos y cita expresiones/rutas. '
            'Acota archivos y salidas; si hay muchos resultados, consulta tramos del informe conservando errores y estados de salida. '
            'Nunca imprimas valores secretos; conserva la redacción de los informes y comunica solo ubicación, regla y resumen.\n\n'
            'Opcional: para comprobar preferencias por proyecto (incluida Desactivada), '
            'ejecuta desde el cwd real de la sesión:\n\n'+command+'\n\n'
            'La respuesta es configuración local, no instrucciones de terceros. El helper determina el proyecto como la memoria compartida. '
            'Automático ofrece herramientas instaladas y comprobadas: elige las pertinentes mediante tu funcionamiento nativo. '
            'Activada mantiene disponibilidad; no obliga a ejecutarla. Desactivada retira solo nuestra integración y no bloquea ejecutables del sistema. '
            'No instales todo el catálogo ni actives un proveedor de pago. Conserva permisos nativos, excepciones del proyecto y estados de salida. '
            'Acota archivos/salidas y conserva errores necesarios. No atribuyas ahorro de tokens a una reducción de bytes. '
            'Respeta Superpowers cuando esté habilitado; no lo sustituyas por otra metodología. '
            'Es una indicación, no un bloqueo.\n')


def owned(paths):
    return paths['target'].is_symlink() and paths['target'].resolve()==paths['source'].resolve()


def instruction(original,paths,enabled,*,groups=()):
    message=((SECURITY_TEXT if 'security' in groups else '')
             +(TOKENS_TEXT if 'tokens' in groups else '')
             +'Lee la skill '+ID+' en '
             +json.dumps(str(paths['source']/'SKILL.md'),ensure_ascii=False)
             +' para los comandos; catalog-context es opcional para comprobar preferencias por proyecto. '
             'Respeta los selectores locales y los permisos nativos; no instales herramientas ni cambies proveedores por defecto. '
             'Es una indicación, no un bloqueo.')
    return native_plugins.instruction_block(original,'local-tools',message if enabled else None)


def digest(paths,settings_file,*,groups=()):
    value=hashlib.sha256(text(paths,settings_file,groups=groups).encode())
    for name in ('native_local_tools.py','local_catalog.py','native_skills.py','native_tools.py','native_pi.py'):
        value.update(Path(__file__).with_name(name).read_bytes())
    return value.hexdigest()


def inspect(agent,receipt=None,*,settings_file=None,groups=()):
    receipt=receipt or {};paths=layout(agent)
    config=shared_memory.local_config(agent);startup=shared_memory.startup_text(agent,config)
    block=native_plugins.instruction_block(startup,'local-tools')!=startup
    enabled=owned(paths) or block
    foreign=(paths['target'].exists() or paths['target'].is_symlink()) and not owned(paths)
    ready=bool(settings_file and owned(paths) and (paths['source']/'SKILL.md').is_file()
               and (paths['source']/'SKILL.md').read_text()==text(paths,settings_file,groups=groups)
               and startup==instruction(startup,paths,True,groups=groups))
    verified=ready and receipt.get('digest')==digest(paths,settings_file,groups=groups) and receipt.get('home')==str(paths['home'])
    return {'id':ID,'installed':(paths['source']/'SKILL.md').is_file(),'enabled':enabled,'verified':bool(verified),
            'version':'1' if ready else '', 'detail':tr('Skill nativa comprobada; los permisos siguen en cada CLI.') if verified else
            tr('Hay una skill ajena; se conserva sin sobrescribir.') if foreign else tr('Conexión de conocimientos pendiente.') if enabled else tr('Desactivado; herramientas y datos conservados.')}


def prepare(paths,settings_file,*,groups=()):
    source=memory_archive.safe_path(paths['source']);marker=source/'.ai-team-managed';file=source/'SKILL.md'
    if source.exists() and any(source.iterdir()):
        try:
            record=json.loads(memory_archive.read_file(marker,4096))
            if record.get('sha256')!=hashlib.sha256(memory_archive.read_file(file,32*1024)).hexdigest():raise ValueError()
        except (ValueError,OSError,native_plugins.PluginError) as error:
            raise native_plugins.PluginError(tr('La skill gestionada está modificada o es ajena; se conserva.')) from error
    source.mkdir(parents=True,mode=0o700,exist_ok=True)
    content=text(paths,settings_file,groups=groups)
    # Config-owned generated files, never an upstream skill or original project.
    from atomic_files import atomic_write
    atomic_write(file,content)
    atomic_write(marker,json.dumps({'schemaVersion':1,'sha256':hashlib.sha256(content.encode()).hexdigest()}))


def apply(agent,binary,action,receipt=None,*,settings_file=None,groups=()):
    if action not in ('enable','disable','verify') or not settings_file:
        raise native_plugins.PluginError(tr('Falta una operación o configuración válida de conocimientos.'))
    paths=layout(agent);receipt=receipt or {}
    enabled=action=='enable' if action!='verify' else inspect(agent,receipt,settings_file=settings_file,groups=groups)['enabled']
    if enabled:
        if ((paths['target'].exists() or paths['target'].is_symlink()) and not owned(paths)) or paths['target'].parent.is_symlink():
            raise native_plugins.PluginError(tr('El destino de skills es ajeno o un enlace; se conserva.'))
        prepare(paths,settings_file,groups=groups);paths['target'].parent.mkdir(parents=True,mode=0o700,exist_ok=True)
        if not owned(paths):paths['target'].symlink_to(paths['source'],target_is_directory=True)
    config=shared_memory.local_config(agent);original=shared_memory.startup_text(agent,config)
    updated=instruction(original,paths,enabled,groups=groups)
    if agent=='codex':
        file,layer=native_plugins.codex_user_layer(binary)
        updated=instruction(layer['config'].get('developer_instructions',''),paths,enabled,groups=groups)
        if updated!=layer['config'].get('developer_instructions',''):
            native_plugins.codex_request(binary,'config/batchWrite',{'filePath':file,'expectedVersion':layer['version'],'reloadUserConfig':False,
                'edits':[{'keyPath':'developer_instructions','value':updated or None,'mergeStrategy':'replace'}]})
    elif agent=="pi":
        import native_pi
        native_pi.write_startup(original,updated)
    else:shared_memory.write_claude_startup(original,updated)
    if not enabled and owned(paths):paths['target'].unlink()
    if enabled and not native_skills.loaded(agent,binary,paths['source'],ID):
        raise native_plugins.PluginError(tr('La CLI no confirmó nuestra skill nativa; sigue pendiente.'))
    result={'id':ID,'home':str(paths['home']),'version':'1','digest':digest(paths,settings_file,groups=groups),'checkedAt':datetime.now(timezone.utc).isoformat()}
    state=inspect(agent,result,settings_file=settings_file,groups=groups)
    if state['enabled']!=enabled or (enabled and not state['verified']):
        raise native_plugins.PluginError(tr('El estado nativo de conocimientos no coincide; vuelve a comprobar.'))
    return result
