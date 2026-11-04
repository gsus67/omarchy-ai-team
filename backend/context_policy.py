"""Pure, per-launch context settings. Never reads or writes agent profiles.

Thresholds are native compaction recommendations, not hard context caps. Floor,
report and checkpoint settings are resolved here for subsequent native consumers.
"""

from copy import deepcopy
from pathlib import Path

from i18n import tr


class ContextPolicyError(ValueError):
    pass


ROLES = ('jefe', 'plan', 'code', 'review', 'simple')
NUMERIC = ('compact_at_tokens', 'compact_floor_tokens', 'worker_report_max_tokens')
WINDOW = 'read_window_lines'
IDLE_TTL = 'idle_cache_ttl_minutes'
SAVINGS_BOOLS = ('task_plan_with_pointers', 'fresh_worker_per_subtask', 'bounded_reads',
                 'stable_prompt_prefix', 'measure_task_tokens', 'clear_between_tasks_below_floor',
                 'group_related_subtasks', 'explore_with_simple_role', 'handoff_on_pause',
                 'suggest_resume_after_idle')
# Keys a role or a reasoned invocation may override (besides ``enabled`` for invocations).
OVERRIDABLE = (*NUMERIC, WINDOW, IDLE_TTL, *SAVINGS_BOOLS)
DEFAULTS = {
    'enabled': True,
    'apply': 'launch',
    'compact_at_tokens': 150000,
    'compact_floor_tokens': 70000,
    'roles': {'jefe': {'compact_at_tokens': 100000}},
    'dump_state_before': True,
    'chief_brief': 'memory_index',
    'worker_report_max_tokens': 800,
    'worker_report_on': ['milestone', 'blocked'],
    **{key: True for key in SAVINGS_BOOLS},
    WINDOW: 120,
    IDLE_TTL: 55,
}


def _object(value, keys):
    if not isinstance(value, dict) or any(key not in keys for key in value):
        raise ContextPolicyError(tr('La política de contexto contiene campos o tipos no válidos.'))


def _numbers(value):
    for key in NUMERIC:
        if key in value and (type(value[key]) is not int or not 1 <= value[key] < 2**63):
            raise ContextPolicyError(tr('Los límites de contexto deben ser enteros positivos admitidos.'))


def _savings(value):
    for key in SAVINGS_BOOLS:
        if key in value and type(value[key]) is not bool:
            raise ContextPolicyError(tr('La política de contexto requiere booleanos explícitos.'))
    if WINDOW in value and (type(value[WINDOW]) is not int or not 1 <= value[WINDOW] <= 2000):
        raise ContextPolicyError(tr('La ventana de lectura debe ser un entero entre 1 y 2000.'))
    if IDLE_TTL in value and (type(value[IDLE_TTL]) is not int or not 1 <= value[IDLE_TTL] <= 1440):
        raise ContextPolicyError(tr('El tiempo de caché inactiva debe ser un entero entre 1 y 1440 minutos.'))


def _floor(value):
    if value['compact_floor_tokens'] >= value['compact_at_tokens']:
        raise ContextPolicyError(tr('El piso de contexto debe ser menor que el umbral de cada papel.'))


def settings(config):
    """Validate without materializing defaults in the caller's configuration."""
    raw = config.get('context', {})
    _object(raw, DEFAULTS)
    value = {**deepcopy(DEFAULTS), **deepcopy(raw)}
    for key in ('enabled', 'dump_state_before'):
        if type(value[key]) is not bool:
            raise ContextPolicyError(tr('La política de contexto requiere booleanos explícitos.'))
    if value['apply'] != 'launch' or value['chief_brief'] != 'memory_index':
        raise ContextPolicyError(tr('La política solo se aplica al lanzamiento y usa un índice de memoria.'))
    events = value['worker_report_on']
    if (not isinstance(events, list) or not events
            or any(event not in ('milestone', 'blocked') for event in events)
            or len(set(events)) != len(events)):
        raise ContextPolicyError(tr('Los informes de contexto solo admiten hitos y bloqueos sin duplicados.'))
    _numbers(value)
    _savings(value)
    _object(value['roles'], ROLES)
    roles = deepcopy(DEFAULTS['roles'])
    for role, row in value['roles'].items():
        _object(row, OVERRIDABLE)
        _numbers(row)
        _savings(row)
        roles[role] = {**roles.get(role, {}), **row}
    value['roles'] = roles
    _floor(value)
    for role in ROLES:
        _floor({**value, **roles.get(role, {})})
    return value


def resolve(config, role='jefe', *, override=None):
    """Precedence: invocation (with reason), role, user settings, recommendations."""
    if role not in ROLES:
        raise ContextPolicyError(tr('El papel de contexto no es válido.'))
    value = settings(config)
    roles = value.pop('roles')
    value.update(roles.get(role, {}))
    if override is not None:
        _object(override, (*OVERRIDABLE, 'enabled', 'reason'))
        reason = override.get('reason')
        if not isinstance(reason, str) or not reason.strip():
            raise ContextPolicyError(tr('La excepción de contexto necesita un motivo explícito.'))
        _numbers(override)
        _savings(override)
        if 'enabled' in override and type(override['enabled']) is not bool:
            raise ContextPolicyError(tr('La política de contexto requiere booleanos explícitos.'))
        value.update(override)
    _floor(value)
    return value


GLOBAL_KEYS = (*OVERRIDABLE, 'enabled', 'dump_state_before', 'worker_report_on')


def with_value(config, key, value, role=None):
    """Return a validated copy of ``config`` with one context key changed; never writes."""
    if role is not None and role not in ROLES:
        raise ContextPolicyError(tr('El papel de contexto no es válido.'))
    if key not in (OVERRIDABLE if role else GLOBAL_KEYS):
        raise ContextPolicyError(tr('Ese ajuste de contexto no existe o no admite este ámbito.'))
    result = deepcopy(config)
    context = result.setdefault('context', {})
    target = context.setdefault('roles', {}).setdefault(role, {}) if role else context
    target[key] = value
    settings(result)
    return result


def without(config, role=None):
    """Return a copy of ``config`` with all context settings, or one role row, removed."""
    result = deepcopy(config)
    if role is None:
        result.pop('context', None)
        return result
    if role not in ROLES:
        raise ContextPolicyError(tr('El papel de contexto no es válido.'))
    context = result.get('context', {})
    context.get('roles', {}).pop(role, None)
    if not context.get('roles', True):
        context.pop('roles')
    settings(result)  # Reject a reset that would leave an invalid file; the caller never saves it.
    return result


def _as_text(row):
    """Token limits can reach 64 bits: JavaScript numbers would round them, so send decimal text."""
    return {key: str(value) if key in NUMERIC else value for key, value in row.items()}


def snapshot(config):
    """Saved settings plus the effective values of every role, computed without writing.

    Token limits are decimal text for the QML; stored files and ``resolve`` keep integers.
    """
    saved = _as_text(settings(config))
    saved['roles'] = {role: _as_text(row) for role, row in saved['roles'].items()}
    return {'settings': saved, 'resolved': {role: _as_text(resolve(config, role)) for role in ROLES}}


def launch_options(agent, policy):
    """Return native argv and terminal-local env assignments; no global mutation."""
    if not policy['enabled']:
        return [], []
    threshold = policy['compact_at_tokens']
    if agent == 'claude':
        if not 100000 <= threshold <= 1000000:
            raise ContextPolicyError(tr('Claude admite umbrales de contexto entre 100000 y 1000000.'))
        # The native env knob wins over --autocompact, including after uwsm.
        return ['--autocompact', str(threshold)], [f'CLAUDE_CODE_AUTO_COMPACT_WINDOW={threshold}']
    if agent in ('codex', 'codex-2'):
        return ['-c', f'model_auto_compact_token_limit={threshold}',
                '-c', 'model_auto_compact_token_limit_scope="total"'], []
    if agent == 'muse':
        return [], []  # only exec-level fractions and no hooks are documented: no thresholds are guessed
    if agent == 'pi':
        return ['--extension', str(Path(__file__).resolve().parent.parent / 'extensions' / 'pi-context-policy.ts')], []
    raise ContextPolicyError(tr('El agente predeterminado no es válido; se conserva la configuración.'))
