import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import harness


class AfterUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {
            'HOME': str(self.root), 'PATH': '',
            'XDG_CONFIG_HOME': str(self.root / 'config'),
            'XDG_DATA_HOME': str(self.root / 'data'),
            'XDG_STATE_HOME': str(self.root / 'state'),
            'CODEX_HOME': str(self.root / '.codex'),
            'CLAUDE_CONFIG_DIR': str(self.root / '.claude'),
        }, clear=True)
        environment.start(); self.addCleanup(environment.stop)
        self.version = json.loads((ROOT / 'manifest.json').read_text())['version']
        self.states = {key: {agent: {'installed': True, 'enabled': True}
                             for agent in harness.AGENTS} for key in harness.MANAGED}
        for key, (_, adapter) in harness.MANAGED.items():
            mock = patch.object(adapter, 'inspect', side_effect=self.inspector(key))
            mock.start(); self.addCleanup(mock.stop)
        for mock in (
            patch.object(harness, 'mise_inventory', return_value=({}, '')),
            patch.object(harness, 'agent_tool', return_value={
                'installation': 'installed', 'binary': '/synthetic/agent', 'version': '1'}),
            patch.object(harness.native_pi, 'inspect_component', side_effect=
                         lambda key, receipt: self.inspector(key)('pi', receipt)),
        ):
            mock.start(); self.addCleanup(mock.stop)
        apply = patch.object(harness, 'apply_component', side_effect=self.apply)
        self.apply_component = apply.start(); self.addCleanup(apply.stop)
        self.fail_component = None

    def inspector(self, key):
        def inspect(agent, receipt=None, **options):
            profile = ('codex-2' if agent == 'codex' and
                       Path(os.environ['CODEX_HOME']).name == '.codex-2' else agent)
            return {**self.states[key][profile], 'version': '1', 'detail': 'Estado simulado',
                    'verified': bool(receipt and receipt.get('digest'))}
        return inspect

    def apply(self, config, key, agent, action, tool):
        if key == self.fail_component:
            raise harness.native_plugins.PluginError('Fallo simulado de conexión')
        config.setdefault(key, {})[agent] = {
            'digest': 'renewed', 'agentBinary': tool['binary'], 'agentVersion': tool['version']}
        harness.save_config(config)

    def configured(self, version):
        config = {'schemaVersion': 1, 'agents': {'claude': True}, 'foreign': 'preserve'}
        if version is not None:
            config['pluginVersion'] = version
        for key in harness.MANAGED:
            if key != 'team': config[key + 'Enabled'] = True
            config[key] = {'claude': {'digest': 'stale'}}
        config['team'].update(enabled=True, roles={})
        harness.save_config(config)
        return config

    def command(self, command='after-update'):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = harness.main([command])
        return code, json.loads(output.getvalue())

    def test_status_reports_manifest_version_and_reconnect_without_writing(self):
        for version, needed in ((None, True), ('previous', True), (self.version, False)):
            with self.subTest(version=version):
                self.configured(version)
                before = harness.config_path().read_bytes()
                code, reply = self.command('status')
                self.assertEqual(code, 0)
                self.assertEqual(reply['data']['pluginVersion'], self.version)
                self.assertIs(reply['data']['needsReconnect'], needed)
                self.assertEqual(harness.config_path().read_bytes(), before)
        self.apply_component.assert_not_called()

    def test_unchanged_version_does_no_work(self):
        self.configured(self.version)
        before = harness.config_path().read_bytes()
        code, reply = self.command()
        self.assertEqual(code, 0)
        self.assertFalse(reply['data']['needsReconnect'])
        self.apply_component.assert_not_called()
        self.assertEqual(harness.config_path().read_bytes(), before)

    def test_changed_version_refreshes_all_managed_components_once(self):
        self.configured('previous')
        code, reply = self.command()
        self.assertEqual(code, 0)
        self.assertEqual([(call.args[1], call.args[2], call.args[3])
                          for call in self.apply_component.call_args_list], [
            ('superpowers', 'claude', 'verify'), ('memory', 'claude', 'verify'),
            ('local-tools', 'claude', 'verify'),
            ('team', 'claude', 'verify')])
        stored = harness.load_config()
        self.assertEqual(stored['pluginVersion'], self.version)
        self.assertEqual(stored['foreign'], 'preserve')
        self.assertFalse(reply['data']['needsReconnect'])
        self.assertIn(self.version, reply['message'])
        self.apply_component.reset_mock()
        self.command()
        self.apply_component.assert_not_called()

    def test_disabled_unselected_and_missing_targets_are_untouched(self):
        config = self.configured('previous')
        config['superpowersEnabled'] = False
        config['team']['enabled'] = False
        harness.save_config(config)
        self.states['memory']['claude']['enabled'] = False
        self.states['local-tools']['claude']['installed'] = False
        code, _ = self.command()
        self.assertEqual(code, 0)
        self.apply_component.assert_not_called()
        self.assertEqual(harness.load_config(), {**config, 'pluginVersion': self.version})

    def test_failure_stores_version_and_error_without_automatic_retry(self):
        self.configured('previous')
        self.fail_component = 'memory'
        _, reply = self.command()
        stored = harness.load_config()
        self.assertEqual(stored['pluginVersion'], self.version)
        self.assertIn('Fallo simulado', stored['memory']['claude']['lastError'])
        self.assertNotIn('digest', stored['memory']['claude'])
        self.assertIn('memory · Claude Code', reply['message'])
        self.assertIn('Fallo simulado', reply['message'])
        self.assertFalse(reply['data']['needsReconnect'])
        self.assertEqual(stored['local-tools']['claude']['digest'], 'renewed')
        self.apply_component.reset_mock()
        self.command()
        self.apply_component.assert_not_called()

    def test_fresh_config_records_version_without_connecting(self):
        code, reply = self.command()
        self.assertEqual(code, 0)
        self.apply_component.assert_not_called()
        self.assertEqual(harness.load_config()['pluginVersion'], self.version)
        self.assertFalse(reply['data']['needsReconnect'])

    def test_one_bar_per_monitor_restarts_the_shell_only_once(self):
        config_file = harness.config_path()
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({'schemaVersion': 1, 'agents': {}, 'pluginVersion': 'previous'}))
        script = str(ROOT / 'backend/harness.py')
        # Separate files: a full pipe nobody is reading yet would block the process holding the lock.
        outputs = [self.root / f'reply-{index}.json' for index in range(6)]
        processes = []
        for output in outputs:
            with output.open('w') as stream:
                processes.append(subprocess.Popen([sys.executable, '-B', script, 'after-update'], stdout=stream,
                                                  stderr=subprocess.DEVNULL, env={**os.environ, 'PATH': os.defpath}))
        for process in processes:
            process.wait(timeout=60)
        replies = [json.loads(output.read_text()) for output in outputs]
        self.assertEqual(sum(1 for reply in replies if reply.get('operation', {}).get('shellRestart')), 1)
        self.assertEqual(json.loads(config_file.read_text())['pluginVersion'], self.version)

    def test_update_from_a_previous_version_asks_for_a_shell_restart(self):
        self.configured('previous')
        code, reply = self.command()
        self.assertEqual(code, 0)
        self.assertIs(reply['operation']['shellRestart'], True)

    def test_second_run_after_an_update_does_not_restart_again(self):
        self.configured('previous')
        self.command()
        _, reply = self.command()
        self.assertNotIn('shellRestart', reply.get('operation', {}))

    def test_first_install_does_not_restart_the_shell(self):
        self.configured(None)
        _, reply = self.command()
        self.assertNotIn('shellRestart', reply.get('operation', {}))

    def test_reconnect_without_a_new_version_does_not_restart_the_shell(self):
        self.configured(self.version)
        with patch.object(harness, 'team_agent_changed', return_value=True):
            _, reply = self.command()
        self.assertNotIn('shellRestart', reply.get('operation', {}))

    def test_after_update_renews_nous_extension_only_when_enabled(self):
        source = harness.nous_quota.paths(harness.config_path())[0]
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                config = self.configured('previous')
                config['piProviders'] = {'nous': enabled}
                harness.save_config(config)
                code, _ = self.command()
                self.assertEqual(code, 0)
                self.assertEqual(source.is_file(), enabled)
        self.assertIn('registerProvider("nous"', source.read_text())

    def test_after_update_replaces_the_previous_managed_nous_extension(self):
        config = self.configured('previous')
        config['piProviders'] = {'nous': True}
        harness.save_config(config)
        with patch.object(harness.nous_quota, 'extension', return_value='// previous version\n'):
            harness.nous_quota.configure(harness.config_path(), True)
        source = harness.nous_quota.paths(harness.config_path())[0]
        self.assertEqual(source.read_text(), '// previous version\n')
        code, _ = self.command()
        self.assertEqual(code, 0)
        self.assertIn('registerProvider("nous"', source.read_text())

    def test_after_update_preserves_an_edited_nous_extension_and_reports_it(self):
        config = self.configured('previous')
        config['piProviders'] = {'nous': True}
        harness.save_config(config)
        harness.nous_quota.configure(harness.config_path(), True)
        source = harness.nous_quota.paths(harness.config_path())[0]
        source.write_text('// user edit\n')
        code, reply = self.command()
        self.assertEqual(code, 0)
        self.assertEqual(source.read_text(), '// user edit\n')
        with harness.i18n.using('en'):
            self.assertIn(harness.tr('La extensión de cuota está modificada o es ajena; se conserva.'), reply['message'])

    def test_after_update_refreshes_team_catalog_when_enabled(self):
        for enabled, selected, calls in ((True, 'codex-2', 1), (False, 'codex-2', 0), (True, 'claude', 0)):
            with self.subTest(enabled=enabled, selected=selected):
                config = self.configured('previous')
                config['agents'] = {selected: True}
                config['team']['enabled'] = enabled
                harness.save_config(config)
                rows = [{'id': 'gpt-6.2-luna'}]
                with patch.object(harness.native_plugins, 'codex_request', return_value={'data': rows}) as request:
                    code, _ = self.command()
                self.assertEqual(code, 0)
                self.assertEqual(request.call_count, calls)
                if calls:
                    self.assertEqual(request.call_args.args, ('/synthetic/agent', 'model/list', {}))
                    self.assertEqual(harness.load_config()['teamModels']['codex'], rows)

    def test_agent_version_guard_refreshes_once_before_sync(self):
        config = self.configured(self.version)
        config['agents'] = {'codex-2': True}
        config['team']['codex-2'] = dict(digest='old', agentBinary='/synthetic/agent', agentVersion='0')
        harness.save_config(config)
        with patch.object(harness.native_plugins, 'codex_request', return_value={'data': [{'id': 'gpt-7-luna'}]}) as request:
            code, reply = self.command('status')
            self.assertTrue(reply['data']['needsReconnect'])
            request.assert_not_called()
            self.command()
            request.assert_called_once()
            self.assertEqual(harness.load_config()['teamModels']['codex'][0]['id'], 'gpt-7-luna')
            self.command()
            request.assert_called_once()

    def test_failed_refresh_preserves_cache_and_persists_warning(self):
        config = self.configured('previous')
        config['agents'] = {'codex': True}
        config['teamModels'] = {'codex': [{'id': 'gpt-6-luna'}]}
        config['team']['roles']['simple'] = dict(agent='codex', model='gpt-6-luna', effort='medium', manual=True)
        harness.save_config(config)
        with patch.object(harness.native_plugins, 'codex_request', side_effect=OSError('sin conexión')):
            self.command()
        stored = harness.load_config()
        self.assertEqual(stored['teamModels'], config['teamModels'])
        self.assertTrue(stored['team']['roles']['simple']['manual'])
        self.assertIn('catálogo', ' '.join(harness.team_state(stored)['warnings']))
