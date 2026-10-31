"""Pure team-role preferences and recommendations; never starts a CLI."""

from i18n import tr

import copy
import re

ROLES = ('plan', 'code', 'review', 'simple')
LABELS = dict(zip(ROLES, (tr('Planificar'), tr('Programar'), tr('Revisar'), tr('Tareas sencillas'))))
CLAUDE_MODELS = ('fable', 'opus', 'sonnet', 'haiku')
CODEX_DEFAULTS = ('gpt-6.1-sol', 'gpt-6-luna')
CLAUDE_EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
PI_EFFORTS = ('low', 'medium', 'high')
STRONG_ORDER = (('claude', 'opus'), ('codex', 'sol'), ('claude', 'sonnet'), ('codex', 'luna'))
FALLBACK_ORDER = {'plan': STRONG_ORDER, 'code': STRONG_ORDER,
                  'review': (('claude', 'sonnet'), ('claude', 'opus'), ('codex', 'sol'), ('codex', 'luna'), ('pi', '')),
                  'simple': (('pi', ''), ('claude', 'haiku'), ('codex', 'luna'), ('claude', 'sonnet'))}
ROLE_EFFORTS = dict(zip(ROLES, ('xhigh', 'high', 'high', 'medium')))


class TeamError(Exception):
    pass


def families(selected):
    return [agent for agent in ('claude', 'codex', 'pi')
            if agent in selected or (agent == 'codex' and 'codex-2' in selected)]


def codex_models(catalog):
    return catalog.get('codex') or [
        {'id': model, 'supportedReasoningEfforts': [{'reasoningEffort': effort} for effort in
         (('medium', 'high', 'xhigh') if model == CODEX_DEFAULTS[0] else ('medium', 'high'))]}
        for model in CODEX_DEFAULTS]


def pi_efforts(catalog, model):
    return list(PI_EFFORTS) if model in catalog.get('nousEffort', []) else ['']


def newest_codex(catalog, family):
    candidates = []
    for row in codex_models(catalog):
        match = re.fullmatch(r'gpt-(\d+(?:\.\d+)*)-' + family, row['id'])
        if match:
            candidates.append((tuple(map(int, match[1].split('.'))), row['id']))
    return max(candidates)[1] if candidates else CODEX_DEFAULTS[family == 'luna']


def options(selected, catalog, free_pi, paid_ok):
    result = []
    for agent in families(selected):
        if agent == 'claude':
            models = [(model, CLAUDE_EFFORTS) for model in CLAUDE_MODELS]
        elif agent == 'codex':
            models = [(row['id'], [effort['reasoningEffort'] for effort in row.get('supportedReasoningEfforts', [])]
                       or ['high', 'medium']) for row in codex_models(catalog)]
        else:
            models = [(model, pi_efforts(catalog, model)) for model in dict.fromkeys([
                *free_pi, *(row['id'] for row in catalog.get('pi', []))])]
        for model, efforts in models:
            paid = agent == 'pi' and model not in free_pi
            label = {'claude': 'Claude', 'codex': 'Codex', 'pi': 'Pi'}[agent] + ' · ' + model
            if paid: label = tr('{modelo} · de pago', modelo=label)
            result.append(dict(agent=agent, model=model, efforts=list(efforts), label=label, paid=paid,
                               recommendedEfforts={key: recommended_effort(key, efforts) for key in ROLES}))
    return result


def recommended_effort(role, supported):
    preferred = (ROLE_EFFORTS[role], 'high', 'medium', 'low', 'none', '')
    return next((effort for effort in preferred if effort in supported), '')


def recommend(selected, catalog, free_pi):
    available = families(selected); warnings = []
    strong, cheap = (newest_codex(catalog, family) for family in ('sol', 'luna'))
    def choice(agent, model, effort=''):
        if agent == 'codex':
            rows = codex_models(catalog)
            row = next((row for row in rows if row['id'] == model), None)
            if row is None:
                row = next((row for row in rows if row.get('isDefault')), rows[0])
                warnings.append(tr('El modelo {model} no aparece en el catálogo; se usa el predeterminado {value2}.', model=model, value2=row['id']))
            model = row['id']
            efforts = [r['reasoningEffort'] for r in row.get('supportedReasoningEfforts', [])] or ['high', 'medium']
            if effort not in efforts:
                effort = recommended_effort('plan' if effort == 'xhigh' else 'simple' if effort == 'medium' else 'code', efforts)
        if agent == 'pi':
            supported = pi_efforts(catalog, model)
            if effort not in supported:
                effort = recommended_effort('plan' if effort == 'xhigh' else 'simple' if effort == 'medium' else 'code', supported)
        return dict(agent=agent, model=model, effort=effort)
    if 'codex' in available and not catalog.get('codex'):
        warnings.append(tr('Catálogo de Codex no disponible; se usan modelos integrados sin comprobar el catálogo.'))
    if 'claude' in available:
        roles = {key: choice('claude', model, ROLE_EFFORTS[key]) for key, model in zip(ROLES, ('opus', 'opus', 'sonnet', 'haiku'))}
        if 'codex' in available:
            roles.update(plan=choice('codex', strong, 'xhigh'),
                         review=choice('codex', strong, 'high'),
                         simple=choice('codex', cheap, 'medium'))
        elif 'pi' in available and free_pi:
            roles.update(review=choice('pi', free_pi[0], ROLE_EFFORTS['review']), simple=choice('pi', free_pi[0], ROLE_EFFORTS['simple']))
    elif 'codex' in available:
        roles = {key: choice('codex', model, effort) for key, model, effort in (
            ('plan', strong, 'xhigh'), ('code', strong, 'high'),
            ('review', cheap, 'high'), ('simple', cheap, 'medium'))}
        if 'pi' in available and free_pi: roles['simple'] = choice('pi', free_pi[0], ROLE_EFFORTS['simple'])
    elif 'pi' in available and free_pi:
        roles = {key: choice('pi', free_pi[0], ROLE_EFFORTS[key]) for key in ROLES}
    else:
        roles = {}
        warnings.append(tr('No hay modelos gratuitos comprobados disponibles para el equipo.') if available else tr('Selecciona agentes para el equipo.'))
    if roles and same_model(roles.get('code'), roles.get('review')):
        warnings.append(tr('No hay revisión independiente: Programar y Revisar usan el mismo modelo.'))
    return roles, list(dict.fromkeys(warnings))


def same_model(left, right):
    return bool(left and right and (left['agent'], left['model']) == (right['agent'], right['model']))


def codex_version(row):
    match = re.fullmatch(r'gpt-(\d+(?:\.\d+)*)-\w+', row['model'])
    return tuple(map(int, match[1].split('.'))) if match else ()


def alternatives(key, roles, options, limit=3):
    """Ordered free or subscription models for a role; writing roles exclude Pi."""
    main = roles.get(key)
    if not main:
        return []
    # A reviewer must stay independent of whoever writes, in both directions.
    others = [roles.get(name) for name in {'review': ('code', 'simple'), 'code': ('review',)}.get(key, ())]
    allowed = [row for row in options if not row['paid'] and not same_model(row, main)
               and not any(same_model(row, other) for other in others)]
    # Free models with levels first: they let the role keep its effort.
    ranked = sorted(options, key=lambda row: row['agent'] == 'pi' and row['efforts'] == [''])
    ordered = []
    for agent, name in FALLBACK_ORDER[key]:
        matches = [row for row in ranked if row['agent'] == agent and name in row['model']]
        # Only the newest Codex of a family: an older one is not a different author.
        if agent == 'codex' and matches: matches = [max(matches, key=codex_version)]
        ordered += [row for row in matches if row in allowed and row not in ordered]
    return [dict(agent=row['agent'], model=row['model'], effort=row['recommendedEfforts'][key]) for row in ordered[:limit]]


def effective(config, selected, catalog, free_pi):
    recommendations, warnings = recommend(selected, catalog, free_pi)
    choices = options(selected, catalog, free_pi, config.get('piPaidApproved', False))
    warnings = [warning for warning in warnings if getattr(warning, 'source', str(warning)) != 'No hay revisión independiente: Programar y Revisar usan el mismo modelo.']
    warnings.extend(config.get('team', {}).get('notices', []))
    roles = {}
    for key in ROLES:
        recommended = recommendations.get(key)
        stored = config.get('team', {}).get('roles', {}).get(key, {})
        manual = bool(stored.get('manual'))
        value = {field: stored.get(field, '') for field in ('agent', 'model', 'effort')} if manual else recommended
        if manual and value['agent'] == 'pi':
            # The catalog decides which free models have levels and can change after the role was saved.
            levels = next((row['efforts'] for row in choices if same_model(row, value)), None)
            if levels and value['effort'] not in levels and (not value['effort'] or levels == ['']):
                value = {**value, 'effort': recommended_effort(key, levels)}
        if manual and not any(same_model(row, value) and value['effort'] in row['efforts']
                              and (not row['paid'] or config.get('piPaidApproved', False)) for row in choices):
            warnings.append(tr('{papel}: el agente ya no está seleccionado o el modelo no está disponible; se usa el recomendado.', papel=LABELS[key]))
            manual = False; value = recommended
        if value:
            roles[key] = {**value, 'manual': manual, 'recommended': recommended}
    if same_model(roles.get('code'), roles.get('review')):
        warning = tr('No hay revisión independiente: Programar y Revisar usan el mismo modelo.')
        if warning not in warnings: warnings.append(warning)
    if any(row['agent'] == 'codex' and row['effort'] == 'ultra' for row in roles.values()):
        warnings.append(tr('Ultra hace que Codex reparta el trabajo por su cuenta: rompe el reparto del equipo y gasta tokens por partida doble.'))
    return {'enabled': config.get('team', {}).get('enabled', False), 'roles': roles, 'options': choices, 'warnings': warnings}


def set_role(config, role, agent, model, effort, *, selected, catalog, free_pi, paid_ok):
    if role not in ROLES:
        raise TeamError(tr('El papel de equipo no es válido.'))
    if agent == 'pi' and model not in free_pi and not paid_ok:
        raise TeamError(tr('El modelo de pago requiere consentimiento explícito.'))
    choices = [row for row in options(selected, catalog, free_pi, paid_ok) if not row['paid'] or paid_ok]
    supported = next((row['efforts'] for row in choices if (row['agent'], row['model']) == (agent, model)), [])
    previous = effective(config, selected, catalog, free_pi)['roles'].get(role, {})
    changed_model = (previous.get('agent'), previous.get('model')) != (agent, model)
    if not effort:
        effort = previous.get('effort', '') if previous.get('effort') in supported else recommended_effort(role, supported)
    elif effort not in supported and changed_model and effort == previous.get('effort'):
        effort = recommended_effort(role, supported)
    value = dict(agent=agent, model=model, effort=effort)
    if not supported or effort not in supported:
        raise TeamError(tr('Agente, modelo o razonamiento no disponible para la selección actual.'))
    candidate = copy.deepcopy(config)
    candidate.setdefault('team', {}).setdefault('roles', {})[role] = {**value, 'manual': True}
    state = effective(candidate, selected, catalog, free_pi)
    if (same_model(state['roles'].get('code'), state['roles'].get('review'))
            and len({(row['agent'], row['model']) for row in choices}) > 1):
        raise TeamError(tr('Revisar debe usar un modelo distinto de Programar.'))
    config.setdefault('team', {}).setdefault('enabled', False)
    config['team'].setdefault('roles', {})[role] = {**value, 'manual': True}
    config['team'].pop('notices', None)


def reconcile(config, selected, catalog, free_pi):
    """Persist invalid manual-role fallback at refresh time, never during status."""
    state = effective(config, selected, catalog, free_pi)
    settings = config.setdefault('team', {})
    for key, stored in list(settings.get('roles', {}).items()):
        if stored.get('manual') and not state['roles'].get(key, {}).get('manual'):
            del settings['roles'][key]
            notice = tr('{papel}: el agente, modelo o esfuerzo ya no está disponible; se usa el recomendado.', papel=LABELS[key])
            notices = settings.setdefault('notices', [])
            if notice not in notices:
                notices.append(notice)


def reset(config):
    config.setdefault('team', {}).setdefault('enabled', False)
    config['team']['roles'] = {}
    config['team'].pop('notices', None)
