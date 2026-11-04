import copy
from datetime import datetime
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import goals


def data(**overrides):
    agents = [{'id': 'codex', 'name': 'Codex', 'enabled': True, 'installation': 'installed'},
              {'id': 'claude', 'name': 'Claude Code', 'enabled': True, 'installation': 'installed'}]
    return {'agents': agents,
            'components': [{'id': key, 'enabled': True,
                            'targets': [{'agent': agent['id'], 'enabled': True, 'verified': True}
                                        for agent in agents]}
                           for key in ('superpowers', 'memory', 'local-tools')],
            'memory': {'projects': [{}, {}, {}], 'backupSettings': {'enabled': False}, 'backupState': {}},
            'localTools': {'rows': [{'id': key, 'installation': 'installed', 'probeVerified': True}
                                    for key in ('rg', 'jq', 'yq', 'ast-grep', 'gitleaks', 'osv-scanner', 'shellcheck', 'ruff', 'hadolint')]},
            **overrides}


class GoalTests(unittest.TestCase):
    def test_memory_line_uses_singular_for_one_project(self):
        memory = {'projects': [{}], 'backupState': {}, 'backupSettings': {}}
        self.assertEqual(goals.memory_line(memory)[0], '1 proyecto · sin copias programadas')
        memory['backupState'] = {'lastError': 'x'}
        self.assertEqual(goals.memory_line(memory)[0], '1 proyecto · copia fallida')

    def setUp(self):
        self.config = {'tokensEnabled': True, 'securityEnabled': True}

    def test_setup_state_for_each_outcome(self):
        current = data()
        codex, claude = current['agents']
        self.assertEqual(goals.setup_state(codex, current['components']),
                         {'setupState': 'ready', 'missingComponents': [], 'setupIssues': []})
        self.assertEqual(goals.setup_state({**codex, 'enabled': False}, current['components'])['setupState'], 'disabled')
        current['components'][1]['targets'][0].update(verified=False, detail='falta')
        state = goals.setup_state(codex, current['components'])
        self.assertEqual((state['setupState'], state['missingComponents']), ('pending', ['memory']))
        self.assertEqual(state['setupIssues'], [{'component': 'memory', 'detail': 'falta'}])
        current['components'][1]['targets'][0]['lastError'] = 'roto'
        self.assertEqual(goals.setup_state(codex, current['components'])['setupState'], 'error')
        current['components'][1]['enabled'] = False
        self.assertEqual(goals.setup_state(codex, current['components'])['setupState'], 'ready')
        self.assertEqual(goals.setup_state({**claude, 'installation': 'missing'}, [])['missingComponents'], ['cli'])

    def test_unsupported_targets_are_not_missing(self):
        current = data()
        codex = current['agents'][0]
        current['components'][1]['targets'][0].update(enabled=False, verified=False, unsupported=True)
        self.assertEqual(goals.setup_state(codex, current['components'])['setupState'], 'ready')
        self.assertEqual(goals.connection_line(current['components'][1], [codex]), '')

    def test_quality_line_is_not_claimed_when_every_agent_is_unsupported(self):
        current = data()
        for component in current['components']:
            for target in component['targets']:
                target.update(enabled=False, verified=False, unsupported=True)
        rows = goals.rows(current, self.config)
        quality = next(row for row in rows if row['id'] == 'quality')
        self.assertEqual(quality['line'], 'No disponible en este agente')
        codex_only = data()
        self.assertEqual(next(row for row in goals.rows(codex_only, self.config) if row['id'] == 'quality')['line'],
                         'Plan, TDD y revisión')

    def test_repair_plan_separates_what_needs_an_installer(self):
        current = data()
        codex = {**current['agents'][0], 'missingComponents': ['cli']}
        self.assertEqual(goals.repair_plan([codex], [])[0], [])
        codex = {**current['agents'][0], 'missingComponents': ['memory', 'local-tools', 'superpowers']}
        targets = current['components']
        targets[0]['targets'][0].update(installed=False, detail='sin instalar')  # superpowers
        targets[1]['targets'][0].update(installed=True, blocked=True, detail='ajeno')  # memory
        targets[2]['targets'][0].update(installed=False, available=True)  # local-tools: only generated files
        repair, pending = goals.repair_plan([codex], targets)
        self.assertEqual(repair, [{'agent': 'codex', 'component': 'local-tools'}])
        self.assertEqual([row['component'] for row in pending], ['memory', 'superpowers'])

    def row(self, current, key):
        return next(row for row in goals.rows(current, self.config) if row['id'] == key)

    def summary(self, current):
        return goals.summary(current, goals.rows(current, self.config))

    def test_goal_rows_have_ordered_ids_names_and_boolean_preferences(self):
        rows = goals.rows(data(), self.config)
        self.assertEqual([(row['id'], row['name']) for row in rows],
                         [('quality', 'Calidad'), ('memory', 'Memoria compartida'),
                          ('tokens', 'Ahorro de tokens'), ('security', 'Seguridad')])
        for row in rows:
            self.assertEqual(set(row), {'id', 'name', 'enabled', 'state', 'line'})
            self.assertIs(row['enabled'], True)
            self.assertEqual(row['state'], 'ok')

    def test_all_connected_says_everything_works(self):
        self.assertEqual(self.summary(data()), {'text': 'Todo funcionando', 'page': '', 'state': 'ok'})

    def test_no_selected_agent_asks_to_select(self):
        current = data()
        for agent in current['agents']:
            agent.update(enabled=False, installation='missing')
        self.assertEqual(self.summary(current), {'text': 'Selecciona un agente', 'page': '', 'state': 'pending'})
        self.assertTrue(all(row['enabled'] for row in goals.rows(current, self.config)))
        self.assertEqual(self.summary(data(agents=[]))['text'], 'Selecciona un agente')

    def test_missing_selected_agent_wins(self):
        for installation in ('missing', 'launcher-only', 'missing-profile', 'error'):
            with self.subTest(installation=installation):
                current = data()
                current['agents'][0]['installation'] = installation
                current['components'][1]['targets'][0]['verified'] = False
                self.assertEqual(self.summary(current),
                                 {'text': 'Codex: sin instalar', 'page': 'codex', 'state': 'pending'})

    def test_unverified_target_names_goal_and_agent(self):
        current = data()
        current['components'][1]['targets'][0]['verified'] = False
        self.assertEqual(self.row(current, 'memory')['line'], 'Codex sin conectar')
        self.assertEqual(self.summary(current),
                         {'text': 'Memoria compartida: Codex sin conectar', 'page': 'memory', 'state': 'pending'})

    def test_english_summary_translates_goal_name_and_line(self):
        import i18n
        current = data()
        current['components'][1]['targets'][0].update(verified=False, needsReconcile=True)
        with i18n.using('en'):
            text = goals.summary(current, goals.rows(current, self.config))['text']
        self.assertEqual(text, 'Shared memory · Codex: checking connection…')

    def test_version_only_change_shows_checking_not_unconnected(self):
        current = data()
        current['components'][1]['targets'][0].update(verified=False, needsReconcile=True)
        self.assertEqual(self.row(current, 'memory')['line'], 'Codex: comprobando conexión…')

    def test_checking_does_not_hide_a_real_failure_of_another_agent(self):
        current = data()
        current['components'][1]['targets'][0].update(verified=False, needsReconcile=True)
        current['components'][1]['targets'][1].update(verified=False)
        self.assertEqual(self.row(current, 'memory')['line'], 'Claude Code sin conectar')
        current['components'][1]['targets'][1].update(enabled=False)
        self.assertEqual(self.row(current, 'memory')['line'], 'Claude Code sin conectar')

    def test_disabled_target_with_mark_is_not_checking(self):
        current = data()
        current['components'][1]['targets'][0].update(enabled=False, verified=False, needsReconcile=True)
        self.assertEqual(self.row(current, 'memory')['line'], 'Codex sin conectar')

    def test_uninstalled_cli_is_never_marked_checking(self):
        import harness
        agent = {'id': 'codex', 'name': 'Codex', 'binary': '/fixture/codex', 'version': '2',
                 'installation': 'missing', 'enabled': True}
        config = {'superpowers': {'codex': {'agentBinary': '/fixture/codex', 'agentVersion': '1'}}}
        inspected = {'verified': True, 'enabled': True, 'installed': True, 'detail': 'ok'}
        with patch.object(harness.native_plugins, 'inspect', side_effect=lambda *a, **k: dict(inspected)):
            target = harness.component_status('superpowers', config, [agent])['targets'][0]
        self.assertFalse(target.get('needsReconcile'))

    def _reconcile_fixture(self):
        import harness
        agent = {'id': 'codex', 'name': 'Codex', 'binary': '/fixture/codex', 'version': '2',
                 'installation': 'installed', 'enabled': True}
        config = {'superpowers': {'codex': {'agentBinary': '/fixture/codex', 'agentVersion': '1', 'digest': 'abc'}}}

        def inspect(_agent, receipt=None):
            return {'verified': bool((receipt or {}).get('digest')), 'enabled': True, 'installed': True, 'detail': 'ok'}

        def target():
            with patch.object(harness.native_plugins, 'inspect', side_effect=inspect):
                return harness.component_status('superpowers', config, [agent])['targets'][0]

        def reconcile(apply):
            current = {'agents': [{**agent, 'missingComponents': ['superpowers']}],
                       'components': [{'id': 'superpowers', 'enabled': True, 'targets': [target()]}]}
            with patch.object(harness, 'status', return_value=current), \
                 patch.object(harness, 'mise_inventory', return_value=({}, None)), \
                 patch.object(harness, 'agent_tool', return_value=None), \
                 patch.object(harness, 'apply_component', side_effect=apply), \
                 patch.object(harness, 'save_config'):
                return harness.reconcile_selected(config)
        return agent, config, target, reconcile

    def test_failed_reconcile_records_error_and_line_becomes_unconnected(self):
        import harness
        for error in (KeyError('installPath'), TypeError('x'), AttributeError('x')):
            with self.subTest(error=type(error).__name__):
                agent, config, target, reconcile = self._reconcile_fixture()
                self.assertTrue(target()['needsReconcile'])
                self.assertEqual(goals.connection_line({'targets': [target()]}, [agent]), 'Codex: comprobando conexión…')
                report = reconcile(Mock(side_effect=error))
                self.assertEqual(len(report['errors']), 1)
                saved = config['superpowers']['codex']
                self.assertNotIn('digest', saved)
                self.assertNotIn('installPath', saved['lastError'])
                self.assertIn('no es compatible', saved['lastError'])
                self.assertEqual(report['errors'][0]['detail'], saved['lastError'])
                after = target()
                self.assertFalse(after.get('needsReconcile'))
                self.assertEqual(goals.connection_line({'targets': [after]}, [agent]), 'Codex sin conectar')

    def test_successful_reconcile_clears_the_mark(self):
        agent, config, target, reconcile = self._reconcile_fixture()
        self.assertTrue(target()['needsReconcile'])

        def apply(cfg, key, agent_id, operation, tool):
            cfg[key][agent_id].update(agentBinary='/fixture/codex', agentVersion='2', digest='abc')
        report = reconcile(apply)
        self.assertEqual(report['errors'], [])
        self.assertEqual(len(report['repaired']), 1)
        after = target()
        self.assertTrue(after['verified'])
        self.assertFalse(after.get('needsReconcile'))
        self.assertEqual(goals.connection_line({'targets': [after]}, [agent]), '')

    def test_component_status_flags_version_only_change_but_not_content_change(self):
        import harness
        agent = {'id': 'codex', 'name': 'Codex', 'binary': '/fixture/codex', 'version': '2',
                 'installation': 'installed', 'enabled': True}
        config = {'superpowers': {'codex': {'agentBinary': '/fixture/codex', 'agentVersion': '1'}}}
        for inspected, expected in (({'verified': True, 'enabled': True, 'installed': True, 'detail': 'ok'}, True),
                                    ({'verified': False, 'enabled': True, 'installed': True, 'detail': 'digest distinto'}, False)):
            with self.subTest(inspected=inspected['verified']), patch.object(harness.native_plugins, 'inspect', side_effect=lambda *a, **k: dict(inspected)):
                target = harness.component_status('superpowers', config, [agent])['targets'][0]
                self.assertFalse(target['verified'])
                self.assertEqual(bool(target.get('needsReconcile')), expected)

    def test_connection_requires_enabled_and_verified_for_every_selected_agent(self):
        for component, keys in ((0, ('quality',)), (1, ('memory',)), (2, ('tokens', 'security'))):
            for change in ({'enabled': False}, {'verified': False}):
                with self.subTest(component=component, change=change):
                    current = data()
                    current['components'][component]['targets'][1].update(change)
                    for key in keys:
                        row = self.row(current, key)
                        self.assertEqual(row['line'], 'Claude Code sin conectar')
                        self.assertEqual(row['state'], 'pending')

    def test_first_selected_unconnected_agent_is_named_even_without_target(self):
        current = data()
        current['components'][0]['targets'] = []
        self.assertEqual(self.row(current, 'quality')['line'], 'Codex sin conectar')
        current['agents'][0]['enabled'] = False
        self.assertEqual(self.row(current, 'quality')['line'], 'Claude Code sin conectar')

    def test_unselected_agent_does_not_block_connection_or_summary(self):
        current = data()
        current['agents'][0].update(enabled=False, installation='missing')
        for component in current['components']:
            component['targets'][0].update(enabled=False, verified=False)
        self.assertEqual(self.summary(current), {'text': 'Todo funcionando', 'page': '', 'state': 'ok'})

    def test_pending_goals_follow_row_order_before_backup_warnings(self):
        current = data()
        current['memory']['backupState']['lastError'] = 'destination missing'
        current['localTools']['rows'][0]['probeVerified'] = False
        self.assertEqual(self.summary(current),
                         {'text': 'Ahorro de tokens: 3 listas · 1 pendiente',
                          'page': 'tokens', 'state': 'pending'})
        for component in current['components'][:2]:
            component['targets'][0]['verified'] = False
        self.assertEqual(self.summary(current)['page'], 'quality')
        current['components'][0]['targets'][0]['verified'] = True
        self.assertEqual(self.summary(current)['page'], 'memory')

    def test_failed_backup_warning(self):
        current = data()
        current['memory']['backupState']['lastError'] = 'destination missing'
        self.assertEqual(self.row(current, 'memory')['line'], '3 proyectos · copia fallida')
        self.assertEqual(self.row(current, 'memory')['state'], 'warning')
        self.assertEqual(self.summary(current),
                         {'text': 'Memoria compartida: copia fallida', 'page': 'memory', 'state': 'warning'})
        current['memory']['backupState'] = {}
        self.assertEqual(self.summary(current),
                         {'text': 'Todo funcionando', 'page': '', 'state': 'ok'})

    def test_tool_line_groups_by_state(self):
        current = data()
        current['localTools']['rows'][2].update(installation='missing', probeVerified=False)
        row = self.row(current, 'tokens')
        self.assertEqual(row['line'], '3 listas · 1 falta')
        self.assertEqual(row['state'], 'pending')

    def test_tool_line_groups_disabled_tools_after_missing_without_pending(self):
        current = data()
        current['localTools']['rows'][0]['mode'] = 'off'
        row = self.row(current, 'tokens')
        self.assertEqual(row['line'], '3 listas · 1 desactivada')
        self.assertEqual(row['state'], 'ok')
        current['localTools']['rows'][2].update(installation='missing', probeVerified=False)
        row = self.row(current, 'tokens')
        self.assertEqual(row['line'], '2 listas · 1 falta · 1 desactivada')
        self.assertEqual(row['state'], 'pending')

    def test_tool_counts_separate_unprobed_from_missing(self):
        current = data()
        current['localTools']['rows'].reverse()
        tools = {row['id']: row for row in current['localTools']['rows']}
        tools['rg']['probeVerified'] = False
        tools['ast-grep'].update(installation='missing', probeVerified=False)
        tools['shellcheck']['probeVerified'] = False
        self.assertEqual(self.row(current, 'tokens')['line'], '2 listas · 1 pendiente · 1 falta')
        self.assertEqual(self.row(current, 'security')['line'], '4 listas · 1 pendiente')
        current['localTools']['rows'] = []
        self.assertEqual(self.row(current, 'tokens')['line'], '4 faltan')

    def test_security_counts_ready_pending_missing_and_disabled(self):
        current = data()
        tools = {row['id']: row for row in current['localTools']['rows']}
        self.assertEqual(goals.tool_line('security', current['localTools']['rows']), ('5 listas', 'ok'))
        tools['gitleaks'].update(installation='missing', probeVerified=False)
        self.assertEqual(goals.tool_line('security', current['localTools']['rows']), ('4 listas · 1 falta', 'pending'))
        tools['osv-scanner']['probeVerified'] = False
        self.assertEqual(goals.tool_line('security', current['localTools']['rows']), ('3 listas · 1 pendiente · 1 falta', 'pending'))
        tools['shellcheck']['mode'] = 'off'
        tools['ruff']['mode'] = 'off'
        self.assertEqual(goals.tool_line('security', current['localTools']['rows']),
                         ('1 lista · 1 pendiente · 1 falta · 2 desactivadas', 'pending'))
        tools['gitleaks'].update(installation='installed', probeVerified=False)
        self.assertEqual(goals.tool_line('security', current['localTools']['rows']),
                         ('1 lista · 2 pendientes · 2 desactivadas', 'pending'))
        tools['osv-scanner']['installation'] = 'missing'
        tools['gitleaks']['installation'] = 'missing'
        self.assertEqual(goals.tool_line('security', current['localTools']['rows']),
                         ('1 lista · 2 faltan · 2 desactivadas', 'pending'))

    def test_uninstalled_optional_tools_do_not_count_in_any_mode(self):
        for installation in ('missing', 'launcher-only', 'error'):
            for mode in ('auto', 'on', 'off'):
                with self.subTest(installation=installation, mode=mode):
                    current = data()
                    current['localTools']['rows'].extend(
                        {'id': key, 'installation': installation, 'probeVerified': False, 'mode': mode}
                        for key in ('semgrep',))
                    self.assertEqual(goals.tool_line('security', current['localTools']['rows']), ('5 listas', 'ok'))

    def test_installed_optional_tools_count_and_need_probes(self):
        current = data()
        current['localTools']['rows'].extend([
            {'id': 'semgrep', 'installation': 'installed', 'probeVerified': False}])
        self.assertEqual(goals.tool_line('security', current['localTools']['rows']), ('5 listas · 1 pendiente', 'pending'))
        current['localTools']['rows'][-1]['mode'] = 'off'
        self.assertEqual(goals.tool_line('security', current['localTools']['rows']), ('5 listas · 1 desactivada', 'ok'))

    def test_legacy_audit_does_not_change_security_or_summary(self):
        current = data(securityAudit={'findingCount': 2})
        self.config['lastSecurityAudit'] = {'findingCount': 3}
        self.assertEqual(self.row(current, 'security')['line'], '5 listas')
        self.assertEqual(self.row(current, 'security')['state'], 'ok')
        self.assertEqual(self.summary(current), {'text': 'Todo funcionando', 'page': '', 'state': 'ok'})

    def test_memory_lines(self):
        current = data()
        memory = current['memory']
        memory['backupState']['lastSuccess'] = datetime(2026, 10, 29, 12).timestamp()
        self.assertEqual(self.row(current, 'memory')['line'], '3 proyectos · sin copias programadas')
        memory['backupSettings']['enabled'] = True
        self.assertEqual(self.row(current, 'memory')['line'], '3 proyectos · copia 2026-10-29')
        memory['backupState'] = {}
        self.assertEqual(self.row(current, 'memory')['line'], '3 proyectos · sin copias programadas')
        memory['projects'] = []
        self.assertEqual(self.row(current, 'memory')['line'], '0 proyectos · sin copias programadas')

    def test_quality_lines(self):
        current = data()
        self.assertEqual(self.row(current, 'quality')['line'], 'Plan, TDD y revisión')
        current['components'][0]['enabled'] = False
        self.assertEqual(self.row(current, 'quality'),
                         {'id': 'quality', 'name': 'Calidad', 'enabled': False, 'state': 'off', 'line': 'Desactivado'})

    def test_disabled_goals_ignore_connection_and_backup_problems(self):
        current = data()
        current['memory']['backupState']['lastError'] = 'destination missing'
        for component in current['components']:
            component.update(enabled=False, targets=[])
        self.config.update(tokensEnabled=False, securityEnabled=False)
        for row in goals.rows(current, self.config):
            self.assertIs(row['enabled'], False)
            self.assertEqual(row['state'], 'off')
            self.assertEqual(row['line'], 'Desactivado')
        self.assertEqual(self.summary(current), {'text': 'Todo funcionando', 'page': '', 'state': 'ok'})

    def test_disabled_goals_with_active_connections_report_pending_deactivation(self):
        current = data()
        current['components'][1].update(enabled=False, activeCount=1)
        self.assertEqual(self.row(current, 'memory'),
                         {'id': 'memory', 'name': 'Memoria compartida', 'enabled': False,
                          'state': 'pending', 'line': 'Desactivación pendiente'})
        self.assertEqual(self.summary(current),
                         {'text': 'Memoria compartida: Desactivación pendiente',
                          'page': 'memory', 'state': 'pending'})

    def test_disabled_tool_goals_report_pending_only_when_bridge_is_off(self):
        self.config.update(tokensEnabled=False, securityEnabled=False)
        current = data()
        local_tools = current['components'][2]
        local_tools.update(enabled=False, activeCount=2)
        for key in ('tokens', 'security'):
            self.assertEqual(self.row(current, key)['state'], 'pending')
            self.assertEqual(self.row(current, key)['line'], 'Desactivación pendiente')
        current['components'][2]['enabled'] = True
        self.assertEqual(self.row(current, 'tokens')['state'], 'off')
        self.assertEqual(self.row(current, 'security')['state'], 'off')

    def test_tool_goal_preferences_use_groups_including_legacy_fallback(self):
        for config, expected in (({'local-toolsEnabled': True}, (True, True)),
                                 ({}, (False, False)),
                                 ({'tokensEnabled': True, 'securityEnabled': False}, (True, False))):
            with self.subTest(config=config):
                rows = goals.rows(data(), config)
                self.assertEqual(tuple(row['enabled'] for row in rows[2:]), expected)

    def test_rows_and_summary_leave_inputs_unchanged(self):
        current = data()
        original = copy.deepcopy(current)
        config = copy.deepcopy(self.config)
        rows = goals.rows(current, self.config)
        original_rows = copy.deepcopy(rows)
        goals.summary(current, rows)
        self.assertEqual(current, original)
        self.assertEqual(self.config, config)
        self.assertEqual(rows, original_rows)


if __name__ == '__main__':
    unittest.main()
