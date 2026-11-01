#!/usr/bin/env python3
"""Per-invocation ECC adapter. No vault parsing, transcripts or semantic summarizer."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time
from datetime import datetime
import uuid

from i18n import tr
import atomic_files
import context_policy
import shared_memory

MAX_INPUT = 64 * 1024  # Transport safety bound, not a model context recommendation.
FIELDS = ('plan', 'decisions', 'files', 'errors', 'pending')
# Stable machine protocol values, independent of the interface language.
SAVED, INCOMPLETE, READY, NO_PROJECT, FAILED, PENDING, UNKNOWN, WORKER = ('saved', 'incomplete', 'ready', 'no_project', 'failed', 'pending', 'unknown', 'worker')


def instance(agent, role, cwd, policy):
    return {'id': str(uuid.uuid4()), 'agent': agent, 'role': role,
            'cwd': str(Path(cwd).resolve(strict=True)), 'policy': policy}


class ECC:
    """Use the existing project-selecting official MCP, with a frozen launch cwd."""
    def __init__(self, capsule, *, runtime=None):
        self.instance = capsule
        self.runtime = runtime if runtime is not None else shared_memory.inspect_runtime()
        if not self.runtime.get('installed') or not self.runtime.get('node'):
            raise ValueError(tr('ECC no está disponible; checkpoint e índice pendientes.'))

    def call(self, name, arguments):
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-11-25', 'clientInfo': {'name': 'ai-team-context', 'version': '1'}, 'capabilities': {}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}]
        agent = 'codex' if self.instance['agent'] == 'codex-2' else self.instance['agent']
        result = subprocess.run(shared_memory.mcp_command(self.runtime, agent), cwd=self.instance['cwd'],
                                input=''.join(json.dumps(m)+'\n' for m in messages),
                                capture_output=True, text=True, timeout=15)
        replies = {r['id']: r for line in result.stdout.splitlines() if 'id' in (r := json.loads(line))}
        for key in (1, 2):
            reply = replies.get(key, {})
            if reply.get('error'):
                raise ValueError(reply['error']['message'])
            if reply.get('result', {}).get('isError'):
                raise ValueError('\n'.join(c['text'] for c in reply['result'].get('content', []) if c.get('type') == 'text'))
            if 'result' not in reply or reply.get('jsonrpc') != '2.0':
                raise ValueError(tr('ECC no completó el protocolo MCP; estado pendiente.'))
        if result.returncode:
            raise ValueError(tr('ECC no completó el protocolo MCP; estado pendiente.'))
        return json.loads(replies[2]['result']['content'][0]['text'])


def validate_state(state):
    if (not isinstance(state, dict) or not set(FIELDS).issubset(state)
            or set(state) - {*FIELDS, 'safe_boundary', 'debug'}
            or any(not isinstance(state[k], list) or any(not isinstance(x, str) for x in state[k]) for k in FIELDS)
            or not state['plan']
            or any(type(state[k]) is not bool for k in ('safe_boundary', 'debug') if k in state)
            or len(json.dumps(state).encode()) > MAX_INPUT):
        raise ValueError(tr('Checkpoint incompleto o inválido: usa plan, decisions, files, errors y pending como listas.'))
    return state


def _project(client):
    report = client.call('memory_doctor', {'scopes': ['project']})
    if not report.get('activeProject'):
        raise ValueError(tr('La sesión no tiene proyecto; checkpoint pendiente.'))
    return report['activeProject']


def _save(client, capsule, kind, body, links=()):
    serialized = json.dumps(body, ensure_ascii=False, sort_keys=True)
    saved = client.call('memory_save', {'title': str(tr('Contexto {kind} {instance}', kind=kind, instance=capsule['id'])), 'body': serialized,
        'kind': 'handoff', 'scope': 'project', 'targetHarnesses': ['all', 'project'],
        'tags': ['context-'+kind, capsule['id']], 'links': list(links)})['memory']
    read = client.call('memory_read', {'id': saved['id'], 'scope': 'project'})['memory']
    if read.get('id') != saved['id'] or read.get('body') != serialized or read.get('tags') != saved['tags']:
        raise ValueError(tr('ECC_CHECKPOINT_VERIFICATION_FAILED: la memoria releída no coincide con la guardada.'))
    _record_handoff(capsule['id'], kind, saved['id'], serialized)
    return saved['id']


def checkpoint(client, capsule, state):
    validate_state(state)
    _project(client)
    key = _save(client, capsule, 'checkpoint', {'schema': 1, 'instance': capsule['id'], 'cwd': capsule['cwd'], 'state': state})
    return {'status': SAVED, 'id': key, 'instance': capsule['id']}


def precompact(client, capsule):
    _project(client)
    rows = client.call('memory_search', {'query': capsule['id'], 'scopes': ['project'], 'kinds': ['handoff'], 'limit': 100})['results']
    memories = [r['memory'] for r in rows if capsule['id'] in r['memory'].get('tags', [])]
    checkpoints = [m for m in memories if 'context-checkpoint' in m.get('tags', [])]
    checkpoints.sort(key=lambda m: (m.get('createdAt', ''), m['id']), reverse=True)
    body = {'instance': capsule['id'], 'status': INCOMPLETE, 'error': tr('CHECKPOINT_MISSING: falta un checkpoint de esta instancia.'), 'checkpoint': None}
    links = []
    if checkpoints:
        latest = checkpoints[0]
        links = [latest['id']]
        body.update(checkpoint=latest['id'], error=tr('CHECKPOINT_STALE: el checkpoint ya fue usado por otro evento precompact.'))
        if not any(latest['id'] in m.get('links', []) for m in memories if 'context-precompact' in m.get('tags', [])):
            try:
                stored = json.loads(client.call('memory_read', {'id': latest['id'], 'scope': 'project'})['memory']['body'])
                validate_state(stored['state'])
                if stored['instance'] != capsule['id'] or stored['cwd'] != capsule['cwd'] or stored['schema'] != 1:
                    raise ValueError(tr('El checkpoint pertenece a otra instancia o carpeta.'))
                body.update(status=SAVED, error=None)
            except (ValueError, KeyError, TypeError) as error:
                body['error'] = str(tr('CHECKPOINT_INVALID: {error}', error=error))
    # Even missing/stale state produces an honest event, never a fabricated summary.
    key = _save(client, capsule, 'precompact', body, links)
    return {**body, 'id': key}


# --- Idle cold-cache advice: a UserPromptSubmit hook that only ever adds one line of context. ---
IDLE_FILE, IDLE_SCHEMA = 'last_turn.json', 1


def _now():
    return int(time.time())


def _idle_folder(capsule_id, *, create):
    folder = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    for part in ('omarchy-ai-team', 'context-usage', str(uuid.UUID(capsule_id))):
        folder = folder / part
        if create:
            folder.mkdir(mode=0o700, exist_ok=True)
    return folder


def _read_state(path, *, limit=1024):
    """Parsed JSON of a small private state file, or None; never blocks on FIFOs/devices, never follows symlinks."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        with os.fdopen(descriptor, 'rb', closefd=False) as source:
            return json.loads(source.read(limit + 1))
    except (OSError, ValueError):
        return None
    finally:
        os.close(descriptor)


def _last_turn(capsule_id):
    """Epoch seconds of the previous prompt, or None; only a number is ever stored (no prompt, no transcript)."""
    try:
        data = _read_state(_idle_folder(capsule_id, create=False) / IDLE_FILE)
    except ValueError:
        return None
    last = data.get('last_turn') if isinstance(data, dict) and data.get('schema') == IDLE_SCHEMA else None
    return last if type(last) is int and last >= 0 else None


def _record_handoff(capsule_id, kind, memory_id, serialized):
    """Remember which memory _save wrote and its sha256, so a later re-read can be proven identical. No content stored."""
    try:
        atomic_files.atomic_write(_idle_folder(capsule_id, create=True) / f'handoff_{kind}.json', json.dumps(
            {'schema': IDLE_SCHEMA, 'id': memory_id, 'sha256': hashlib.sha256(serialized.encode()).hexdigest()}))
    except (OSError, ValueError):
        pass  # Without a record the handoff simply never counts as verified.


def _recorded_handoff(capsule_id, kind):
    try:
        data = _read_state(_idle_folder(capsule_id, create=False) / f'handoff_{kind}.json')
    except ValueError:
        return None
    if (isinstance(data, dict) and data.get('schema') == IDLE_SCHEMA and isinstance(data.get('id'), str)
            and isinstance(data.get('sha256'), str)):
        return data['id'], data['sha256']
    return None


def _record_turn(capsule_id, now):
    atomic_files.atomic_write(_idle_folder(capsule_id, create=True) / IDLE_FILE,
                              json.dumps({'schema': IDLE_SCHEMA, 'last_turn': now}))


def _seconds(text):
    try:
        return datetime.fromisoformat(str(text).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def _verified_handoff(client, capsule, since):
    """Id of the checkpoint this instance saved, only if ECC returns exactly the body whose digest _save recorded."""
    recorded = _recorded_handoff(capsule['id'], 'checkpoint')
    if recorded is None:
        return None
    memory_id, digest = recorded
    _project(client)
    read = client.call('memory_read', {'id': memory_id, 'scope': 'project'})['memory']
    body = read['body']
    if read.get('id') != memory_id or hashlib.sha256(body.encode()).hexdigest() != digest:
        return None
    stored = json.loads(body)
    validate_state(stored['state'])
    if (stored['schema'] != 1 or stored['instance'] != capsule['id'] or stored['cwd'] != capsule['cwd']
            or capsule['id'] not in read.get('tags', []) or 'context-checkpoint' not in read.get('tags', [])):
        return None
    created = _seconds(read.get('createdAt'))
    # Without a valid date freshness cannot be proven, so it does not count as verified.
    return None if created is None or created < since else memory_id


def _idle_notice(minutes, ttl, tokens, floor, handoff):
    size = (f"contexto de unos {tokens} tokens" if tokens is not None else
            f"tokens desconocidos (el hook nativo no los informa; piso {floor})")
    head = (f"Aviso de caché fría: {minutes} min sin actividad (TTL {ttl} min); la caché del prompt probablemente caducó "
            f"y el próximo turno reenvía todo el contexto: {size}. ")
    if handoff:
        brief = shlex.join([sys.executable, str(Path(__file__).resolve()), 'brief'])
        return head + (f"Existe un relevo ECC verificado de esta instancia ({handoff}): conviene /clear y cargar el brief con "
                       f"`{brief}`. Díselo al usuario; no se ha limpiado nada y no se limpiará solo.")
    return head + ("NO hay relevo verificado de esta instancia: NO conviene limpiar todavía. Guarda antes un relevo con el "
                   "comando de checkpoint y comprueba status=saved.")


def idle(capsule, *, now=None, tokens=None, client=None):
    """Record this turn and return advice if the cache went cold on a big context; never acts on the session."""
    policy = capsule['policy']
    if not _flag(policy, 'suggest_resume_after_idle'):
        return None
    now = _now() if now is None else now
    previous = _last_turn(capsule['id'])
    _record_turn(capsule['id'], now)
    ttl, floor = _flag(policy, 'idle_cache_ttl_minutes'), _flag(policy, 'compact_floor_tokens')
    if previous is None or now - previous <= ttl * 60 or (tokens is not None and tokens < floor):
        return None
    try:
        handoff = _verified_handoff(client or ECC(capsule), capsule, previous)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        handoff = None  # Any ECC doubt means no verified handoff.
    return _idle_notice((now - previous) // 60, ttl, tokens, floor, handoff)


def _title(text):
    # Metadata remains untrusted data, rendered as JSON/plain text only.
    return re.sub(r'[\x00-\x1f\x7f]', ' ', re.sub(r'<[^>]*>', '', str(text)))[:160]


def brief(client):
    project = client.call('memory_doctor', {'scopes': ['project']}).get('activeProject')
    result = {'project': project, 'status': READY if project else NO_PROJECT,
              'untrusted_metadata': True, 'pointers': [], 'errors': []}
    if not project:
        return result
    for scope in (None, 'compartido'):
        args = {'query': '', 'scopes': ['project'], 'limit': 8}
        if scope:
            args['project'] = scope
        try:
            found = client.call('memory_search', args)
            result['pointers'].extend({'project': scope or project, 'id': r['memory']['id'],
                'title': _title(r['memory']['title']), 'status': r['memory'].get('status', UNKNOWN)}
                for r in found['results'][:8])
        except ValueError as error:
            result['status'] = PENDING; result['errors'].append(str(error))
    return result


def _flag(policy, key):
    """Capsules frozen before a setting existed behave with its recommended default."""
    return policy.get(key, context_policy.DEFAULTS[key])


def _body(capsule, stable):
    p = capsule['policy']
    # Stable text never holds the UUID or cwd, so equal policy+role share one cacheable prefix.
    head = f"Política de contexto del papel {capsule['role']}" + ('' if stable else f", instancia {capsule['id']}")
    checkpoint = ("con el comando de checkpoint de tu instancia, al final de estas instrucciones"
                  if stable else "por stdin: " + _checkpoint_command(capsule))
    text = (head + ". "
        f"Umbral recomendado {p['compact_at_tokens']}; piso recomendado {p['compact_floor_tokens']}; "
        f"informe final recomendado <= {p['worker_report_max_tokens']} tokens. "
        "Todos los números se ajustan por configuración/papel o excepción explícita de ejecución con motivo; apply=launch y no globalconfig son fijos. "
        "Antes de compactar, escribe un checkpoint estructurado explícito " + checkpoint +
        ". JSON con plan, decisions, files, errors, pending (listas de texto); safe_boundary/debug opcionales booleanos. "
        "ECC guarda y relee el estado; comprueba status=saved. Sin checkpoint fresco o si falla ECC, declara incompleto/fallo y solicita aplazar cuando el control nativo lo permita. "
        "Retén plan, decisiones, archivos con líneas, errores, pendientes y punteros ECC; descarta herramientas crudas, logs y cuerpos ya referenciados. "
        "Piso, fase segura y fronteras son cooperativos donde no hay guarda nativa: no compactes voluntariamente bajo el piso, "
        "durante debug activo o una operación irreversible; termina la frontera segura y justifica la excepción al umbral. "
        "No hay garantía de máximo duro ni conocimiento determinista de fase/tokens. ")
    if capsule['role'] == 'jefe':
        text += ("Jefe: usa solo el índice ECC y delegación nativa para abrir obreros; no releas archivos, cuerpos completos, herramientas ni ejecutes tareas. "
                 "Recibe informes de cinco campos: status, references, tests, error, pending. Pide referencias puntuales a un obrero cuando falte evidencia. ")
    else:
        text += ("Obrero: reporta por "+', '.join(p['worker_report_on'])+", nunca herramienta por herramienta. "
                 "Salida final JSON de cinco campos string: status, references, tests, error, pending; usa referencias de archivo:línea/ECC para contenido completo. ")
    if _flag(p, 'task_plan_with_pointers'):
        if capsule['role'] in ('jefe', 'plan'):
            text += ("Cada encargo lleva objetivo, referencias archivo:línea, criterio de aceptación y una lista «no releer» "
                     "con lo ya verificado; sin pegar cuerpos de archivos ni logs. ")
        text += ("Respeta la lista «no releer» de un encargo salvo que cambian los archivos, aparezca un fallo "
                 "o la evidencia insuficiente lo exija; entonces relee solo lo necesario y dilo. ")
    if capsule['role'] != 'jefe' and _flag(p, 'bounded_reads'):
        text += (f"Lee en ventanas de {_flag(p, 'read_window_lines')} líneas (rg, ast-grep, jq o rangos de líneas) "
                 "antes que archivos enteros; amplía solo con motivo. ")
    grouping = capsule['role'] in ('jefe', 'plan') and _flag(p, 'group_related_subtasks')
    if grouping:
        text += ("Agrupa las subtareas relacionadas (mismos archivos) en UN obrero y abre otro solo al cambiar de fase "
                 "(por ejemplo de implementar a revisar): abrir un obrero por cada paso repite lecturas y encarece la sesión. ")
    if capsule['role'] == 'jefe' and _flag(p, 'fresh_worker_per_subtask'):
        text += ("Abre un obrero nuevo por grupo de subtareas, con encargo autocontenido; no reanudes ni heredes su conversación. "
                 if grouping else
                 "Abre un obrero nuevo por subtarea, con encargo autocontenido; no reanudes ni heredes su conversación. ")
    if _flag(p, 'explore_with_simple_role'):
        text += ("Exploración: devuelve solo punteros archivo:línea, sin contenido de archivos. "
                 if capsule['role'] == 'simple' else
                 "Delega la exploración, lectura y búsquedas al papel Tareas sencillas, que devuelve solo punteros "
                 "archivo:línea, sin contenido; lee después únicamente esos rangos. ")
    if _flag(p, 'handoff_on_pause'):
        text += ("Al terminar un bloque de trabajo o quedar esperando al usuario, guarda un relevo estructurado con el comando de "
                 "checkpoint y comprueba status=saved; si ECC falla, dilo y no afirmes que existe relevo. ")
    if _flag(p, 'clear_between_tasks_below_floor'):
        text += (f"Al cambiar de tarea con el contexto bajo el piso ({p['compact_floor_tokens']} tokens), guarda el relevo y "
                 "sugiere /clear o su equivalente nativo comprobado; no lo envíes automáticamente. "
                 "Si los tokens son desconocidos, informa la incertidumbre. ")
    if not p['dump_state_before']:
        text += 'Checkpoint automático previo desactivado explícitamente; no se afirma retención verificada. '
    return text


def _checkpoint_command(capsule):
    return shlex.join([sys.executable, str(Path(__file__).resolve()), 'checkpoint', '--instance', json.dumps(capsule)])


def stable_instructions(capsule):
    return _body(capsule, True)


def _stable(capsule):
    return _flag(capsule['policy'], 'stable_prompt_prefix')


def instructions(capsule):
    if not _stable(capsule):
        return _body(capsule, False)
    return (stable_instructions(capsule) + f"Instancia {capsule['id']}, carpeta {capsule['cwd']}. "
            "Comando de checkpoint: " + _checkpoint_command(capsule))


def launch_options(capsule):
    serialized = json.dumps(capsule, ensure_ascii=False)
    script = str(Path(__file__).resolve())
    hooks = {}
    events = {'SessionStart': 'start'}
    if capsule['policy']['dump_state_before']:
        events['PreCompact'] = 'precompact'
    if _flag(capsule['policy'], 'suggest_resume_after_idle'):
        events['UserPromptSubmit'] = 'idle'
    for event, action in events.items():
        command = shlex.join([sys.executable, script, action, '--instance', serialized])
        hooks[event] = [{'matcher': 'manual|auto' if event == 'PreCompact' else '',
                         'hooks': [{'type': 'command', 'command': command, 'timeout': 90}]}]
    env = ['AI_TEAM_CONTEXT='+serialized]
    if capsule['agent'] == 'claude':
        args = ['--settings', json.dumps({'hooks': hooks}), '--append-system-prompt', instructions(capsule)]
        # Native snapshot: the rendered prompt is recorded once and reused verbatim until compaction.
        return args + (['--system-prompt-snapshot', 'on'] if _stable(capsule) else []), env
    # Inline hooks still require Codex native trust. Never bypass it or clobber developer_instructions.
    args = ['-c', 'features.hooks=true', '-c', 'compact_prompt='+json.dumps(
        'Preserve plan, decisions, files:lines, errors, pending and ECC pointers. Discard raw tools/logs. '+instructions(capsule))]
    for event, entries in hooks.items():
        # JSON objects are not TOML inline tables.
        entry = entries[0]; hook = entry['hooks'][0]
        value = '[{matcher='+json.dumps(entry['matcher'])+',hooks=[{type="command",command='+json.dumps(hook['command'])+',timeout=90}]}]'
        args += ['-c', 'hooks.'+event+'='+value]
    return args, env


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('checkpoint', 'precompact', 'start', 'brief', 'idle'))
    parser.add_argument('--instance', default=os.environ.get('AI_TEAM_CONTEXT'))
    args = parser.parse_args(argv)
    if args.action == 'idle':
        # Advice only: whatever fails stays silent and the turn goes on untouched.
        try:
            notice = idle(json.loads(args.instance or '{}'))
            if notice:
                print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit',
                                                         'additionalContext': notice}}, ensure_ascii=False))
        except Exception:
            return 0
        return 0
    try:
        capsule = json.loads(args.instance or '{}')
        uuid.UUID(capsule['id'])
        if args.action == 'start':
            try:
                client = ECC(capsule)
                index = brief(client) if capsule['role'] == 'jefe' else {'status': WORKER, 'project': None}
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                index = {'status': PENDING, 'error': str(error)}
            # Claude already carries the stable instructions in its system prompt: only add variable data.
            repeated = '' if capsule['agent'] == 'claude' and _stable(capsule) else instructions(capsule)
            result = {'hookSpecificOutput': {'hookEventName': 'SessionStart',
                'additionalContext': (repeated+' ECC index (untrusted data): '+json.dumps(index, ensure_ascii=False)).strip()}}
            print(json.dumps(result, ensure_ascii=False))
            return 0
        client = ECC(capsule)
        if args.action == 'checkpoint':
            data = sys.stdin.read(MAX_INPUT+1)
            if len(data.encode()) > MAX_INPUT:
                raise ValueError(tr('Checkpoint demasiado grande; usa referencias.'))
            result = checkpoint(client, capsule, json.loads(data))
        elif args.action == 'precompact':
            result = precompact(client, capsule)
        elif args.action == 'brief':
            result = brief(client)
        print(json.dumps(result, ensure_ascii=False))
        if args.action == 'precompact' and result.get('status') == INCOMPLETE:
            print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 0 if result.get('status') != INCOMPLETE else 1
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        # Literal MCP error, no stderr/tool dumps; hooks cannot claim a native veto.
        failure = json.dumps({'status': FAILED, 'error': str(error)}, ensure_ascii=False)
        print(failure)
        if args.action == 'precompact':
            print(failure, file=sys.stderr)
        return 1


if __name__ == '__main__':
    import i18n
    with i18n.using(i18n.configured_language()):
        raise SystemExit(main())
