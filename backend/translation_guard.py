"""Deterministic source guard for the panel's explicit message lookups."""

import ast
import json
from pathlib import Path
import re
from string import Formatter

# These functions produce agent instructions or generated program source, not panel copy.
INSTRUCTIONS = {
    'context_state.py': {'instructions', '_body', 'stable_instructions', '_idle_notice'},
    'native_local_tools.py': {'text', 'instruction'},
    'native_team.py': {'text', 'instruction', 'codex_command', 'claude_command', 'role_commands'},
    'shared_memory.py': {'instructions'},
    'native_plugins.py': {'codex_instructions'},
    'nous_quota.py': {'extension'},
    'ai_check.py': {'prompt_for'},
}
INSTRUCTION_CONSTANTS = {'USAGE_GUARD_MESSAGE', 'SECURITY_DESCRIPTION', 'TOKENS_DESCRIPTION', 'SECURITY_TEXT',
                         'TOKENS_TEXT', 'SECURITY_COMMANDS', 'SECURITY_LESSONS', 'TOKENS_COMMANDS'}
PROPER = {'AI Team', 'ECC Memory Vault', 'Superpowers', 'Claude', 'Claude Code', 'Codex',
          'Codex-2', 'Pi', 'Grok', 'Nous', 'xAI', 'English', 'Español', 'Tokens', 'Git', 'Python',
          'Node', 'Ruff', 'ShellCheck', 'Gitleaks', 'OSV-Scanner', 'Semgrep', 'qmllint',
          'ripgrep', 'jq', 'yq', 'ast-grep', 'hadolint'}
DISPLAY_FIELDS = {'text', 'label', 'detail', 'message', 'error', 'warning', 'purpose', 'brief',
                  'warnings', 'failures', 'notices', 'updateErrors', 'category', 'description', 'ownershipDetail', 'status', 'line', 'help',
                  'errorText', 'emptyText', 'noSelectionText', 'tooltipText', 'placeholderText', 'cancelText', 'confirmText', 'title'}
DISPLAY_VARIABLES = {'label', 'message', 'detail', 'launch_detail', 'warning', 'notice'}


def words(value):
    return isinstance(value, str) and value not in PROPER and bool(re.search(r'[A-Za-záéíóúñÁÉÍÓÚÑ]', value))


def python_nodes(text, filename=''):
    tree = ast.parse(text)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr)):
            continue
        if isinstance(node, ast.Constant) and not isinstance(node.value, str):
            continue
        if isinstance(parents.get(node), (ast.JoinedStr, ast.Expr)):
            continue
        ancestors = []; current = node
        while current in parents:
            current = parents[current]; ancestors.append(current)
        if any(isinstance(a, ast.FunctionDef) and a.name in INSTRUCTIONS.get(filename, set()) for a in ancestors):
            continue
        if any(isinstance(a, ast.Assign) and any(isinstance(t, ast.Name) and t.id in INSTRUCTION_CONSTANTS for t in a.targets) for a in ancestors):
            continue
        if any(isinstance(a, ast.Call) and isinstance(a.func, ast.Name) and a.func.id == 'tr' for a in ancestors):
            continue
        if isinstance(parents.get(node), ast.Dict) and any(isinstance(k, ast.Constant) and k.value == 'usage' and v is node for k, v in zip(parents[node].keys, parents[node].values)):
            continue
        if isinstance(parents.get(node), ast.Dict) and node in parents[node].keys:
            continue
        if any(isinstance(a, ast.Subscript) and (a.slice is node or a.slice in ancestors) for a in ancestors):
            continue
        if any(isinstance(a, ast.Compare) and (node in a.comparators or a.left is node) for a in ancestors):
            continue
        if any(isinstance(a, ast.Call) and isinstance(a.func, ast.Attribute) and a.func.attr in ('get', 'setdefault') and a.args and a.args[0] is node for a in ancestors):
            continue
        if any(isinstance(a, ast.Call) and isinstance(a.func, ast.Attribute) and isinstance(a.func.value, ast.Name) and a.func.value.id == 'native_plugins' and a.func.attr == 'command' for a in ancestors):
            continue
        parent = parents.get(node)
        if isinstance(node, ast.Constant) and node.value in ('ok', 'warning', 'pending', 'off') and any(isinstance(a, ast.Tuple) and (a.elts[-1] is node or a.elts[-1] in ancestors) for a in ancestors):
            continue
        value = node.value if isinstance(node, ast.Constant) else ''.join(v.value for v in node.values if isinstance(v, ast.Constant))
        if not words(value):
            continue
        sink = any(isinstance(a, ast.Raise) for a in ancestors)
        if any(isinstance(a, ast.Return) for a in ancestors) and any(isinstance(a, ast.FunctionDef) and (a.name in DISPLAY_FIELDS or a.name.endswith('_line')) for a in ancestors):
            sink = True
        for a in ancestors:
            if isinstance(a, ast.Assign):
                metadata = {target.id for target in a.targets if isinstance(target, ast.Name)}
                parent = parents.get(node)
                if (metadata & {'LABELS', 'BRIEFS'}
                        or ('MANAGED' in metadata and isinstance(parent, ast.Tuple) and parent.elts[0] is node)
                        or ('GOALS' in metadata and isinstance(parent, ast.Tuple) and len(parent.elts) > 1 and parent.elts[1] is node)):
                    sink = True
            if isinstance(a, ast.FunctionDef) and a.name == 'notify_failure':
                parent = parents.get(node)
                if isinstance(parent, ast.List) and node in parent.elts[3:]:
                    sink = True
            if isinstance(a, ast.Dict):
                for key, item in zip(a.keys, a.values):
                    if isinstance(key, ast.Constant) and key.value in DISPLAY_FIELDS and (item is node or item in ancestors):
                        sink = True
            if isinstance(a, ast.Assign) and any(isinstance(t, ast.Name) and t.id in DISPLAY_VARIABLES for t in a.targets):
                sink = True
            if isinstance(a, ast.keyword) and a.arg in DISPLAY_FIELDS:
                sink = True
        # Catch Spanish copy outside a recognized presentation sink too.
        spanish = bool(re.search(r'[áéíóúñÁÉÍÓÚÑ¿¡]', value))
        if sink or spanish:
            yield node


def unwrapped(text, language, filename=''):
    if language == 'py':
        return [f'{node.lineno}: {ast.get_source_segment(text, node)}' for node in python_nodes(text, filename)]
    result = []
    # All textual literals in user-facing properties must use the lookup, including English text.
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith('//'):
            continue
        for match in re.finditer(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'', line):
            value = ast.literal_eval(match[0])
            if not words(value):
                continue
            prefix = line[:match.start()]
            fields = list(re.finditer(r'\b([A-Za-z]+)\s*:', prefix))
            if fields and fields[-1][1] in ('value', 'id', 'objectName', 'state'):
                continue
            if re.search(r'\btr\(\s*$', prefix):
                continue
            if re.search(r'\b(?:text|label|status|errorText|message|emptyText|noSelectionText|tooltipText|placeholderText|cancelText|confirmText|title)\s*:', prefix) or re.search(r'\b(?:message|errorText)\s*=(?!=)', prefix) or re.search(r'[áéíóúñÁÉÍÓÚÑ¿¡]', value):
                # Text properties may compare protocol states inside an expression.
                if re.search(r'(?:===|!==|objectName:)\s*$', prefix):
                    continue
                result.append(f'{number}: {value}')
    return result


def sources(root):
    keys = set()
    for path in (root / 'backend').glob('*.py'):
        if path.name in ('i18n.py', 'translation_guard.py'):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'tr' and node.args and isinstance(node.args[0], ast.Constant):
                keys.add(node.args[0].value)
    for path in root.glob('*.qml'):
        for match in re.finditer(r'\btr\(\s*("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')', path.read_text()):
            keys.add(ast.literal_eval(match[1]))
    return keys


def compare(keys, translations):
    problems = [f'missing: {key}' for key in sorted(keys - translations.keys())]
    problems += [f'orphan: {key}' for key in sorted(translations.keys() - keys)]
    for key in keys & translations.keys():
        if not isinstance(translations[key], str) or not translations[key]:
            problems.append(f'invalid: {key}')
            continue
        try:
            fields = lambda text: {name for _, name, _, _ in Formatter().parse(text) if name is not None}
            if fields(key) != fields(translations[key]):
                problems.append(f'fields: {key}')
        except ValueError:
            problems.append(f'template: {key}')
    return problems


def check(root):
    problems = []
    for path in [*(root / 'backend').glob('*.py'), *root.glob('*.qml')]:
        if path.name in ('i18n.py', 'translation_guard.py'):
            continue
        problems.extend(f'{path.name}:{item}' for item in unwrapped(path.read_text(), 'qml' if path.suffix == '.qml' else 'py', path.name))
    return problems + compare(sources(root), json.loads((root / 'translations/en.json').read_text()))
