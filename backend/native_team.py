"""Native team skill for selected CLIs; execution and permissions stay native."""

from i18n import tr, saved_message

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import tomllib

from atomic_files import atomic_write
import memory_archive
import native_plugins
import native_skills
import shared_memory


ID='ai-team-roles'


def layout(agent):
    home=native_plugins.agent_home(agent)
    profile='pi' if agent=='pi' else ('claude-2' if home.name=='.claude-2' else 'claude') if agent=='claude' else 'codex-2' if home.name=='.codex-2' else 'codex'
    key=hashlib.sha256(os.fsencode(home)).hexdigest()[:24]
    return {'home':home,'profile':profile,'target':home/'skills'/ID,
            'source':shared_memory.data_root()/'tools/team-skills'/key}


def owned(paths):
    return paths['target'].is_symlink() and paths['target'].resolve()==paths['source'].resolve()


def instruction(original,paths,enabled,*,team=None):
    message=None
    if enabled:
        state=team or {};assignments=state.get('roles',{})
        profiles=[profile for profile in ('codex-2','codex','claude-2','claude') if profile in state.get('selected',[paths['profile']])]
        review=assignments.get('review')
        import team as roles
        only_pi=roles.only_pi(state.get('selected',[paths['profile']]))
        command=role_commands('review',review,profiles,only_pi=only_pi)[0] if review else ''
        message=('Con política de contexto activa en papel Jefe usa solo índice ECC y delegación nativa; las lecturas/ejecuciones van a obreros. '
                 'Equipo activo: tú diriges y los modelos del equipo trabajan, también dentro de los pasos de Superpowers. '
                 +('Solo Pi: los modelos de Nous trabajan sin sandbox ni aprobación por herramienta; compruébalo todo en el diff. ' if only_pi else '')+
                 'Haz tú mismo solo preguntas, lecturas y cambios de una o dos líneas sin lógica nueva. '
                 'Cualquier otro cambio de código: encarga Programar al modelo de ese papel, salvo que sea tu propio modelo. '
                 'Antes de decir que un cambio de código está terminado, encarga siempre Revisar al modelo de ese papel y corrige lo que encuentre'
                 +(': '+command if command else '')+'. '
                 'Si tu sandbox o tus permisos impiden lanzar un encargo, pide el permiso nativo; si no es posible, di claramente que ese papel no se hizo con su modelo: un ayudante interno de tu CLI no cuenta como revisión independiente. '
                 'Lee la skill '+ID+' en '+json.dumps(str(paths['source']/'SKILL.md'),ensure_ascii=False)
                 +' para los demás comandos, las alternativas si un modelo no responde y las reglas. Conserva los permisos nativos; nunca de pago sin consentimiento.')
    return native_plugins.instruction_block(original,'team',message if enabled else None)


def digest(paths,settings_file,*,team=None):
    value=hashlib.sha256(text(paths,settings_file,team=team).encode())
    for name in ('native_team.py','team.py','native_skills.py','native_pi.py','native_launcher.py','context_policy.py','context_state.py','context_run.py'):
        value.update(Path(__file__).with_name(name).read_bytes())
    return value.hexdigest()


def inspect(agent,receipt=None,*,settings_file=None,team=None):
    receipt=receipt or {};paths=layout(agent)
    config=shared_memory.local_config(agent);startup=shared_memory.startup_text(agent,config)
    block=native_plugins.instruction_block(startup,'team')!=startup
    enabled=owned(paths) or block
    foreign=(paths['target'].exists() or paths['target'].is_symlink()) and not owned(paths)
    ready=bool(settings_file and owned(paths) and (paths['source']/'SKILL.md').is_file()
               and (paths['source']/'SKILL.md').read_text()==text(paths,settings_file,team=team)
               and startup==instruction(startup,paths,True,team=team))
    verified=ready and receipt.get('digest')==digest(paths,settings_file,team=team) and receipt.get('home')==str(paths['home'])
    return {'id':ID,'installed':(paths['source']/'SKILL.md').is_file(),'enabled':enabled,'verified':bool(verified),
            'version':'1' if ready else '', 'detail':tr('Skill nativa comprobada; los permisos siguen en cada CLI.') if verified else
            tr('Hay una skill ajena; se conserva sin sobrescribir.') if foreign else tr('Conexión de equipo pendiente.') if enabled else tr('Desactivado; instalación y datos conservados.')}


def prepare(paths,settings_file,*,team=None):
    native_skills.publish(paths['source'],text(paths,settings_file,team=team))


def guard_hook_trusted(home):
    """Codex skips a hook until the user approves it with /hooks; every hook in the file needs its approval."""
    try:
        hooks=json.loads((home/'hooks.json').read_text()).get('hooks',{})
        total=sum(len(entry.get('hooks',[])) for entries in hooks.values() for entry in entries)
    except (OSError,ValueError,AttributeError,TypeError):return True  # Nothing installed that could be approved.
    if not total:return True
    try:states=tomllib.loads((home/'config.toml').read_text()).get('hooks',{}).get('state',{})
    except (OSError,ValueError):return False
    return sum(key.startswith(str(home/'hooks.json')) and 'trusted_hash' in value for key,value in states.items())>=total


def set_guard_hook(file,settings_file,enabled,*,agent='claude',home=None):
    """Add or remove only our hook in the CLI's settings; the script reads the limit at run time."""
    if not enabled and not file.exists():return
    invalid=native_plugins.PluginError(tr('La configuración de Claude no es válida; se conserva.'))
    if file.is_symlink():raise invalid
    try:original=json.loads(memory_archive.read_file(file,1024*1024)) if file.exists() else {}
    except (OSError,ValueError,native_plugins.PluginError) as error:raise invalid from error
    if not isinstance(original,dict) or not isinstance(original.get('hooks',{}),dict):raise invalid
    if any(not isinstance(original.get('hooks',{}).get(event,[]),list) for event in ('PostToolUse','UserPromptSubmit')):raise invalid
    script=Path(__file__).with_name('usage_guard.py')
    # Foreign entries are kept as they are, whatever their shape.
    ours=lambda entry:isinstance(entry,dict) and isinstance(entry.get('hooks'),list) and any(isinstance(hook,dict) and script.name in str(hook.get('command','')) for hook in entry['hooks'])
    # Always exit 0: in a prompt hook a failure would block the user, for example once the plugin is gone.
    command='[ -f '+shlex.quote(str(script))+' ] && '+shlex.join(['/usr/bin/python3','-B',str(script),'--settings',str(settings_file),
                                                                 *(['--agent','codex','--codex-home',str(home)] if agent=='codex' else [])])+' || true'
    hooks=dict(original.get('hooks',{}))
    for event in ('PostToolUse','UserPromptSubmit'):
        kept=[entry for entry in hooks.get(event,[]) if not ours(entry)]
        if enabled:kept.append({'hooks':[{'type':'command','command':command,'timeout':15}]})
        if kept:hooks[event]=kept
        else:hooks.pop(event,None)
    updated={**original,'hooks':hooks}
    if not hooks:updated.pop('hooks')
    if updated==original:return
    mode=stat.S_IMODE(file.stat().st_mode) if file.exists() else 0o600
    file.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    atomic_write(file,json.dumps(updated,ensure_ascii=False,indent=2)+'\n')
    file.chmod(mode)


def apply(agent,binary,action,receipt=None,*,settings_file=None,team=None):
    if action not in ('enable','disable','verify') or not settings_file:
        raise native_plugins.PluginError(tr('Falta una operación o configuración válida de equipo.'))
    paths=layout(agent);receipt=receipt or {}
    enabled=action=='enable' if action!='verify' else inspect(agent,receipt,settings_file=settings_file,team=team)['enabled']
    if enabled:
        if ((paths['target'].exists() or paths['target'].is_symlink()) and not owned(paths)) or paths['target'].parent.is_symlink():
            raise native_plugins.PluginError(tr('El destino de skills es ajeno o un enlace; se conserva.'))
        prepare(paths,settings_file,team=team);paths['target'].parent.mkdir(parents=True,mode=0o700,exist_ok=True)
        if not owned(paths):paths['target'].symlink_to(paths['source'],target_is_directory=True)
    if agent in ('claude','codex') and paths['profile']!='claude-2':
        # claude-2 has its own account limits; the shared usage reading would mix them up, so it gets no guard hook.
        # Before anything else changes. A broken file must not stop the team from being turned off.
        try:set_guard_hook(paths['home']/('settings.json' if agent=='claude' else 'hooks.json'),settings_file,enabled,agent=agent,home=paths['home'])
        except native_plugins.PluginError:
            if enabled:raise
    config=shared_memory.local_config(agent);original=shared_memory.startup_text(agent,config)
    updated=instruction(original,paths,enabled,team=team)
    if agent=='codex':
        file,layer=native_plugins.codex_user_layer(binary)
        updated=instruction(layer['config'].get('developer_instructions',''),paths,enabled,team=team)
        edits=[{'keyPath':'developer_instructions','value':updated or None,'mergeStrategy':'replace'}] if updated!=layer['config'].get('developer_instructions','') else []
        if enabled and layer['config'].get('features',{}).get('hooks') is not True:
            edits.append({'keyPath':'features.hooks','value':True,'mergeStrategy':'replace'})
        if edits:
            native_plugins.codex_request(binary,'config/batchWrite',{'filePath':file,'expectedVersion':layer['version'],'reloadUserConfig':False,'edits':edits})
    elif agent=="pi":
        import native_pi
        native_pi.write_startup(original,updated)
    else:shared_memory.write_claude_startup(original,updated)
    if not enabled and owned(paths):paths['target'].unlink()
    if enabled and not native_skills.loaded(agent,binary,paths['source'],ID):
        raise native_plugins.PluginError(tr('La CLI no confirmó nuestra skill nativa; sigue pendiente.'))
    result={'id':ID,'home':str(paths['home']),'version':'1','digest':digest(paths,settings_file,team=team),'checkedAt':datetime.now(timezone.utc).isoformat()}
    state=inspect(agent,result,settings_file=settings_file,team=team)
    if state['enabled']!=enabled or (enabled and not state['verified']):
        raise native_plugins.PluginError(tr('El estado nativo de equipo no coincide; vuelve a comprobar.'))
    return result


def codex_command(row, profile, sandbox):
    command = ['codex', 'exec', '-C', '<dir>', '-s', sandbox, '-m', row['model']]
    if row['effort']: command += ['-c', 'model_reasoning_effort='+json.dumps(row['effort'])]
    return 'CODEX_HOME='+shlex.quote(str(Path.home()/('.codex-2' if profile=='codex-2' else '.codex')))+' '+shlex.join([*command, '<encargo>'])+' </dev/null'


def claude_command(row, mode, profile='claude'):
    # The main profile clears the variable: a session running in claude-2 would otherwise pass its own account on.
    env='CLAUDE_CONFIG_DIR='+shlex.quote(str(Path.home()/'.claude-2'))+' ' if profile=='claude-2' else 'env -u CLAUDE_CONFIG_DIR '
    return 'cd '+shlex.quote('<dir>')+' && '+env+shlex.join(['claude', '-p', '<encargo>', '--model', row['model'], '--effort', row['effort'], '--permission-mode', mode])+' </dev/null'


def claude_profiles(profiles):
    return [profile for profile in ('claude-2', 'claude') if profile in profiles] or ['claude']


def codex_profiles(profiles):
    return [profile for profile in ('codex-2', 'codex') if profile in profiles] or ['codex']


def role_commands(key, row, profiles, *, settings_file=None, only_pi=False):
    # A visible recipe, never a background dispatcher. Resolve current settings at execution.
    def wrapped(profile, arguments):
        command = ['/usr/bin/python3', str(Path(__file__).with_name('context_run.py').resolve()),
                   *(['--settings', str(settings_file)] if settings_file else []), '--agent', profile, '--role', key, '--cwd', '<dir>', '--', *arguments]
        return shlex.join(command)+' </dev/null'
    if row['agent'] == 'codex':
        sandbox = 'read-only' if key in ('plan', 'review') else 'workspace-write'
        effort = ['-c', 'model_reasoning_effort='+json.dumps(row['effort'])] if row['effort'] else []
        return ['CODEX_HOME='+shlex.quote(str(Path.home()/('.codex-2' if profile == 'codex-2' else '.codex')))+' '+
                wrapped(profile, ['codex', 'exec', '-C', '<dir>', '-s', sandbox, '-m', row['model'], *effort, '<encargo>']) for profile in codex_profiles(profiles)]
    if row['agent'] == 'claude':
        # The main profile clears the variable: a session running in claude-2 would otherwise pass its own account on.
        return [('CLAUDE_CONFIG_DIR='+shlex.quote(str(Path.home()/'.claude-2'))+' ' if profile == 'claude-2' else 'env -u CLAUDE_CONFIG_DIR ')+
                wrapped(profile, ['claude', '-p', '<encargo>', '--model', row['model'], '--effort', row['effort'],
                                   '--permission-mode', 'plan' if key in ('plan', 'review') else 'acceptEdits']) for profile in claude_profiles(profiles)]
    if only_pi or key in ('review', 'simple'):
        provider, model = row['model'].split('/', 1)
        thinking = ['--thinking', row['effort']] if row.get('effort') else []
        return [wrapped('pi', ['pi', '-p', '--provider', provider, '--model', model, *thinking, '<encargo>'])]

    return ['# Pi: encargo pendiente; requiere otra CLI para este papel.']


def text(paths, settings_file, team=None):
    import team as roles
    state = team or {}; assignments = state.get('roles', {})
    selected = state.get('selected', [paths['profile']])
    profiles = [profile for profile in ('codex-2', 'codex', 'claude-2', 'claude') if profile in selected]
    only_pi = roles.only_pi(selected)
    lines = ['---', 'name: '+ID,
             'description: "Úsala siempre que vayas a cambiar código: encarga programar y revisar a los modelos del equipo, y planificar o tareas sencillas cuando compense."',
             '---', '', '# Equipo por papeles', '']
    for key in roles.ROLES:
        row = assignments.get(key)
        lines.append(roles.LABELS[key]+': '+(' · '.join(filter(None, (row['agent'], row['model'], row['effort']))) if row else 'Pendiente; no hay modelo disponible.'))
    lines.extend(['', *(saved_message(warning, 'es') for warning in state.get('warnings', [])), '',
        'Con política de contexto activa, una instancia Jefe se limita al índice ECC y a la delegación nativa; los obreros leen y ejecutan. '
        'Las recetas context_run.py resuelven preferencias actuales por papel y excepción motivada, sin cambiar perfiles. '
        'Entregan solo el informe final status/references/tests/error/pending, acotado; los detalles extensos van por puntero ECC verificado. '
        'Sin política se conserva la salida nativa. Umbral, piso, fase segura y presupuesto no son garantías duras. '
        'La sesión habitual dirige. Si el papel corresponde a tu propia CLI y modelo, hazlo tú mismo sin lanzarte en headless. '
        'Haz tú mismo solo preguntas, lecturas y cambios de una o dos líneas sin lógica nueva; todo lo demás se encarga. Respeta Superpowers como única metodología: sus pasos se reparten con estos papeles. '
        'Un encargo que no responde no se espera ni se repite sin fin: intenta lanzarlo una sola vez con `timeout 60` si estás en un sandbox que puede impedirlo; '
        'si no responde, sigue con lo que puedas hacer tú, di qué papel quedó pendiente y termina. '
        'Si el sandbox o los permisos impiden lanzar un encargo, pide el permiso nativo; si no es posible, informa de que ese papel no se hizo con su modelo. Un ayudante interno de tu CLI no cuenta como revisión independiente. '
        'Cada encargo será autocontenido: objetivo, archivos, criterio de aceptación, «no hagas commits» y «no encargues nada a otros modelos». '
        'Si tú recibiste un encargo así, hazlo y devuelve el resultado: revisar y repartir corresponde a la sesión que dirige. '
        'Revisa los resultados y ejecuta pruebas antes de darlos por buenos. La revisión corresponde al modelo de Revisar. '
        'Usa un worktree Git por escritor paralelo. Los encargos no dejan archivos sueltos: temporales con `mktemp -d`, borrados al terminar, nunca registros en `/tmp` compartido. Con memoria compartida activa, guarda allí el plan y las decisiones para el traspaso; '
        'no captures transcripciones. Informa qué modelo hizo qué y tokens/coste solo si la CLI los reporta. '
        'Modelos nunca de pago sin consentimiento explícito vigente. No cambies modelos o permisos de las sesiones abiertas.',
        'Codex-2 primero; solo ante un error real de cuota usa Codex si también está seleccionado. '
        'Si ambas cuentas agotan cuota, avisa y usa otro recomendado permitido; si no existe, pide una decisión. '
        'No inventes cuotas ni actives un proveedor de pago.',
        *(['Claude Code-2 primero; solo ante un error real de cuota usa Claude Code si también está seleccionado. '
           'Si ambas cuentas agotan cuota, avisa y usa otro recomendado permitido; si no existe, pide una decisión.'] if 'claude-2' in selected else []),
        'Si el modelo de un papel no responde, reintenta una vez un fallo transitorio (conexión, tiempo agotado o saturación). '
        'Si sigue fallando, o es cuota, modelo no encontrado o configuración pendiente, usa la siguiente alternativa de ese papel, en orden. '
        'Ante permisos denegados o un encargo inválido no cambies de modelo: corrígelo o avisa. '
        'Una revisión nunca la hace el modelo que escribió el cambio ni un modelo de la familia que planificó, salvo que solo haya una familia disponible: salta esa alternativa. '
        'Informa qué modelo hizo cada papel y por qué se cambió. Si no queda alternativa, déjalo pendiente y dilo; no lo des por hecho.',
        ('Solo Pi: los modelos de Nous cubren todos los papeles y no tienen sandbox ni aprobación por herramienta, así que pueden cambiar archivos o ejecutar comandos sin preguntar. '
         'Pedir a Revisar que solo lea es una instrucción, no es aislamiento efectivo: trabaja en un worktree Git y comprueba el diff antes de aceptar el resultado. '
         'Avisa de este límite al informar de cada encargo.' if only_pi else
         'Pi no tiene sandbox ni aprobación por herramienta: solo lectura para Revisar o Tareas sencillas, '
         'pero pedirlo es una instrucción, no aislamiento efectivo: si Pi revisa, hazlo en un worktree Git y comprueba el diff después; '
         'no delegues Planificar ni Programar a Pi. Si no puedes garantizar una tarea de lectura, informa del límite.'),
        '', 'Sustituye <dir> y <encargo> por valores concretos citados como argumentos de shell. '
        'Los comandos siguientes son recetas para la sesión; el plugin nunca los ejecuta.', ''])
    for key in roles.ROLES:
        row = assignments.get(key)
        if not row: continue
        lines += [roles.LABELS[key]+':', '```sh']
        lines += role_commands(key, row, profiles, settings_file=settings_file, only_pi=only_pi)
        lines += ['```', '']
        options = roles.alternatives(key, assignments, state.get('options', []))
        if options:
            lines += ['Alternativas de '+roles.LABELS[key]+', en orden:', '```sh']
            for number, option in enumerate(options, 1):
                # One model can take one line per Codex account: the number marks the alternative.
                # The leading CLI's own family cannot give an independent review of what it wrote.
                mark = ' (no independiente: es la familia de la CLI que dirige esta sesión)' if key == 'review' and option['agent'] == paths['profile'].removesuffix('-2') else ''
                lines += ['# '+str(number)+': '+option['model']+mark, *role_commands(key, option, profiles, settings_file=settings_file, only_pi=only_pi)]
            lines += ['```', '']
        else:
            lines += [roles.LABELS[key]+' no tiene alternativa permitida: si su modelo falla, queda pendiente; dilo.', '']
    return '\n'.join(lines)+'\n'
