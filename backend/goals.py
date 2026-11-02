"""Pure goal rows and panel summary derived from the native status snapshot."""

from i18n import tr

from datetime import datetime

import local_catalog


GOALS = (('quality', tr('Calidad'), 'superpowers'),
         ('memory', tr('Memoria compartida'), 'memory'),
         ('tokens', tr('Ahorro de tokens'), 'local-tools'),
         ('security', tr('Seguridad'), 'local-tools'))
NEEDS_INSTALLER = ('superpowers', 'memory')


def connection_line(component: dict, agents: list[dict]) -> str:
    if not agents:
        return tr('Selecciona un agente')
    targets = {target['agent']: target for target in component.get('targets', [])}
    for agent in agents:
        target = targets.get(agent['id'], {})
        if not (target.get('enabled') and target.get('verified')):
            return tr('{nombre} sin conectar', nombre=agent['name'])
    return ''


def setup_state(agent: dict, components: list[dict], *, models_missing: bool = False) -> dict:
    """Preparation of one profile: an unselected profile has nothing to repair."""
    if not agent.get('enabled'):
        return {'setupState': 'disabled', 'missingComponents': [], 'setupIssues': []}
    missing, issues, failed = [], [], False
    if agent.get('installation') != 'installed':
        missing.append('cli')
    for component in components:
        if not component.get('enabled'):
            continue
        target = next((row for row in component.get('targets', []) if row.get('agent') == agent.get('id')), {})
        if target.get('enabled') and target.get('verified'):
            continue
        missing.append(component['id'])
        issues.append({'component': component['id'], 'detail': target.get('detail', '')})
        failed = failed or bool(target.get('blocked') or target.get('lastError'))
    if models_missing:
        missing.append('models')
        issues.append({'component': 'team', 'detail': tr('El equipo no tiene modelos: activa Nous en la página de Pi y actualiza su catálogo.')})
    state = 'error' if failed else 'pending' if missing else 'ready'
    return {'setupState': state, 'missingComponents': missing, 'setupIssues': issues}


def repair_plan(agents: list[dict], components: list[dict]) -> tuple[list[dict], list[dict]]:
    """What opening the panel may repair, and what needs an explicit preparation instead."""
    by_id = {component['id']: component for component in components}
    repair, pending = [], []
    for agent in agents:
        for key in agent.get('missingComponents', []):
            target = next((row for row in by_id.get(key, {}).get('targets', []) if row.get('agent') == agent['id']), {})
            if (key not in by_id or target.get('available') is False or target.get('blocked')
                    or (key in NEEDS_INSTALLER and not target.get('installed'))):
                pending.append({'agent': agent['id'], 'component': key, 'detail': target.get('detail', '')})
            else:
                repair.append({'agent': agent['id'], 'component': key})
    return repair, pending


def memory_line(memory: dict) -> tuple[str, str]:
    count = len(memory.get('projects', []))
    backup = memory.get('backupState', {})
    if backup.get('lastError'):
        return (tr('1 proyecto · copia fallida') if count == 1 else tr('{cantidad} proyectos · copia fallida', cantidad=count)), 'warning'
    if memory.get('backupSettings', {}).get('enabled') and backup.get('lastSuccess') is not None:
        date = datetime.fromtimestamp(backup['lastSuccess']).date().isoformat()
        return (tr('1 proyecto · copia {fecha}', fecha=date) if count == 1 else tr('{cantidad} proyectos · copia {fecha}', cantidad=count, fecha=date)), 'ok'
    return (tr('1 proyecto · sin copias programadas') if count == 1 else tr('{cantidad} proyectos · sin copias programadas', cantidad=count)), 'ok'


def tool_line(group: str, tools: list[dict]) -> tuple[str, str]:
    by_id = {tool['id']: tool for tool in tools}
    states = {'lista': 0, 'pendiente': 0, 'falta': 0, 'desactivada': 0}
    for key in local_catalog.GROUPS[group]:
        tool = by_id.get(key, {})
        if key in local_catalog.OPTIONAL and tool.get('installation') != 'installed':
            continue
        state = ('desactivada' if tool.get('mode') == 'off' else
                 'falta' if tool.get('installation') != 'installed' else
                 'lista' if tool.get('probeVerified') else 'pendiente')
        states[state] += 1
    labels = {'lista': (tr('{cantidad} lista', cantidad=states['lista']), tr('{cantidad} listas', cantidad=states['lista'])),
              'pendiente': (tr('{cantidad} pendiente', cantidad=states['pendiente']), tr('{cantidad} pendientes', cantidad=states['pendiente'])),
              'falta': (tr('{cantidad} falta', cantidad=states['falta']), tr('{cantidad} faltan', cantidad=states['falta'])),
              'desactivada': (tr('{cantidad} desactivada', cantidad=states['desactivada']), tr('{cantidad} desactivadas', cantidad=states['desactivada']))}
    line = ' · '.join(labels[state][count != 1] for state, count in states.items() if count)
    return line, 'pending' if states['pendiente'] or states['falta'] else 'ok'


def rows(data: dict, config: dict) -> list[dict]:
    components = {component['id']: component for component in data.get('components', [])}
    agents = [agent for agent in data.get('agents', []) if agent.get('enabled')]
    groups = local_catalog.group_state(config)
    result = []
    for key, name, component_id in GOALS:
        component = components.get(component_id, {})
        enabled = groups[key] if key in groups else bool(component.get('enabled'))
        line, state = tr('Desactivado'), 'off'
        if (not enabled and not component.get('enabled') and component.get('activeCount', 0) > 0
                and (key not in groups or not any(groups.values()))):
            line, state = tr('Desactivación pendiente'), 'pending'
        if enabled:
            line = connection_line(component, agents)
            state = 'pending' if line else 'ok'
            if not line:
                if key == 'quality':
                    line = tr('Plan, TDD y revisión')
                elif key == 'memory':
                    line, state = memory_line(data.get('memory', {}))
                else:
                    line, state = tool_line(key, data.get('localTools', {}).get('rows', []))
        result.append({'id': key, 'name': name, 'enabled': enabled, 'state': state, 'line': line})
    return result


def summary(data: dict, rows: list[dict]) -> dict:
    agents = [agent for agent in data.get('agents', []) if agent.get('enabled')]
    if not agents:
        return {'text': tr('Selecciona un agente'), 'page': '', 'state': 'pending'}
    for agent in agents:
        if agent.get('installation') != 'installed':
            return {'text': tr('{nombre}: sin instalar', nombre=agent['name']), 'page': agent['id'], 'state': 'pending'}
    for row in rows:
        if row['state'] == 'pending':
            return {'text': tr('{nombre}: {estado}', nombre=row['name'], estado=row['line']), 'page': row['id'], 'state': 'pending'}
    if any(row['id'] == 'memory' and row['enabled'] and row['state'] == 'warning' for row in rows):
        return {'text': tr('Memoria compartida: copia fallida'), 'page': 'memory', 'state': 'warning'}
    return {'text': tr('Todo funcionando'), 'page': '', 'state': 'ok'}
