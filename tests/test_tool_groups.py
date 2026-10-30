import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import ANY, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import harness
import local_catalog as catalog
import project_scope


class ToolGroupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        env = patch.dict(os.environ, {"LC_ALL": "es", 'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root / 'config'),
                                     'XDG_DATA_HOME': str(self.root / 'data'), 'CLAUDE_CONFIG_DIR': str(self.root / '.claude')})
        env.start(); self.addCleanup(env.stop)
        self.config = {'schemaVersion': 1, 'agents': {}, 'foreign': 'keep'}
        self.project = self.root / 'project'; self.project.mkdir()
        self.settings = self.root / 'config/omarchy-ai-team/settings.json'

    def run_main(self, arguments):
        output = io.StringIO()
        with patch.object(harness, 'status', return_value={}), contextlib.redirect_stdout(output):
            result = harness.main(arguments)
        return result, json.loads(output.getvalue())

    def test_legacy_flag_enables_both_groups(self):
        self.assertEqual(catalog.group_state({'local-toolsEnabled': True}), {'tokens': True, 'security': True})
        self.assertEqual(catalog.group_state({'local-toolsEnabled': False}), {'tokens': False, 'security': False})
        self.assertEqual(catalog.group_state({}), {'tokens': False, 'security': False})

    def test_explicit_flags_replace_legacy_fallback_without_mutating_config(self):
        for flags, expected in (({'tokensEnabled': True}, {'tokens': True, 'security': False}),
                                ({'securityEnabled': True}, {'tokens': False, 'security': True}),
                                ({'tokensEnabled': False, 'securityEnabled': False}, {'tokens': False, 'security': False})):
            with self.subTest(flags=flags):
                config = {'local-toolsEnabled': True, **flags}
                before = copy.deepcopy(config)
                self.assertEqual(catalog.group_state(config), expected)
                self.assertEqual(config, before)

    def test_enabled_groups_are_sorted(self):
        for config, expected in (({}, ()), ({'tokensEnabled': True}, ('tokens',)),
                                 ({'securityEnabled': True}, ('security',)),
                                 ({'local-toolsEnabled': True}, ('security', 'tokens'))):
            with self.subTest(config=config):
                self.assertEqual(catalog.enabled_groups(config), expected)

    def test_first_change_keeps_other_legacy_group(self):
        for group, expected in (('tokens', {'tokens': False, 'security': True}),
                                ('security', {'tokens': True, 'security': False})):
            with self.subTest(group=group):
                config = {'schemaVersion': 1, 'local-toolsEnabled': True}
                catalog.set_group(config, group, False)
                self.assertEqual(catalog.group_state(config), expected)
                self.assertIs(config['tokensEnabled'], expected['tokens'])
                self.assertIs(config['securityEnabled'], expected['security'])

    def test_enable_sets_on_and_returns_group_tools(self):
        for group, tools in (('tokens', ('rg', 'jq', 'yq', 'ast-grep')),
                             ('security', ('gitleaks', 'osv-scanner', 'shellcheck', 'ruff', 'hadolint'))):
            with self.subTest(group=group):
                config = copy.deepcopy(self.config)
                self.assertEqual(catalog.set_group(config, group, True), tools)
                self.assertEqual({key: catalog.preference(config, key) for key in tools}, dict.fromkeys(tools, 'on'))
                self.assertIs(config[group + 'Enabled'], True)
                other = 'security' if group == 'tokens' else 'tokens'
                self.assertIs(config[other + 'Enabled'], False)
                self.assertEqual(config['foreign'], 'keep')
                self.assertEqual(config['agents'], {})
                self.assertEqual({key: catalog.preference(config, key) for key in ('git', 'python', 'node', 'qmllint')},
                                 {'git': 'auto', 'python': 'auto', 'node': 'auto', 'qmllint': 'auto'})

    def test_security_group_keeps_optional_preferences_when_enabled(self):
        self.assertEqual(catalog.GROUPS['security'],
                         ('gitleaks', 'osv-scanner', 'shellcheck', 'ruff', 'hadolint', 'semgrep'))
        self.assertEqual(catalog.OPTIONAL, frozenset({'semgrep'}))
        for mode in ('auto', 'on', 'off'):
            with self.subTest(mode=mode):
                config = copy.deepcopy(self.config)
                catalog.set_preference(config, 'semgrep', 'default', mode)
                self.assertEqual(catalog.set_group(config, 'security', True),
                                 ('gitleaks', 'osv-scanner', 'shellcheck', 'ruff', 'hadolint'))
                self.assertEqual(catalog.preference(config, 'semgrep'), mode)

    def test_security_disable_resets_all_tools_including_optional(self):
        scope = project_scope.project_key(self.project)
        for key in ('gitleaks', 'osv-scanner', 'shellcheck', 'ruff', 'hadolint', 'semgrep'):
            self.assertIn(key,catalog.CATALOG)
            catalog.set_preference(self.config, key, 'default', 'on')
            catalog.set_preference(self.config, key, scope, 'off')
        projects = copy.deepcopy(self.config['localToolsSettings']['projects'])
        self.assertEqual(catalog.set_group(self.config, 'security', False), ())
        self.assertEqual(self.config['localToolsSettings']['defaults'],
                         dict.fromkeys(('gitleaks', 'osv-scanner', 'shellcheck', 'ruff', 'hadolint', 'semgrep'), 'auto'))
        self.assertEqual(self.config['localToolsSettings']['projects'], projects)

    def test_disable_returns_auto_and_keeps_project_preferences(self):
        scope = project_scope.project_key(self.project)
        config = self.config
        catalog.set_group(config, 'security', True)
        catalog.set_group(config, 'tokens', True)
        catalog.set_preference(config, 'rg', scope, 'off')
        catalog.set_preference(config, 'jq', scope, 'on')
        catalog.set_preference(config, 'git', 'default', 'off')
        projects = copy.deepcopy(config['localToolsSettings']['projects'])
        self.assertEqual(catalog.set_group(config, 'tokens', False), ())
        self.assertEqual({key: catalog.preference(config, key) for key in ('rg', 'jq', 'yq', 'ast-grep')},
                         {'rg': 'auto', 'jq': 'auto', 'yq': 'auto', 'ast-grep': 'auto'})
        self.assertEqual(config['localToolsSettings']['projects'], projects)
        self.assertEqual(catalog.preference(config, 'rg', scope), 'off')
        self.assertEqual(catalog.preference(config, 'jq', scope), 'on')
        self.assertEqual(catalog.preference(config, 'shellcheck'), 'on')
        self.assertEqual(catalog.preference(config, 'git'), 'off')
        self.assertTrue(config['securityEnabled'])

    def test_bridge_wanted_only_without_groups_or_manual_on(self):
        config = {'tokensEnabled': False, 'securityEnabled': False}
        self.assertFalse(catalog.bridge_wanted(config))
        catalog.set_preference(config, 'git', 'default', 'on')
        self.assertTrue(catalog.bridge_wanted(config))
        catalog.set_preference(config, 'git', 'default', 'auto')
        catalog.set_preference(config, 'rg', 'default', 'off')
        self.assertFalse(catalog.bridge_wanted(config))

    def test_bridge_wanted_with_either_group_or_legacy_flag(self):
        for config in ({'tokensEnabled': True}, {'securityEnabled': True}, {'local-toolsEnabled': True}):
            with self.subTest(config=config):
                self.assertTrue(catalog.bridge_wanted(config))

    def test_bridge_wanted_with_project_on(self):
        config = {'tokensEnabled': False, 'securityEnabled': False}
        scope = project_scope.project_key(self.project)
        catalog.set_preference(config, 'git', scope, 'on')
        self.assertTrue(catalog.bridge_wanted(config))
        catalog.set_preference(config, 'git', scope, 'off')
        self.assertFalse(catalog.bridge_wanted(config))

    def test_invalid_group_flag_is_rejected(self):
        for flag in ('tokensEnabled', 'securityEnabled'):
            for value in ('yes', None, 0, 1, [], {}):
                with self.subTest(flag=flag, value=value), self.assertRaises(catalog.CatalogError):
                    catalog.group_state({flag: value})

    def test_invalid_group_change_is_rejected_without_mutation(self):
        for group, enabled in (('unknown', True), ('tokens', 'yes'), ('security', 1)):
            with self.subTest(group=group, enabled=enabled):
                config = copy.deepcopy(self.config)
                with self.assertRaises(catalog.CatalogError):
                    catalog.set_group(config, group, enabled)
                self.assertEqual(config, self.config)

    def test_tool_group_disable_keeps_bridge_while_other_group_on(self):
        self.config.update(tokensEnabled=True, securityEnabled=True)
        harness.save_config(self.config)
        with patch.object(harness, 'activate_tool') as activate, patch.object(harness, 'manage_component') as manage:
            result, reply = self.run_main(['tool-group', 'security', 'disable'])
        self.assertEqual(result, 0)
        self.assertTrue(reply['ok'])
        config = harness.load_config()
        manage.assert_called_once_with(config, 'local-tools', 'enable')
        activate.assert_not_called()
        self.assertEqual(catalog.group_state(config), {'tokens': True, 'security': False})

    def test_tool_group_both_off_disconnects_bridge(self):
        self.config.update(tokensEnabled=True, securityEnabled=False)
        harness.save_config(self.config)
        with patch.object(harness, 'activate_tool') as activate, patch.object(harness, 'manage_component') as manage:
            result, reply = self.run_main(['tool-group', 'tokens', 'disable'])
        self.assertEqual(result, 0)
        self.assertTrue(reply['ok'])
        manage.assert_called_once_with(harness.load_config(), 'local-tools', 'disable')
        activate.assert_not_called()

    def test_tool_group_disable_keeps_bridge_for_manual_on(self):
        for scope in ('default', project_scope.project_key(self.project)):
            with self.subTest(scope=scope):
                config = {'schemaVersion': 1, 'tokensEnabled': True, 'securityEnabled': False}
                catalog.set_preference(config, 'git', scope, 'on')
                harness.save_config(config)
                with patch.object(harness, 'activate_tool') as activate, patch.object(harness, 'manage_component') as manage:
                    result, _ = self.run_main(['tool-group', 'tokens', 'disable'])
                self.assertEqual(result, 0)
                manage.assert_called_once_with(harness.load_config(), 'local-tools', 'enable')
                activate.assert_not_called()

    def test_tool_group_first_change_keeps_other_legacy_group(self):
        self.config['local-toolsEnabled'] = True
        harness.save_config(self.config)
        with patch.object(harness, 'activate_tool') as activate, patch.object(harness, 'manage_component') as manage:
            result, _ = self.run_main(['tool-group', 'tokens', 'disable'])
        self.assertEqual(result, 0)
        config = harness.load_config()
        self.assertEqual(catalog.group_state(config), {'tokens': False, 'security': True})
        manage.assert_called_once_with(config, 'local-tools', 'enable')
        activate.assert_not_called()

    def test_tool_group_enable_saves_before_activation_and_connects(self):
        for group, tools in (('tokens', ('rg', 'jq', 'yq', 'ast-grep')),
                             ('security', ('gitleaks', 'osv-scanner', 'shellcheck', 'ruff', 'hadolint'))):
            with self.subTest(group=group):
                harness.save_config(self.config)
                def check_saved(config, key):
                    self.assertEqual(harness.load_config(), config)
                    self.assertTrue(config[group + 'Enabled'])
                    self.assertEqual(catalog.preference(config, key), 'on')
                with patch.object(harness, 'activate_tool', side_effect=check_saved) as activate, patch.object(harness, 'manage_component') as manage:
                    result, reply = self.run_main(['tool-group', group, 'enable'])
                config = harness.load_config()
                self.assertEqual(result, 0)
                self.assertTrue(reply['ok'])
                self.assertEqual(activate.call_args_list, [call(config, key) for key in tools])
                manage.assert_called_once_with(config, 'local-tools', 'enable')
                self.assertEqual(reply['message'], 'Aplicado. Abre sesiones nuevas para usarlo.')
                self.assertEqual(config['agents'], {})

    def test_partial_install_names_tool_and_still_connects(self):
        harness.save_config(self.config)
        def activate(config, key):
            if key == 'ruff':
                raise catalog.CatalogError('mise no disponible')
        with patch.object(harness, 'activate_tool', side_effect=activate) as activate, patch.object(harness, 'manage_component') as manage:
            result, reply = self.run_main(['tool-group', 'security', 'enable'])
        self.assertEqual(result, 1)
        self.assertFalse(reply['ok'])
        self.assertIn('Ruff: mise no disponible', reply['error'])
        config = harness.load_config()
        self.assertEqual(activate.call_args_list, [call(config, key) for key in ('gitleaks', 'osv-scanner', 'shellcheck', 'ruff', 'hadolint')])
        manage.assert_called_once_with(config, 'local-tools', 'enable')
        self.assertTrue(config['securityEnabled'])
        self.assertEqual(catalog.preference(config, 'ruff'), 'on')

    def test_tool_group_accumulates_activation_and_connection_errors(self):
        harness.save_config(self.config)
        with patch.object(harness, 'activate_tool', side_effect=[OSError('mise ausente'), None, subprocess.TimeoutExpired('shellcheck', 10), None, None]) as activate, patch.object(harness, 'manage_component', side_effect=harness.HarnessError('Codex sin conectar')) as manage:
            result, reply = self.run_main(['tool-group', 'security', 'enable'])
        self.assertEqual(result, 1)
        self.assertFalse(reply['ok'])
        self.assertIn('Gitleaks: mise ausente', reply['error'])
        self.assertIn('ShellCheck:', reply['error'])
        self.assertTrue(reply['error'].endswith('; Codex sin conectar'))
        self.assertEqual(activate.call_count, 5)
        manage.assert_called_once_with(harness.load_config(), 'local-tools', 'enable')

    def test_load_config_rejects_non_boolean_group_flag(self):
        for flag in ('tokensEnabled', 'securityEnabled'):
            with self.subTest(flag=flag):
                harness.save_config({**self.config, flag: 'yes'})
                before = self.settings.read_bytes()
                with self.assertRaises(harness.HarnessError):
                    harness.load_config()
                self.assertEqual(self.settings.read_bytes(), before)

    def test_load_config_preserves_legacy_and_explicit_settings(self):
        for flags in ({'local-toolsEnabled': True}, {'tokensEnabled': True, 'securityEnabled': False}):
            with self.subTest(flags=flags):
                config = {**self.config, **flags}
                harness.save_config(config)
                before = self.settings.read_bytes()
                self.assertEqual(harness.load_config(), config)
                self.assertEqual(self.settings.read_bytes(), before)

    def test_activate_tool_installs_supported_tools_and_saves_probe(self):
        for key in ('rg', 'git'):
            with self.subTest(key=key):
                config = copy.deepcopy(self.config)
                receipt = {'binary': '/native/' + key, 'stamp': 'new', 'version': '1', 'checkedAt': 'now'}
                with patch.object(catalog, 'install') as install, patch.object(harness, 'mise_inventory', return_value=({'native': []}, '')), patch.object(catalog.native_tools.shutil, 'which', return_value=None), patch.object(catalog, 'probe', return_value=receipt) as probe:
                    self.assertIsNone(harness.activate_tool(config, key))
                if key == 'git':
                    install.assert_not_called()
                else:
                    install.assert_called_once_with(key, on_install=ANY)
                probe.assert_called_once_with(key, {'native': []})
                self.assertEqual(harness.load_config()['localToolsSettings']['probes'][key], receipt)

    def test_activate_tool_failed_probe_clears_stale_receipt(self):
        self.config['localToolsSettings'] = {'probes': {'rg': {'stamp': 'old'}, 'git': {'stamp': 'keep'}}}
        with patch.object(catalog, 'install'), patch.object(harness, 'mise_inventory', return_value=({}, '')), patch.object(catalog, 'probe', side_effect=catalog.CatalogError('versión no válida')):
            with self.assertRaises(catalog.CatalogError):
                harness.activate_tool(self.config, 'rg')
        self.assertEqual(harness.load_config()['localToolsSettings']['probes'], {'git': {'stamp': 'keep'}})

    def test_catalog_preference_on_reuses_activation(self):
        harness.save_config(self.config)
        with patch.object(harness, 'activate_tool') as activate, patch.object(harness, 'manage_component') as manage:
            result, reply = self.run_main(['catalog-preference', 'rg', 'on', '--scope', 'default'])
        self.assertEqual(result, 0)
        self.assertTrue(reply['ok'])
        config = harness.load_config()
        self.assertEqual(catalog.preference(config, 'rg'), 'on')
        activate.assert_called_once_with(config, 'rg')
        manage.assert_called_once_with(config, 'local-tools', 'enable')

    def test_catalog_preference_off_disconnects_bridge_when_no_longer_wanted(self):
        config = {**self.config, 'tokensEnabled': False, 'securityEnabled': False,
                  'local-toolsEnabled': True}
        catalog.set_preference(config, 'rg', 'default', 'on')
        harness.save_config(config)
        with patch.object(harness, 'activate_tool') as activate, patch.object(harness, 'manage_component') as manage:
            result, reply = self.run_main(['catalog-preference', 'rg', 'off', '--scope', 'default'])
        self.assertEqual(result, 0)
        self.assertTrue(reply['ok'])
        saved = harness.load_config()
        self.assertEqual(catalog.preference(saved, 'rg'), 'off')
        activate.assert_not_called()
        manage.assert_called_once_with(saved, 'local-tools', 'disable')

    def test_catalog_preference_does_not_reconcile_matching_bridge_state(self):
        config = {**self.config, 'local-toolsEnabled': True}
        harness.save_config(config)
        with patch.object(harness, 'activate_tool') as activate, patch.object(harness, 'manage_component') as manage:
            result, reply = self.run_main(['catalog-preference', 'rg', 'on', '--scope', 'default'])
        self.assertEqual(result, 0)
        self.assertTrue(reply['ok'])
        saved = harness.load_config()
        activate.assert_called_once_with(saved, 'rg')
        manage.assert_not_called()

    def test_catalog_preference_auto_and_off_do_not_activate(self):
        for mode in ('auto', 'off'):
            with self.subTest(mode=mode), patch.object(harness, 'activate_tool') as activate:
                result, _ = self.run_main(['catalog-preference', 'rg', mode])
                self.assertEqual(result, 0)
                self.assertEqual(catalog.preference(harness.load_config(), 'rg'), mode)
                activate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
