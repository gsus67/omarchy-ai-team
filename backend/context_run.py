#!/usr/bin/env python3
"""Explicit native worker recipe with a final-report transport boundary, no routing."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import context_policy
import context_state
from i18n import tr
import atomic_files
import native_launcher
import role_usage

REPORT_FIELDS = ('status', 'references', 'tests', 'error', 'pending')
SCHEMA = {'type': 'object', 'properties': {k: {'type': 'string'} for k in REPORT_FIELDS},
          'required': list(REPORT_FIELDS), 'additionalProperties': False}
MAX_EVENT = 1024 * 1024  # Safety bound for a native event, not a context/token cap.


COUNTERS = ('total_input', 'cached_read', 'new_input', 'cache_creation', 'output')
SNAPSHOT_SCHEMA = 1


def _count(value):
    return value if type(value) is int and value >= 0 else None


def token_usage(agent, event):
    """Counters of one native completion event; unknown stays None, never zero.

    Codex input_tokens already include the cached ones. Claude reports new input, cache
    reads and cache creation separately, and all three are input. No cost is derived.
    """
    usage = event.get('usage') if isinstance(event, dict) else None
    if not isinstance(usage, dict):
        return None
    if agent in ('codex', 'codex-2') and event.get('type') == 'turn.completed':
        total, cached = _count(usage.get('input_tokens')), _count(usage.get('cached_input_tokens'))
        new = total - cached if total is not None and cached is not None and cached <= total else None
        return {'total_input': total, 'cached_read': cached, 'new_input': new, 'cache_creation': None,
                'output': _count(usage.get('output_tokens'))}
    if agent == 'claude' and event.get('type') == 'result':
        new, read, created = (_count(usage.get(key)) for key in
                              ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'))
        known = None not in (new, read, created)
        return {'total_input': new + read + created if known else None, 'cached_read': read, 'new_input': new,
                'cache_creation': created, 'output': _count(usage.get('output_tokens'))}
    return None


def token_delta(before, after):
    """after - before for two compatible private snapshots; a counter restart is flagged."""
    for snap in (before, after):
        if not isinstance(snap, dict) or snap.get('schema') != SNAPSHOT_SCHEMA or not isinstance(snap.get('counters'), dict):
            raise ValueError(tr('Las instantáneas de tokens no son compatibles.'))
    if before.get('agent') != after.get('agent'):
        raise ValueError(tr('Las instantáneas de tokens no son compatibles.'))
    delta, unknown, reset = {}, [], False
    for key in COUNTERS:
        old, new = _count(before['counters'].get(key)), _count(after['counters'].get(key))
        if old is None or new is None:
            delta[key] = None
            unknown.append(key)
        elif new < old:
            delta[key], reset = new, True
        else:
            delta[key] = new - old
    turns = [_count(snap.get('turns')) for snap in (before, after)]
    return {**delta, 'unknown': unknown, 'reset': reset,
            'turns': turns[1] - turns[0] if None not in turns and turns[1] >= turns[0] else None}


def _snapshot(agent, counters, turns):
    return {'schema': SNAPSHOT_SCHEMA, 'agent': agent, 'turns': turns, 'counters': counters}


def _accumulate(counters, usage):
    return {key: counters[key] + usage[key] if counters[key] is not None and usage[key] is not None else None
            for key in COUNTERS}


def _write_snapshots(capsule_id, **snapshots):
    """Private numeric files only: no prompts, tool output, stderr or transcripts."""
    folder = Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')
    folder.mkdir(parents=True, exist_ok=True)
    for part in ('omarchy-ai-team', 'context-usage', capsule_id):
        folder = folder/part
        folder.mkdir(mode=0o700, exist_ok=True)
    for name, snapshot in snapshots.items():
        atomic_files.atomic_write(folder/(name + '.json'), json.dumps(snapshot))


# Codex options that take a separate value; `--opt=value` is a single token.
_CODEX_VALUE_OPTIONS = frozenset((
    '-c', '--config', '-m', '--model', '-p', '--profile', '-s', '--sandbox', '-a', '--ask-for-approval',
    '-C', '--cd', '-i', '--image', '--add-dir', '--color', '--output-schema', '-o', '--output-last-message',
    '--enable', '--disable', '--oss-provider', '--local-provider', '--remote', '--remote-auth-token-env',
    '--thread-source', '--cyber-access-program'))


def _codex_positionals(rest):
    """Yield (index, word) of positional words, skipping options and their values."""
    index = 0
    while index < len(rest):
        word = rest[index]
        if word == '--':
            return  # everything after the delimiter is prompt text, never a subcommand
        if word in ('-i', '--image'):
            index += 1  # a list option: it also takes every following word that is not an option, as clap does
            while index < len(rest) and (not rest[index].startswith('-') or rest[index] == '-') and rest[index] != '--':
                index += 1
            continue
        if word.startswith('-') and word != '-':
            index += 2 if word in _CODEX_VALUE_OPTIONS else 1
            continue
        yield index, word
        index += 1


def _fresh_command(command, agent):
    """Fresh worker per subtask: refuse continuation and ask the CLI not to persist the session."""
    rest = list(command[1:])
    refusal = tr('Con obrero nuevo por subtarea no se permite reanudar ni bifurcar sesiones.')
    if agent in ('codex', 'codex-2'):
        # `-c key=value` is a valid Codex override; only a subcommand can continue a session.
        # Options may precede or follow `exec`, so find the subcommands by position, not by prefix.
        positions = list(_codex_positionals(rest))
        names = [word for _, word in positions]
        exec_at = next((index for index, word in positions[:1] if word in ('exec', 'e')), None)
        subcommands = names[:2] if exec_at is not None else names[:1]
        if any(item in ('resume', 'fork') for item in subcommands):
            raise ValueError(refusal)
        # Only an option before the delimiter counts: after `--` the words are prompt text.
        before_delimiter = rest[:rest.index('--')] if '--' in rest else rest
        if exec_at is not None and '--ephemeral' not in before_delimiter:
            rest.insert(exec_at + 1, '--ephemeral')
    elif agent == 'claude':
        # For Claude `-c` means --continue, unlike Codex.
        if any(item in ('--resume', '-r', '--continue', '-c', '--fork-session', '--from-pr')
               or item.startswith(('--resume=', '--from-pr=')) for item in rest):
            raise ValueError(refusal)
        if any(item in ('-p', '--print') for item in rest) and '--no-session-persistence' not in rest:
            rest.insert(0, '--no-session-persistence')
    elif agent == 'muse':
        # `resume` is a subcommand and --session-id reuses a session: both continue earlier work.
        before_delimiter = rest[:rest.index('--')] if '--' in rest else rest
        if rest[:1] == ['resume'] or any(item == '--session-id' or item.startswith('--session-id=') for item in before_delimiter):
            raise ValueError(refusal)
        if 'exec' not in before_delimiter:
            raise ValueError(tr('La receta de Muse requiere exec nativo.'))
        if '--no-session-log' not in before_delimiter:
            rest.insert(before_delimiter.index('exec') + 1, '--no-session-log')
    elif agent == 'pi':
        if any(item in ('--continue', '-c', '--resume', '-r', '--session', '--fork')
               or item.startswith(('--continue=', '--resume=', '--session=', '--fork=')) for item in rest):
            raise ValueError(refusal)
        # Anchor the flag at the front: an option that takes a value could swallow a later literal
        # '--no-session' as its argument, so presence anywhere in the list proves nothing.
        while rest[:2] == ['--no-session', '--no-session']:
            del rest[0]
        if rest[:1] != ['--no-session']:
            rest.insert(0, '--no-session')
    return [command[0], *rest]


def _check_codex_cwd(command, cwd):
    # Codex accepts options on either side of `exec` and after the prompt.
    rest = command[1:]
    selected = None
    index = 0
    while index < len(rest):
        word = rest[index]
        if word == '--':
            break
        if word == '--worktree':
            raise ValueError(tr('No se permite --worktree con la política de contexto activa.'))
        if word in ('-C', '--cd'):
            if index + 1 >= len(rest):
                raise ValueError(tr('Falta el directorio de -C/--cd.'))
            selected = rest[index + 1]
            index += 2
            continue
        if word.startswith('--cd='):
            selected = word[5:]
        elif word.startswith('-C') and word != '-C':
            selected = word[2:].removeprefix('=')
        elif word in ('-i', '--image'):
            index += 1
            while index < len(rest) and not rest[index].startswith('-'):
                index += 1
            continue
        elif word in _CODEX_VALUE_OPTIONS:
            index += 2
            continue
        index += 1
    if selected is not None and (Path(cwd) / selected).resolve() != Path(cwd).resolve():
        raise ValueError(tr('El directorio -C/--cd no coincide con --cwd.'))


# Keys the plugin injects with `-c` that a native argument must not override.
_CODEX_PROTECTED_KEYS = frozenset(('model_auto_compact_token_limit', 'features.hooks'))


def _codex_override_key(option, operand):
    """Config key set by one `-c/--config/--enable/--disable` operand, or None for other options."""
    if option in ('-c', '--config'):
        key, equals, _ = operand.partition('=')
        return key.strip() if equals else None  # without `=` Codex reports the syntax error itself
    operand = operand.strip()
    if option in ('--enable', '--disable'):
        return 'features.hooks' if operand == 'hooks' else operand
    return operand


def _codex_config_keys(rest):
    """Yield the config key changed by every native override option before the delimiter."""
    index = 0
    while index < len(rest):
        word = rest[index]
        if word == '--':
            return
        option, operand = word, None
        if word.startswith(('--config=', '--enable=', '--disable=')):
            option, _, operand = word.partition('=')
        elif word.startswith('-c') and not word.startswith('--') and word != '-c':
            option, operand = '-c', word[2:].removeprefix('=')
        elif word in ('-c', '--config', '--enable', '--disable') and index + 1 < len(rest):
            operand = rest[index + 1]
        if operand is not None:
            yield _codex_override_key(option, operand)
        index += 2 if word in _CODEX_VALUE_OPTIONS else 1


def _check_codex_context_overrides(command, injected_args):
    """Refuse a native Codex argument that overrides a key the context policy injected."""
    protected = _CODEX_PROTECTED_KEYS.intersection(_codex_config_keys(injected_args))
    for key in _codex_config_keys(command[1:]):
        # Assigning an ancestor table (`features={...}`) also discards the injected child key.
        for clave in sorted(protected):
            if key is not None and (clave == key or clave.startswith(f'{key}.')):
                raise ValueError(tr('El argumento nativo de Codex modifica {clave}, gestionada por Contexto. '
                                    'Cambia ese ajuste en Contexto o desactívalo para esta ejecución.', clave=clave))


def prepare(command, agent, role, config, cwd, *, override=None, profile=None):
    policy = context_policy.resolve(config, role, override=override)
    if policy['enabled'] and agent in ('codex', 'codex-2'):
        _check_codex_cwd(command, cwd)
    if policy['enabled'] and policy['fresh_worker_per_subtask']:
        command = _fresh_command(command, agent)
    args, assignments = native_launcher.invocation(profile or agent, config=config, role=role,
                                                  context_override=override, cwd=cwd)
    if agent in ('codex', 'codex-2'):
        _check_codex_context_overrides(command, args)
    environment = dict(os.environ)
    environment.update(item.split('=', 1) for item in assignments)
    if agent in ('codex', 'codex-2'):
        environment['CODEX_HOME'] = str(Path.home()/('.codex-2' if agent == 'codex-2' else '.codex'))
    if profile == 'claude-2':
        environment['CLAUDE_CONFIG_DIR'] = str(Path.home()/'.claude-2')
    capsule = json.loads(environment['AI_TEAM_CONTEXT']) if any(x.startswith('AI_TEAM_CONTEXT=') for x in assignments) else None
    return [command[0], *args, *command[1:]], environment, capsule


def save_report(capsule, report):
    client = context_state.ECC(capsule)
    context_state._project(client)
    # ECC's official validator/secret rejection runs before anything is persisted.
    return context_state._save(client, capsule, 'report', report)


def _failure(error):
    return dict(zip(REPORT_FIELDS, ('failed', '', '', error, str(tr('Verificación de la CLI nativa o del informe del obrero.')))))


def _report(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict) or set(value) != set(REPORT_FIELDS) or any(not isinstance(v, str) for v in value.values()):
        raise ValueError(tr('El obrero no entregó el informe final de cinco campos.'))
    return value


def _bounded(report, capsule):
    # Approximately four UTF-8 bytes/token: a transport budget, never a tokenizer claim.
    # Workers without a capsule (Muse) have no ECC handoff: default budget, no referenced report.
    budget = max(256, (capsule['policy']['worker_report_max_tokens'] if capsule else 800) * 4)
    if len(json.dumps(report, ensure_ascii=False).encode()) <= budget:
        return report
    if capsule is None:
        failure = _failure(str(tr('El informe del obrero excede el límite y no hay ECC donde guardarlo; pide uno más corto.')))
        failure['pending'] = str(tr('Repetir el encargo pidiendo un informe más corto.'))
        failure['error'] = failure['error'].encode()[:budget].decode(errors='ignore')
        while len(json.dumps(failure, ensure_ascii=False).encode()) > budget:
            failure['error'] = failure['error'][:-1]
        return failure
    try:
        key = save_report(capsule, report)
        return dict(zip(REPORT_FIELDS, ('referenced', key, '', '', str(tr('Informe ECC; contenido omitido para respetar el presupuesto de transporte.')))))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        # A failed secret check or save must not leak the rejected body to the chief.
        failure = _failure(str(tr('ECC rechazó o no pudo verificar el informe extenso; contenido omitido.'))+' '+str(error))
        failure['pending'] = str(tr('Guardado ECC fallido; fragmento del error literal.'))
        failure['error'] = failure['error'].encode()[:budget].decode(errors='ignore')
        while len(json.dumps(failure, ensure_ascii=False).encode()) > budget:
            failure['error'] = failure['error'][:-1]
        return failure


# Flags of `muse exec --help` that take no value: a prompt may follow them. Any other
# `-flag` (valued, optional-valued like -w, or unknown) leaves the arguments untouched.
MUSE_FLAGS = frozenset((
    '--json', '--api-key-stdin', '--parallel-tool-calls', '--no-parallel-tool-calls',
    '--allow-workspace-switch', '--user-input-auto-resolve', '--subagent-worktree-isolation',
    '--disable-web-tools', '--disable-reminders', '--disable-request-user-input',
    '--no-foreign-personal-context', '--no-session-log', '--yolo', '--trust-workspace',
    '--disable-approval', '--disable-sandbox', '--disable-write', '--disable-shell',
    '--enable-shell-tool',
))


def _muse_prompt_last(arguments):
    """True only when the last argument is surely the prompt, not the value of an option."""
    if len(arguments) < 2:
        return False
    if '--' in arguments:
        return arguments.index('--') == len(arguments)-2
    last, before = arguments[-1], arguments[-2]
    if last.startswith('-'):
        return False
    return not before.startswith('-') or '=' in before or before in MUSE_FLAGS


def run(command, agent, role, config, cwd, *, override=None, output=None, profile=None):
    output = output or sys.stdout
    policy = context_policy.resolve(config, role, override=override)
    if not policy['enabled']:
        # Exact native behavior, including stdout/stderr, when explicitly disabled.
        os.execvpe(command[0], command, dict(os.environ))
        return 0
    final = None
    fatal = None
    pi_output = bytearray()
    pi_overflow = False
    prepared, environment, capsule = prepare(command, agent, role, config, cwd, override=override, profile=profile)
    measure = policy['measure_task_tokens'] and capsule is not None
    totals, turns = dict.fromkeys(COUNTERS, 0), 0
    if measure:
        _write_snapshots(capsule['id'], before=_snapshot(agent, dict.fromkeys(COUNTERS, 0), 0))
    with tempfile.TemporaryDirectory(prefix='ai-team-report-schema-') as temporary:
        schema = Path(temporary)/'report.json'
        schema.touch(mode=0o600)
        schema.write_text(json.dumps(SCHEMA))
        if agent in ('codex', 'codex-2'):
            try:
                position = prepared.index('exec') + 1
            except ValueError as error:
                raise ValueError(tr('La receta de Codex requiere exec nativo.')) from error
            prepared[position:position] = ['--json', '--output-schema', str(schema)]
        elif agent == 'claude':
            prepared[1:1] = ['--output-format', 'json', '--json-schema', json.dumps(SCHEMA)]
        elif agent == 'muse':
            # Muse's exec output format is undocumented: its text is read whole, so the task itself carries the schema.
            if _muse_prompt_last(prepared):
                prepared[-1] += '\n\n' + str(tr('Responde solo con un objeto JSON (sin texto ni cercas de código) que cumpla este esquema, con cinco campos de texto: ')) + json.dumps(SCHEMA)
        elif agent == 'pi':
            pass  # Pi is read as plain text.
        # Raw events are drained and discarded. No transcripts or raw stderr reach the caller.
        with subprocess.Popen(prepared, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as child:
            while True:
                line = child.stdout.readline(MAX_EVENT+1)
                if not line:
                    break
                if len(line) > MAX_EVENT:
                    final = None
                    if agent in ('pi', 'muse'):
                        pi_overflow = True
                        pi_output.clear()
                    while line and not line.endswith(b'\n'):
                        line = child.stdout.readline(MAX_EVENT+1)
                    continue
                if agent in ('pi', 'muse'):
                    if len(pi_output) + len(line) > MAX_EVENT:
                        pi_overflow = True
                        pi_output.clear()
                    elif not pi_overflow:
                        pi_output.extend(line)
                    continue
                try:
                    event = json.loads(line)
                    counted = token_usage(agent, event) if measure else None
                    if counted:
                        totals, turns = _accumulate(totals, counted), turns + 1
                    if event.get('type') == 'item.completed' and event.get('item', {}).get('type') == 'agent_message':
                        final = None
                        final = _report(event['item']['text'])
                    elif event.get('type') == 'result':
                        final = None
                        if event.get('is_error'):
                            fatal = '\n'.join(event.get('errors', [])) or str(event.get('result', 'native result error'))
                        else:
                            final = _report(event.get('structured_output', event.get('result')))
                    elif event.get('type') == 'error' and isinstance(event.get('message'), str):
                        fatal = event['message']
                    elif event.get('type') == 'turn.failed':
                        fatal = event.get('error', {}).get('message') or 'turn.failed'
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
            code = child.wait()
        if agent in ('pi', 'muse') and not pi_overflow:
            try:
                final = _report(pi_output.decode('utf-8'))
            except (ValueError, UnicodeError, TypeError):
                final = None
    if measure:
        # Without a single counted event the totals are unknown, never an invented zero.
        _write_snapshots(capsule['id'], after=_snapshot(agent, totals if turns else dict.fromkeys(COUNTERS), turns))
        if turns:
            try:
                role_usage.record(role, profile or agent, totals)
            except OSError:
                pass  # the ledger is only a convenience for the usage screen
    if code or fatal:
        final = _failure(fatal or str(tr('La CLI nativa terminó con código {code}; stderr descartado.', code=code)))
    elif final is None:
        final = _failure(str(tr('El obrero no entregó el informe final de cinco campos.')))
        code = 1
    bounded = _bounded(final, capsule)
    print(json.dumps(bounded, ensure_ascii=False), file=output)
    return code or (1 if fatal or (bounded is not final and bounded['status'] == 'failed') else 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings')
    parser.add_argument('--agent', choices=('claude', 'claude-2', 'codex', 'codex-2', 'pi', 'muse'), required=True)
    parser.add_argument('--role', choices=context_policy.ROLES, required=True)
    parser.add_argument('--cwd', required=True)
    parser.add_argument('--context-override', type=json.loads)
    parser.add_argument('native', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        command = args.native[1:] if args.native[:1] == ['--'] else args.native
        if not command:
            raise ValueError(tr('Falta el comando nativo del obrero.'))
        import harness
        if args.settings:
            config = json.loads(Path(args.settings).read_text())
        else:
            config = harness.load_config()
        os.chdir(Path(args.cwd).resolve(strict=True))
        agent = 'claude' if args.agent == 'claude-2' else args.agent
        return run(command, agent, args.role, config, Path.cwd(), override=args.context_override, profile=args.agent)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        # Only a literal bounded startup failure; no child output is relayed here.
        print(json.dumps(_failure(str(error)[:512]), ensure_ascii=False))
        return 1


if __name__ == '__main__':
    import i18n
    with i18n.using(i18n.configured_language()):
        raise SystemExit(main())
