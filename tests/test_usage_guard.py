import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import harness
import native_plugins
import native_team
import team


class GuardScriptTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.settings = self.root / 'settings.json'
        self.bin = self.root / 'bin'; self.bin.mkdir()
        self.calls = self.root / 'calls'
        self.usage(session=0.5)

    def usage(self, session=0.5, weekly=0.2):
        payload = json.dumps({'limits': [{'label': 'Session (5-hour)', 'percent': session}, {'label': 'Weekly (7-day)', 'percent': weekly}]})
        tool = self.bin / 'omarchy-agent-usage-claude'
        tool.write_text(f"#!/bin/sh\necho call >> '{self.calls}'\necho '{payload}'\n")
        tool.chmod(0o700)

    def run_guard(self, event='PostToolUse', stdin=None):
        env = {'PATH': f"{self.bin}:{os.environ['PATH']}", 'XDG_STATE_HOME': str(self.root / 'state'), 'HOME': str(self.root)}
        return subprocess.run([sys.executable, '-B', str(ROOT / 'backend/usage_guard.py'), '--settings', str(self.settings)],
                              input=json.dumps({'hook_event_name': event}) if stdin is None else stdin, capture_output=True, text=True, env=env, timeout=20)

    def context(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['hookSpecificOutput']['additionalContext'] if result.stdout.strip() else ''

    def test_silent_below_the_limit(self):
        self.assertEqual(self.context(self.run_guard()), '')

    def test_default_limit_is_ninety_percent(self):
        self.usage(session=0.89); self.assertEqual(self.context(self.run_guard()), '')
        (self.root / 'state/omarchy-ai-team/usage-guard.json').unlink()
        self.usage(session=0.9); self.assertIn('90', self.context(self.run_guard()))

    def test_message_orders_stopping_and_a_detailed_handoff(self):
        self.usage(session=0.93)
        text = self.context(self.run_guard())
        for wanted in ('Session (5-hour)', '93', 'Deja de avanzar', 'memory_save', 'handoff', 'siguiente paso', 'archivos', 'rama', 'avisa al usuario'):
            self.assertIn(wanted, text)

    def test_weekly_window_also_counts(self):
        self.usage(session=0.1, weekly=0.95)
        self.assertIn('Weekly (7-day)', self.context(self.run_guard()))

    def test_limit_can_be_changed(self):
        self.settings.write_text(json.dumps({'usageGuard': {'enabled': True, 'percent': 40}}))
        self.assertIn('40', self.context(self.run_guard()))

    def test_can_be_turned_off(self):
        self.usage(session=0.99)
        self.settings.write_text(json.dumps({'usageGuard': {'enabled': False, 'percent': 90}}))
        self.assertEqual(self.context(self.run_guard()), '')
        self.assertFalse(self.calls.exists())

    def test_reports_the_event_it_was_called_for(self):
        self.usage(session=0.99)
        result = self.run_guard('UserPromptSubmit')
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['hookEventName'], 'UserPromptSubmit')

    def test_usage_is_read_once_per_minute_at_most(self):
        self.run_guard(); self.run_guard(); self.run_guard()
        self.assertEqual(self.calls.read_text().count('call'), 1)

    def test_stale_reading_is_refreshed(self):
        self.run_guard()
        cache = self.root / 'state/omarchy-ai-team/usage-guard.json'
        os.utime(cache, (1, 1))
        self.run_guard()
        self.assertEqual(self.calls.read_text().count('call'), 2)

    def test_cache_is_private(self):
        self.run_guard()
        cache = self.root / 'state/omarchy-ai-team/usage-guard.json'
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(cache.parent.stat().st_mode), 0o700)

    def test_never_breaks_a_session(self):
        cases = {'tool falla': "#!/bin/sh\nexit 3\n", 'json roto': "#!/bin/sh\necho nope\n", 'sin limites': "#!/bin/sh\necho '{}'\n",
                 'porcentaje raro': "#!/bin/sh\necho '{\"limits\":[{\"label\":\"x\",\"percent\":\"mucho\"}]}'\n"}
        for name, body in cases.items():
            with self.subTest(name):
                (self.bin / 'omarchy-agent-usage-claude').write_text(body)
                (self.root / 'state/omarchy-ai-team/usage-guard.json').unlink(missing_ok=True)
                self.assertEqual(self.context(self.run_guard()), '')
        self.assertEqual(self.run_guard(stdin='no es json').returncode, 0)
        self.settings.write_text('{roto')
        self.assertEqual(self.run_guard().returncode, 0)


class GuardSettingTests(unittest.TestCase):
    def test_default_and_changed_values(self):
        self.assertEqual(team.usage_guard({}), {'enabled': True, 'percent': 90})
        self.assertEqual(team.usage_guard({'usageGuard': {'enabled': False, 'percent': 75}}), {'enabled': False, 'percent': 75})

    def test_invalid_values_are_rejected(self):
        for value in (0, 49, 100, 90.5, '90', True):
            with self.subTest(value=value), self.assertRaises(team.TeamError):
                team.set_usage_guard({}, True, value)

    def test_status_exposes_the_setting(self):
        state = harness.team_state({'usageGuard': {'enabled': True, 'percent': 80}, 'agents': {}})
        self.assertEqual(state['usageGuard'], {'enabled': True, 'percent': 80})


class GuardHookTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.file = self.root / 'settings.json'; self.config = self.root / 'plugin.json'

    def read(self):
        return json.loads(self.file.read_text())

    def test_enabling_adds_both_events_and_keeps_foreign_settings(self):
        self.file.write_text(json.dumps({'model': 'x', 'hooks': {'PostToolUse': [{'hooks': [{'type': 'command', 'command': 'ajeno'}]}]}}))
        native_team.set_guard_hook(self.file, self.config, True)
        data = self.read()
        self.assertEqual(data['model'], 'x')
        self.assertEqual(len(data['hooks']['PostToolUse']), 2)
        self.assertEqual(data['hooks']['PostToolUse'][0]['hooks'][0]['command'], 'ajeno')
        self.assertEqual(len(data['hooks']['UserPromptSubmit']), 1)
        self.assertIn('usage_guard.py', data['hooks']['UserPromptSubmit'][0]['hooks'][0]['command'])

    def test_enabling_twice_does_not_duplicate(self):
        native_team.set_guard_hook(self.file, self.config, True)
        before = self.file.read_text()
        native_team.set_guard_hook(self.file, self.config, True)
        self.assertEqual(self.file.read_text(), before)

    def test_disabling_removes_only_ours(self):
        self.file.write_text(json.dumps({'hooks': {'PostToolUse': [{'hooks': [{'type': 'command', 'command': 'ajeno'}]}]}}))
        native_team.set_guard_hook(self.file, self.config, True)
        native_team.set_guard_hook(self.file, self.config, False)
        self.assertEqual(self.read(), {'hooks': {'PostToolUse': [{'hooks': [{'type': 'command', 'command': 'ajeno'}]}]}})

    def test_disabling_without_a_file_creates_nothing(self):
        native_team.set_guard_hook(self.file, self.config, False)
        self.assertFalse(self.file.exists())

    def test_a_broken_settings_file_is_preserved(self):
        self.file.write_text('{roto')
        with self.assertRaises(native_plugins.PluginError):
            native_team.set_guard_hook(self.file, self.config, True)
        self.assertEqual(self.file.read_text(), '{roto')

    def test_new_file_is_private(self):
        native_team.set_guard_hook(self.file, self.config, True)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)

    def command(self):
        return self.read()['hooks']['UserPromptSubmit'][0]['hooks'][0]['command']

    def test_paths_with_spaces_are_quoted_and_run(self):
        config = self.root / 'con espacio/plugin.json'
        native_team.set_guard_hook(self.file, config, True)
        result = subprocess.run(['sh', '-c', self.command()], input='no es json', capture_output=True, text=True,
                                env={'PATH': os.environ['PATH'], 'HOME': str(self.root)})
        self.assertEqual((result.returncode, result.stdout), (0, ''))

    def test_a_missing_script_never_blocks_the_user(self):
        native_team.set_guard_hook(self.file, self.config, True)
        script = str(Path(native_team.__file__).with_name('usage_guard.py'))
        command = self.command().replace(script, str(self.root / 'desinstalado/usage_guard.py'))
        result = subprocess.run(['sh', '-c', command], input='{}', capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_permissions_are_kept(self):
        self.file.write_text('{}'); self.file.chmod(0o644)
        native_team.set_guard_hook(self.file, self.config, True)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o644)

    def test_unexpected_hook_types_are_refused_and_preserved(self):
        for value in ('{"hooks":{"PostToolUse":null}}', '{"hooks":[]}', '[]'):
            with self.subTest(value=value):
                self.file.write_text(value)
                with self.assertRaises(native_plugins.PluginError):
                    native_team.set_guard_hook(self.file, self.config, True)
                self.assertEqual(self.file.read_text(), value)

    def test_foreign_entries_of_any_shape_are_kept(self):
        foreign = [{'hooks': None}, {'hooks': 5}, 'texto', {'hooks': ['x']}]
        self.file.write_text(json.dumps({'hooks': {'PostToolUse': foreign}}))
        native_team.set_guard_hook(self.file, self.config, True)
        self.assertEqual(self.read()['hooks']['PostToolUse'][:4], foreign)
        native_team.set_guard_hook(self.file, self.config, False)
        self.assertEqual(self.read()['hooks']['PostToolUse'], foreign)

    def test_a_symlinked_settings_file_is_refused_and_kept(self):
        real = self.root / 'real.json'; real.write_text('{}')
        self.file.symlink_to(real)
        with self.assertRaises(native_plugins.PluginError):
            native_team.set_guard_hook(self.file, self.config, True)
        self.assertTrue(self.file.is_symlink())

    def test_a_broken_file_cannot_stop_the_team_from_being_disabled(self):
        self.file.write_text('{roto')
        with patch.object(native_team, 'inspect', return_value={'enabled': False, 'verified': False}), \
             patch.object(native_team.shared_memory, 'local_config', return_value={}), \
             patch.object(native_team.shared_memory, 'startup_text', return_value=''), \
             patch.object(native_team.shared_memory, 'write_claude_startup') as startup, \
             patch.object(native_team, 'layout', return_value={'home': self.root, 'profile': 'claude', 'target': self.root / 'skill', 'source': self.root / 'source'}), \
             patch.object(native_team, 'digest', return_value='x'):
            native_team.apply('claude', '/bin/true', 'disable', settings_file=self.config)
        startup.assert_called_once()
        self.assertEqual(self.file.read_text(), '{roto')

    def test_a_broken_file_stops_enabling_before_anything_changes(self):
        self.file.write_text('{roto')
        (self.root / 'source').mkdir()
        with patch.object(native_team, 'inspect', return_value={'enabled': True, 'verified': True}), \
             patch.object(native_team, 'prepare'), patch.object(native_team.shared_memory, 'local_config', return_value={}), \
             patch.object(native_team.shared_memory, 'startup_text', return_value=''), \
             patch.object(native_team.shared_memory, 'write_claude_startup') as startup, \
             patch.object(native_team, 'layout', return_value={'home': self.root, 'profile': 'claude', 'target': self.root / 'skill', 'source': self.root / 'source'}):
            with self.assertRaises(native_plugins.PluginError):
                native_team.apply('claude', '/bin/true', 'enable', settings_file=self.config)
        startup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
