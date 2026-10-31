"""Explicit, bounded inference check through native CLIs in disposable projects."""

from i18n import tr

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile

import memory_archive
import memory_mcp
import native_team
import shared_memory
import team


def connected(snapshot, component, agent):
    return any(row['id'] == component and row.get('enabled') and any(
        target['agent'] == agent and target.get('enabled') and target.get('verified')
        for target in row.get('targets', [])) for row in snapshot.get('components', []))


def expected(config, snapshot, agent):
    skills = []
    if config.get('local-toolsEnabled'): skills.append('ai-team-local-tools')
    if config.get('team', {}).get('enabled'): skills.append('ai-team-roles')
    return dict(skills=skills, tools=connected(snapshot, 'local-tools', agent),
                memory=connected(snapshot, 'memory', agent),
                codeModel=snapshot.get('team', {}).get('roles', {}).get('code', {}).get('model', '')
                if config.get('team', {}).get('enabled') else '')


def pi_check_model(config):
    free = ['nous/' + model['id'] for model in config.get('piNousCatalog', {}).get('models', [])
            if model['id'].endswith(':free') and model.get('cost')
            and all(type(cost) in (int, float) and cost == 0 for cost in model['cost'].values())]
    # A connection check must not fail because of the model: measured 3/3 with beam and 0/3 with solar on 2026-10-31.
    return next((model for model in free if model.startswith('nous/reflection/beam')), free[0] if free else None)


def prompt_for(wanted, marker):
    return ('Comprobación de conexiones en un proyecto temporal. No delegues ni hagas commits. '
            'No leas otros proyectos ni conversaciones. Enumera las skills ai-team-* visibles. '
            'Si tools es true, ejecuta exactamente rg --version y copia su primera línea en rgVersion. '
            'Si memory es true, usa memory_save del MCP ai-team-memory para guardar el marcador '
            'en este proyecto y memory_search para buscarlo; devuelve en memoryMarker solo el marcador '
            'encontrado. Si team es true, lee ai-team-roles y la línea de Programar tiene la forma agente · modelo · esfuerzo: devuelve en codeModel solo el campo modelo. '
            'No inventes resultados: usa cadenas vacías para lo que no pudiste comprobar. '
            'Responde una sola línea JSON con skills (lista de nombres), rgVersion, memoryMarker y codeModel.\n'
            + json.dumps(dict(tools=wanted['tools'], memory=wanted['memory'],
                              team=bool(wanted['codeModel']), marker=marker), ensure_ascii=False))


def command(agent, model, wanted):
    binary, key = agent['binary'], agent['id']
    if key == 'claude':
        allowed = ['Bash(rg --version)']
        if wanted['codeModel']:
            skill = native_team.layout('claude')['source'] / 'SKILL.md'
            allowed += ['Skill(' + native_team.ID + ')', 'Read(/' + str(skill) + ')']
        if wanted['memory']:
            allowed += ['mcp__ai-team-memory__memory_save', 'mcp__ai-team-memory__memory_search']
        return [binary, '-p', '--model', 'haiku', '--effort', 'low', '--output-format', 'json',
                '--allowedTools', *allowed]
    if key in ('codex', 'codex-2'):
        args = [binary, 'exec', '--json', '-s', 'read-only', '-m', model,
                '-c', 'model_reasoning_effort="low"']
        if wanted['memory']:
            args += ['-c', 'mcp_servers.ai-team-memory.default_tools_approval_mode="approve"']
        return [*args, '-']
    provider, model_id = model.split('/', 1)
    return [binary, '--print', '--mode', 'json', '--no-session', '--offline',
            '--thinking', 'off', '--provider', provider, '--model', model_id]


def parse_output(output):
    """Read the last JSON report, including native Claude/Codex/Pi envelopes."""
    report = None; metrics = {}; failed = False
    for line in output.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict): continue
        message = row.get('message', {}) if row.get('type') == 'message_end' else {}
        if not isinstance(message, dict): return None, metrics
        if (row.get('is_error') or row.get('type') in ('turn.failed', 'error')
                or message.get('stopReason') in ('error', 'aborted')):
            failed = True
        usage = row.get('usage') or message.get('usage') or {}
        if isinstance(usage, dict):
            count = usage.get('total_tokens')
            if count is None:
                inputs, outputs = usage.get('input_tokens', usage.get('input')), usage.get('output_tokens', usage.get('output'))
                if type(inputs) is int and type(outputs) is int: count = inputs + outputs
            if type(count) is int and count >= 0: metrics['tokens'] = count
        cost = row.get('total_cost_usd')
        if cost is None and isinstance(usage, dict) and isinstance(usage.get('cost'), dict):
            cost = usage['cost'].get('total')
        if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0: metrics['cost'] = cost
        text = row.get('result')
        if row.get('type') == 'item.completed':
            item = row.get('item')
            if not isinstance(item, dict): return None, metrics
            if item.get('type') == 'agent_message': text = item.get('text')
        if message.get('role') == 'assistant':
            content = message.get('content', [])
            if not isinstance(content, list) or any(not isinstance(part, dict) for part in content):
                return None, metrics
            text = '\n'.join(part['text'] for part in content
                             if part.get('type') == 'text' and isinstance(part.get('text'), str))
        if isinstance(text, str):
            nested, _ = parse_output(text)
            if nested is not None: report = nested
        elif 'skills' in row:
            report = row
    return None if failed else report, metrics


def compare(wanted, report, marker):
    failures = []
    skills = report.get('skills', [])
    if not isinstance(skills, list): skills = []
    for skill in wanted['skills']:
        if skill not in skills: failures.append(tr('No se ve la skill {skill}.', skill=skill))
    if wanted['tools'] and not re.match(r'^ripgrep \d', str(report.get('rgVersion', ''))):
        failures.append(tr('No se comprobó la ejecución de rg --version.'))
    if wanted['memory'] and report.get('memoryMarker') != marker:
        failures.append(tr('No se encontró el marcador guardado en Memoria.'))
    # Models answer "claude opus high" as often as "opus"; the id must be one of the words.
    if wanted['codeModel'] and wanted['codeModel'] not in re.split(r'[\s·,:]+', str(report.get('codeModel', ''))):
        failures.append(tr('El modelo de Programar no coincide: esperado {esperado}, visto {visto}.', esperado=wanted['codeModel'],
                           visto=str(report.get('codeModel', ''))[:80] or tr('nada')))
    return failures


def run(config, snapshot):
    results = []
    for agent in snapshot.get('agents', []):
        if not agent.get('enabled') or agent.get('installation') != 'installed': continue
        key = agent['id']; wanted = expected(config, snapshot, key)
        row = dict(agent=key, name=agent['name'], ok=False, failures=[], warnings=[])
        results.append(row)
        if key == 'pi':
            model = pi_check_model(config)
            if not model:
                row.update(skipped=True, warnings=[tr('Pi omitido: no hay un modelo gratuito comprobado.')])
                continue
            row['warnings'].append(tr('Pi no tiene sandbox ni aprobación por herramienta.'))
        else:
            model = 'haiku' if key == 'claude' else team.newest_codex(config.get('teamModels', {}), 'luna')
        row['model'] = model
        marker = secrets.token_hex(24)
        environment = {name: value for name, value in os.environ.items() if not name.startswith('GIT_')}
        if key in ('codex', 'codex-2'):
            environment['CODEX_HOME'] = str(Path.home() / ('.codex-2' if key == 'codex-2' else '.codex'))
        store = None
        try:
            with tempfile.TemporaryDirectory(prefix='ai-team-ai-check-') as directory:
                try:
                    init = subprocess.run(['git', 'init', '--quiet'], cwd=directory, env=environment,
                                          capture_output=True, text=True, timeout=10)
                    if init.returncode: raise ValueError()
                    store = memory_mcp.project_store(directory, shared_memory.data_root() / 'memory')
                    reply = subprocess.run(command(agent, model, wanted), cwd=directory, env=environment,
                                           input=prompt_for(wanted, marker), capture_output=True, text=True, timeout=180)
                    report, metrics = parse_output(reply.stdout)
                    row.update(metrics)
                    if reply.returncode:
                        row['failures'].append(tr('La CLI no completó la comprobación con IA.'))
                    else:
                        if report is None: row['failures'].append(tr('La CLI no devolvió una línea JSON válida.'))
                        else: row['failures'].extend(compare(wanted, report, marker))
                finally:
                    if store is not None:
                        safe_store = memory_archive.safe_path(store)
                        if safe_store.exists(): shutil.rmtree(safe_store)
        except subprocess.TimeoutExpired:
            row['failures'].append(tr('Se agotó el tiempo de comprobación (máximo 180 s por agente).'))
        except (OSError, ValueError, shared_memory.MemoryError, memory_archive.ArchiveError):
            row['failures'].append(tr('No se pudo preparar, ejecutar o limpiar la comprobación temporal.'))
        row['ok'] = not row['failures']
    return dict(checkedAt=datetime.now(timezone.utc).isoformat(), results=results)
