import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_snapshot import snapshot
import harness
import native_plugins
import native_team
import team


class NativeTeamTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        env = patch.dict(os.environ, {"LC_ALL": "es", 'HOME': str(self.root), 'PATH': '', 'XDG_CONFIG_HOME': str(self.root / 'config'),
                                     'XDG_DATA_HOME': str(self.root / 'data'), 'CODEX_HOME': str(self.root / '.codex'),
                                     'CLAUDE_CONFIG_DIR': str(self.root / '.claude'), 'PI_CODING_AGENT_DIR': str(self.root / '.pi/agent')})
        env.start(); self.addCleanup(env.stop)
        self.config = {'schemaVersion': 1, 'agents': {'claude': True, 'codex-2': True, 'codex': True}}
        self.settings = harness.config_path()

    def test_startup_block_states_triggers_and_review_command(self):
        paths = native_team.layout('claude')
        block = native_team.instruction('', paths, True, team=self.state())
        for wanted in ('tú diriges', 'una o dos líneas', 'encarga Programar', 'encarga siempre Revisar',
                       "-s read-only -m gpt-6.1-sol", 'Superpowers', 'no cuenta como revisión independiente'):
            self.assertIn(wanted, block)
        self.assertEqual(native_team.instruction(block, paths, False, team=self.state()), '')

    def test_security_skill_carries_the_lessons_without_privilege_words(self):
        import native_local_tools as tools
        paths = tools.layout('claude')
        skill = tools.text(paths, self.settings, groups=('security',))
        for wanted in ('Text.PlainText', 'argumentos posicionales', '/proc/*/cmdline', '0600', 'mktemp -d',
                       'git status', 'fijadas por SHA', 'test de regresión', 'sube solo lo necesario',
                       'ignorados por Git', 'git diff --cached --stat'):
            self.assertIn(wanted, skill)
        self.assertNotIn('sudo', skill.lower())
        self.assertNotIn('Text.PlainText', tools.text(paths, self.settings, groups=('tokens',)))
        self.assertIn('temporales', tools.instruction('', paths, True, groups=('security',)))

    def state(self, selected=None):
        selected = selected if selected is not None else {'claude', 'codex-2', 'codex'}
        return {**team.effective(self.config, selected, {}, []), 'selected': sorted(selected)}

    def content(self, selected=None, state=None):
        return native_team.text(native_team.layout('claude'), self.settings, state or self.state(selected))

    def run_main(self, arguments):
        output = io.StringIO()
        with patch.object(harness, 'status', return_value={}), contextlib.redirect_stdout(output):
            result = harness.main(arguments)
        return result, json.loads(output.getvalue())

    def test_skill_roles_commands_and_rules(self):
        text = self.content()
        for value in (*team.LABELS.values(), 'gpt-6.1-sol', 'gpt-6-luna', 'opus', '-s workspace-write',
                      '-s read-only', '</dev/null', '--effort high --permission-mode acceptEdits', '--permission-mode plan',
                      'hazlo tú mismo', 'worktree', 'no hagas commits', 'nunca de pago'):
            self.assertIn(value, text)
        for forbidden in ('bypassPermissions', 'danger-full-access', '--dangerously', '--yolo'):
            self.assertNotIn(forbidden, text)
        self.assertLess(text.index('CODEX_HOME=' + str(self.root / '.codex-2')),
                        text.index('CODEX_HOME=' + str(self.root / '.codex') + ' '))

    def test_a_task_that_does_not_answer_is_not_retried_forever(self):
        text = self.content()
        for wanted in ('intenta lanzarlo una sola vez', 'timeout 60', 'sandbox', 'di qué papel quedó pendiente'):
            self.assertIn(wanted, text)

    def test_a_delegated_task_does_not_delegate_again(self):
        self.assertIn('no encargues nada a otros modelos', self.content())

    def test_only_codex_2_never_uses_other_account(self):
        text = self.content({'codex-2'})
        self.assertIn('CODEX_HOME=' + str(self.root / '.codex-2'), text)
        self.assertNotIn('CODEX_HOME=' + str(self.root / '.codex') + ' ', text)
        self.assertIn('cuota', text)

    def test_claude_2_profile_has_its_own_layout_and_commands(self):
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.root / '.claude-2')}):
            second = native_team.layout('claude')
        first = native_team.layout('claude')
        self.assertEqual((first['profile'], second['profile']), ('claude', 'claude-2'))
        self.assertNotEqual(first['source'], second['source'])
        self.assertEqual(second['target'], self.root / '.claude-2/skills/ai-team-roles')

    def test_only_claude_2_never_uses_the_other_claude_account(self):
        text = self.content({'claude-2', 'codex'})
        line = [row for row in text.splitlines() if '--permission-mode plan' in row]
        self.assertTrue(line)
        self.assertTrue(all('CLAUDE_CONFIG_DIR=' + str(self.root / '.claude-2') + ' /usr/bin/python3' in row for row in line), line)
        self.assertIn('Claude Code-2 primero', text)
        both = self.content({'claude-2', 'claude', 'codex'})
        self.assertLess(both.index('CLAUDE_CONFIG_DIR=' + str(self.root / '.claude-2')), both.index('env -u CLAUDE_CONFIG_DIR /usr/bin/python3'))
        main = [row for row in self.content({'claude', 'codex'}).splitlines() if '--agent claude' in row]
        self.assertTrue(main)
        self.assertTrue(all('env -u CLAUDE_CONFIG_DIR /usr/bin/python3' in row and '.claude-2' not in row for row in main), main)

    def test_mixed_selections_emit_only_selected_profiles(self):
        for selected, codex_homes, claude_homes in (
                ({'claude', 'codex-2'}, {'.codex-2'}, {'main'}),
                ({'claude-2', 'codex'}, {'.codex'}, {'second'}),
                ({'claude-2', 'claude', 'codex-2', 'codex'}, {'.codex-2', '.codex'}, {'main', 'second'}),
                ({'claude-2', 'codex-2'}, {'.codex-2'}, {'second'})):
            text = self.content(selected)
            found = {home for home in ('.codex-2', '.codex')
                     if re.search('CODEX_HOME=' + re.escape(str(self.root / home)) + ' ', text)}
            self.assertEqual(found, codex_homes, selected)
            second = 'CLAUDE_CONFIG_DIR=' + str(self.root / '.claude-2') + ' /usr/bin/python3' in text
            main = 'env -u CLAUDE_CONFIG_DIR /usr/bin/python3' in text
            self.assertEqual({name for name, on in (('main', main), ('second', second)) if on}, claude_homes, selected)

    def test_claude_2_gets_no_usage_guard_hook(self):
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.root / '.claude-2')}), \
             patch.object(native_team, 'set_guard_hook') as hook, \
             patch.object(native_team.native_skills, 'loaded', return_value=True), \
             patch.object(native_team.shared_memory, 'write_claude_startup'), \
             patch.object(native_team, 'inspect', return_value={'enabled': False, 'verified': False}):
            native_team.apply('claude', '/bin/claude', 'disable', settings_file=self.settings, team=self.state({'claude-2'}))
        hook.assert_not_called()
        with patch.object(native_team, 'set_guard_hook') as hook, \
             patch.object(native_team.shared_memory, 'write_claude_startup'), \
             patch.object(native_team, 'inspect', return_value={'enabled': False, 'verified': False}):
            native_team.apply('claude', '/bin/claude', 'disable', settings_file=self.settings, team=self.state({'claude'}))
        hook.assert_called_once()

    def test_pi_next_to_another_cli_stays_restricted_to_reading(self):
        text = self.content({'claude', 'pi'})
        self.assertIn('Pi no tiene sandbox', text)
        self.assertIn('solo lectura', text)
        self.assertIn('es una instrucción, no aislamiento efectivo', text)
        self.assertIn('no delegues Planificar ni Programar a Pi', text)

    def only_pi_state(self):
        return {**team.effective(self.config, {'pi'}, {'nousEffort': ['nous/a:free']}, ['nous/a:free', 'nous/b:free']), 'selected': ['pi']}

    def test_only_pi_skill_warns_that_nous_models_have_no_sandbox(self):
        text = self.content(state=self.only_pi_state())
        self.assertIn('los modelos de Nous no tienen sandbox ni aprobación por herramienta', text)
        self.assertIn('no es aislamiento', text)
        self.assertNotIn('no delegues Planificar ni Programar a Pi', text)
        self.assertNotIn('encargo pendiente', text)

    def test_only_pi_recipes_cover_programar_and_revisar(self):
        text = self.content(state=self.only_pi_state())
        code = text[text.index('Programar:\n```sh'):]
        self.assertIn('pi -p --provider nous --model a:free --thinking high', code[:code.index('```', 12)])
        review = text[text.index('Revisar:\n```sh'):]
        self.assertIn('pi -p --provider nous --model b:free', review[:review.index('```', 12)])

    def test_only_pi_startup_block_uses_the_review_recipe(self):
        block = native_team.instruction('', native_team.layout('pi'), True, team=self.only_pi_state())
        self.assertIn('--model b:free', block)
        self.assertIn('sin sandbox', block)

    def test_pi_code_role_generates_native_command(self):
        row = {'agent': 'pi', 'model': 'nous/odd$(touch x) name:free', 'effort': 'high'}
        command = native_team.role_commands('code', row, [], only_pi=True)[0]
        self.assertIn("--provider nous --model 'odd$(touch x) name:free' --thinking high", command)
        self.assertIn("'<encargo>'", command)
        self.assertTrue(command.endswith('</dev/null'))

    def test_pi_code_role_stays_pending_next_to_another_cli(self):
        row = {'agent': 'pi', 'model': 'nous/a:free', 'effort': ''}
        self.assertIn('pendiente', native_team.role_commands('code', row, [])[0])

    def test_team_set_round_trip_and_recommend_preserves_foreign(self):
        self.config['foreign'] = 'keep'; harness.save_config(self.config)
        code, reply = self.run_main(['team-set', 'simple', 'claude', 'haiku'])
        self.assertEqual(code, 0, reply)
        stored = harness.load_config()
        self.assertEqual(stored['team']['roles']['simple'], dict(agent='claude', model='haiku', effort='medium', manual=True))
        self.assertEqual(stored['foreign'], 'keep')
        self.assertEqual(self.run_main(['team-recommend'])[0], 0)
        self.assertEqual(harness.load_config()['team']['roles'], {})

    def test_team_set_pair_saves_both_roles_in_one_command(self):
        self.config['agents'] = {'claude': True}; harness.save_config(self.config)
        code, reply = self.run_main(['team-set-pair', 'claude', 'opus', 'claude', 'sonnet'])
        self.assertEqual(code, 0, reply)
        roles = harness.load_config()['team']['roles']
        self.assertEqual((roles['plan']['model'], roles['review']['model']), ('opus', 'sonnet'))

    def test_every_team_edit_resyncs_only_when_enabled(self):
        self.config['team'] = {'enabled': True, 'roles': {}}; harness.save_config(self.config)
        with patch.object(harness, 'manage_component') as manage:
            self.assertEqual(self.run_main(['team-set', 'simple', 'claude', 'haiku'])[0], 0)
            self.assertEqual(self.run_main(['team-recommend'])[0], 0)
            self.assertEqual(manage.call_count, 2)
            manage.assert_called_with(unittest.mock.ANY, 'team', 'sync')
        self.config['team']['enabled'] = False; harness.save_config(self.config)
        with patch.object(harness, 'manage_component') as manage:
            self.assertEqual(self.run_main(['team-recommend'])[0], 0)
            manage.assert_not_called()

    def test_refreshing_the_nous_catalog_resyncs_the_team_only_when_enabled(self):
        self.config['agents'] = {'claude': True, 'pi': True}
        self.config['piProviders'] = {'nous': True}
        self.config['team'] = {'enabled': True, 'roles': {}}; harness.save_config(self.config)
        with patch.object(harness.native_pi, 'refresh_nous', return_value={}), patch.object(harness, 'manage_component') as manage:
            self.assertEqual(self.run_main(['pi-nous-refresh'])[0], 0)
            manage.assert_called_once_with(unittest.mock.ANY, 'team', 'sync')
        self.config['team']['enabled'] = False; harness.save_config(self.config)
        with patch.object(harness.native_pi, 'refresh_nous', return_value={}), patch.object(harness, 'manage_component') as manage:
            self.assertEqual(self.run_main(['pi-nous-refresh'])[0], 0)
            manage.assert_not_called()

    def test_team_all_uses_managed_component(self):
        harness.save_config(self.config)
        for action in ('enable', 'disable', 'verify', 'sync'):
            with patch.object(harness, 'manage_component') as manage:
                self.assertEqual(self.run_main(['team-all', action])[0], 0)
                manage.assert_called_once_with(unittest.mock.ANY, 'team', action)

    def test_model_refresh_uses_first_selected_profile_and_preserves_cache_on_failure(self):
        self.config['teamModels'] = {'codex': [{'id': 'old'}], 'checkedAt': 'before'}
        harness.save_config(self.config)
        tool = {'installation': 'installed', 'binary': '/codex', 'version': '1'}
        observed = []
        def request(binary, method, params):
            observed.append((binary, method, params, os.environ['CODEX_HOME']))
            raise native_plugins.PluginError('Sin cuota')
        with patch.object(harness, 'mise_inventory', return_value=({}, '')), patch.object(harness, 'agent_tool', return_value=tool), \
                patch.object(native_plugins, 'codex_request', side_effect=request):
            code, reply = self.run_main(['team-models'])
        self.assertEqual(code, 0, reply)
        self.assertIn('aviso', reply['message'].lower())
        self.assertEqual(harness.load_config()['teamModels'], self.config['teamModels'])
        self.assertEqual(observed, [('/codex', 'model/list', {}, str(self.root / '.codex-2'))])

    def test_model_refresh_success_caches_models_and_syncs(self):
        self.config['team'] = {'enabled': True, 'roles': {}}; harness.save_config(self.config)
        rows = [{'id': 'model', 'supportedReasoningEfforts': [{'reasoningEffort': 'high'}]}]
        with patch.object(harness, 'mise_inventory', return_value=({}, '')), \
                patch.object(harness, 'agent_tool', return_value={'installation': 'installed', 'binary': '/codex'}), \
                patch.object(native_plugins, 'codex_request', return_value={'data': rows}), \
                patch.object(harness, 'manage_component') as manage:
            self.assertEqual(self.run_main(['team-models'])[0], 0)
        self.assertEqual(harness.load_config()['teamModels']['codex'], rows)
        self.assertTrue(harness.load_config()['teamModels']['checkedAt'])
        manage.assert_called_once_with(unittest.mock.ANY, 'team', 'sync')

    def test_component_options_and_status_never_query_models(self):
        with patch.object(native_plugins, 'codex_request', side_effect=AssertionError('no CLI requests')), \
                patch.object(harness, 'mise_inventory', return_value=({}, '')), \
                patch.object(harness, 'memory_status', return_value={'projects': []}):
            options = harness.component_options('team', self.config)
            snapshot = harness.status(self.config)
        self.assertEqual(options['settings_file'], str(self.settings))
        self.assertEqual(options['team']['roles']['code']['model'], 'opus')
        for key in ('roles', 'options', 'warnings', 'targets', 'connectedCount', 'selectedCount'):
            self.assertIn(key, snapshot['team'])

    def test_native_lifecycle_preserves_foreign_and_detects_modified_skill(self):
        paths = native_team.layout('claude'); paths['home'].mkdir()
        startup = paths['home'] / 'CLAUDE.md'; startup.write_text('Instrucción ajena.\n')
        with patch('native_skills.loaded', return_value=True):
            receipt = native_team.apply('claude', '/claude', 'enable', settings_file=self.settings, team=self.state())
        self.assertTrue(native_team.inspect('claude', receipt, settings_file=self.settings, team=self.state())['verified'])
        self.assertIn('Instrucción ajena.', startup.read_text())
        (paths['source'] / 'SKILL.md').write_text('manual')
        with self.assertRaises(native_plugins.PluginError):
            native_team.apply('claude', '/claude', 'enable', receipt, settings_file=self.settings, team=self.state())
        native_team.apply('claude', '/claude', 'disable', receipt, settings_file=self.settings, team=self.state())
        self.assertFalse(paths['target'].is_symlink())
        self.assertEqual(startup.read_text(), 'Instrucción ajena.\n')
        self.assertTrue(paths['source'].exists())

    def test_second_identical_apply_changes_no_file(self):
        state = self.state()
        with patch('native_skills.loaded', return_value=True):
            first = native_team.apply('claude', '/claude', 'enable', settings_file=self.settings, team=state)
            paths = native_team.layout('claude')
            self.assertTrue((paths['source'] / '.ai-team-managed').is_file())
            before = snapshot(self.root)
            native_team.apply('claude', '/claude', 'enable', first, settings_file=self.settings, team=state)
        self.assertEqual(snapshot(self.root), before)

    def test_modified_skill_is_still_detected_when_content_would_match(self):
        state = self.state()
        with patch('native_skills.loaded', return_value=True):
            native_team.apply('claude', '/claude', 'enable', settings_file=self.settings, team=state)
            marker = native_team.layout('claude')['source'] / '.ai-team-managed'
            marker.write_text('{"sha256":"altered"}')
            with self.assertRaises(native_plugins.PluginError):
                native_team.apply('claude', '/claude', 'enable', settings_file=self.settings, team=state)
        self.assertEqual(marker.read_text(), '{"sha256":"altered"}')

    def test_foreign_skill_is_never_overwritten(self):
        paths = native_team.layout('claude'); paths['target'].mkdir(parents=True)
        foreign = paths['target'] / 'SKILL.md'; foreign.write_text('foreign')
        with self.assertRaises(native_plugins.PluginError):
            native_team.apply('claude', '/claude', 'enable', settings_file=self.settings, team=self.state())
        self.assertEqual(foreign.read_text(), 'foreign')

    def test_managed_enable_verify_disable_uses_nested_preference_and_receipts(self):
        config = {'schemaVersion': 1, 'agents': {'claude': True}}
        tool = {'installation': 'installed', 'binary': '/claude', 'version': '1'}
        with patch.object(harness, 'mise_inventory', return_value=({}, '')), \
                patch.object(harness, 'agent_tool', return_value=tool), \
                patch.object(harness, 'memory_status', return_value={'projects': []}), \
                patch('native_skills.loaded', return_value=True):
            harness.manage_component(config, 'team', 'enable')
            self.assertTrue(config['team']['enabled'])
            self.assertNotIn('teamEnabled', config)
            self.assertEqual(harness.status(config)['team']['connectedCount'], 1)
            self.assertEqual(harness.load_config()['team']['claude']['id'], native_team.ID)
            harness.manage_component(config, 'team', 'verify')
            harness.manage_component(config, 'team', 'disable')
        self.assertFalse(config['team']['enabled'])
        self.assertTrue((native_team.layout('claude')['source'] / 'SKILL.md').exists())

    def test_team_set_rejection_returns_spanish_error_without_saving(self):
        self.config['agents'] = {'claude': True}; harness.save_config(self.config)
        before = self.settings.read_bytes()
        code, reply = self.run_main(['team-set', 'review', 'claude', 'opus'])
        self.assertEqual(code, 1)
        self.assertIn('Revisar', reply['error'])
        self.assertEqual(self.settings.read_bytes(), before)

    def test_paid_pi_rejected_even_if_native_catalog_lists_it(self):
        self.config.update(agents={'pi': True}, agentCatalogs={'pi': {'models': [{'id': 'xai/grok'}]}})
        harness.save_config(self.config)
        code, reply = self.run_main(['team-set', 'simple', 'pi', 'xai/grok'])
        self.assertEqual(code, 1)
        self.assertIn('pago', reply['error'])

    def test_malformed_team_settings_rejected_without_rewriting(self):
        for settings in (None, {'enabled': 1}, {'roles': []}, {'roles': {'bad': {}}},
                         {'roles': {'plan': dict(agent='claude', model='opus', effort='', manual=False)}}):
            config = {**self.config, 'team': settings}; harness.save_config(config)
            before = self.settings.read_bytes()
            with self.subTest(settings=settings), self.assertRaises(harness.HarnessError):
                harness.load_config()
            self.assertEqual(self.settings.read_bytes(), before)

    def test_skill_changes_invalidate_receipt_and_sync_refreshes_it(self):
        state = self.state({'claude'})
        with patch('native_skills.loaded', return_value=True):
            receipt = native_team.apply('claude', '/claude', 'enable', settings_file=self.settings, team=state)
            state['roles']['simple']['model'] = 'sonnet'
            self.assertFalse(native_team.inspect('claude', receipt, settings_file=self.settings, team=state)['verified'])
            receipt = native_team.apply('claude', '/claude', 'enable', receipt, settings_file=self.settings, team=state)
            self.assertTrue(native_team.inspect('claude', receipt, settings_file=self.settings, team=state)['verified'])

    def test_failed_native_loader_keeps_connection_pending(self):
        with patch('native_skills.loaded', return_value=False), self.assertRaisesRegex(native_plugins.PluginError, 'pendiente'):
            native_team.apply('claude', '/claude', 'enable', settings_file=self.settings, team=self.state())
        state = native_team.inspect('claude', settings_file=self.settings, team=self.state())
        self.assertTrue(state['enabled'])
        self.assertFalse(state['verified'])
