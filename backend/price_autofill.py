"""Ask the chief AI (the default Claude or Codex agent) to look up the empty token prices.

Explicit, bounded, read-only: the chief gets web search and nothing else, in a disposable folder, and
only empty fields are filled (the user's own prices are never touched). Every filled price keeps the
source the chief reported so it can be checked.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from i18n import tr
import price_table
import team

CHIEFS = ('claude', 'claude-2', 'codex', 'codex-2')
TIMEOUT = 240


def chief(config, snapshot):
    key = config.get('defaultAgent', '')
    row = next((agent for agent in snapshot.get('agents', []) if agent.get('id') == key), None)
    if key not in CHIEFS or not row or not row.get('enabled') or row.get('installation') != 'installed':
        raise ValueError(tr('Elige como predeterminado un agente Claude o Codex activo: es la IA jefe que consulta los precios.'))
    return row


def prompt(models):
    return ('Look up the current API prices, in USD per million tokens at standard (non-batch, non-priority) rates, for these models: '
            + ', '.join(models) + '.\n'
            'Use only the provider\'s official pricing page (platform.claude.com for Claude, developers.openai.com for OpenAI/Codex models). '
            'Third-party sites disagree with each other and are not acceptable sources.\n'
            'Reply with ONLY one JSON object: {"models": {"<model>": {"input": number|null, "output": number|null, '
            '"cacheRead": number|null, "cacheWrite": number|null, "source": "<official url>"}}}.\n'
            'cacheRead is the price of cached input tokens and cacheWrite of writing to the cache. '
            'Use null for any value you cannot confirm on an official page. Do not guess, estimate or convert from another model.')


def command(agent, config):
    binary = agent['binary']
    if agent['id'] in ('claude', 'claude-2'):
        return [binary, '-p', '--model', 'sonnet', '--effort', 'medium', '--output-format', 'json',
                '--tools', 'WebSearch,WebFetch', '--strict-mcp-config', '--disable-slash-commands',
                '--allowedTools', 'WebSearch', 'WebFetch']
    return [binary, '--search', 'exec', '--json', '--skip-git-repo-check', '-s', 'read-only',
            '-m', team.newest_codex(config.get('teamModels', {}), 'luna'), '-c', 'model_reasoning_effort="medium"', '-']


def _texts(output):
    texts = []
    for line in output.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if isinstance(row.get('result'), str):
            texts.append(row['result'])
        item = row.get('item')
        if row.get('type') == 'item.completed' and isinstance(item, dict) and item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
            texts.append(item['text'])
    return texts or [output]


def extract(output):
    """The validated ``{model: proposal}`` of the last JSON object that has a ``models`` mapping, or {}."""
    for text in reversed(_texts(output)):
        fenced = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.S)
        candidate = fenced.group(1) if fenced else text[text.find('{'):text.rfind('}') + 1] if '{' in text else ''
        try:
            data = json.loads(candidate)
        except (ValueError, RecursionError):
            continue
        models = data.get('models') if isinstance(data, dict) else None
        if not isinstance(models, dict):
            continue
        return {name: row for name, row in models.items() if isinstance(name, str) and isinstance(row, dict)}
    return {}


def run(config, snapshot):
    result = {'agent': '', 'at': datetime.now(timezone.utc).isoformat(), 'filled': [], 'pending': [], 'error': ''}
    try:
        row = chief(config, snapshot)
    except ValueError as error:
        result['error'] = str(error)
        return result
    result['agent'] = row['id']
    try:
        price_table.ensure_writable()
    except ValueError as error:
        result['error'] = str(error)
        return result
    models = [entry['model'] for entry in price_table.rows() if any(entry[field] is None for field in price_table.FIELDS)]
    if not models:
        return result
    environment = {name: value for name, value in os.environ.items() if not name.startswith('GIT_')}
    if row['id'] in ('codex', 'codex-2'):
        environment['CODEX_HOME'] = str(Path.home() / ('.codex-2' if row['id'] == 'codex-2' else '.codex'))
    if row['id'] == 'claude-2':
        environment['CLAUDE_CONFIG_DIR'] = str(Path.home() / '.claude-2')
    elif row['id'] == 'claude':
        environment.pop('CLAUDE_CONFIG_DIR', None)  # the main account never inherits claude-2's directory
    try:
        with tempfile.TemporaryDirectory(prefix='ai-team-prices-') as directory:
            reply = subprocess.run(command(row, config), cwd=directory, env=environment, input=prompt(models),
                                   capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        result['error'] = tr('Se agotó el tiempo de la consulta de precios (máximo {segundos} s).', segundos=TIMEOUT)
        return result
    except OSError:
        result['error'] = tr('No se pudo ejecutar la IA jefe para consultar precios.')
        return result
    proposals = extract(reply.stdout) if reply.returncode == 0 else {}
    if not proposals:
        result['error'] = tr('La IA jefe no devolvió precios legibles; no se cambió nada.')
        return result
    result['filled'] = price_table.apply_proposals(proposals)
    result['pending'] = [entry['model'] for entry in price_table.rows() if any(entry[field] is None for field in price_table.FIELDS)]
    return result
