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
    agent = 'claude'

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
        for wanted in ('Session (5-hour)', '93', 'Para ya', 'memory_save', 'handoff'):
            self.assertIn(wanted, text)

    def test_threshold_forbids_delegation_and_asks_for_a_checked_checkpoint(self):
        self.usage(session=0.95)
        text = self.context(self.run_guard())
        for wanted in ('no delegues', 'context_state.py checkpoint', 'status=saved'):
            self.assertIn(wanted, text)

    def test_unknown_quota_is_explicit_once_per_prompt_and_exits_zero(self):
        cases = {'tool falla': "#!/bin/sh\nexit 3\n", 'json roto': "#!/bin/sh\necho nope\n", 'sin limites': "#!/bin/sh\necho '{}'\n",
                 'limites None': "#!/bin/sh\necho '{\"limits\":null}'\n", 'lista vacia': "#!/bin/sh\necho '{\"limits\":[]}'\n"}
        for name, body in cases.items():
            with self.subTest(name):
                (self.bin / ('omarchy-agent-usage-' + self.agent)).write_text(body)
                for stale in (self.root / 'state/omarchy-ai-team').glob('usage-guard*.json'): stale.unlink()
                text = self.context(self.run_guard('UserPromptSubmit'))
                self.assertIn('cuota desconocida', text); self.assertIn('no delegar trabajo nuevo', text)
                self.assertEqual(self.context(self.run_guard('PostToolUse')), '')

    def test_broken_row_before_the_one_over_the_limit_is_ignored(self):
        tool = self.bin / ('omarchy-agent-usage-' + self.agent)
        tool.write_text("#!/bin/sh\necho '{\"limits\":[{\"label\":\"A\"},{\"label\":\"B\",\"percent\":\"x\"},{\"label\":\"Weekly (7-day)\",\"percent\":0.95}]}'\n")
        self.assertIn('Weekly (7-day)', self.context(self.run_guard()))

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


class CodexGuardScriptTests(GuardScriptTests):
    """The same behaviour, reading the usage of the Codex account that runs the hook."""
    agent = 'codex'

    def usage(self, session=0.5, weekly=0.2):
        payload = json.dumps({'limits': [{'label': 'Session (5-hour)', 'percent': session}, {'label': 'Weekly (7-day)', 'percent': weekly}]})
        tool = self.bin / 'omarchy-agent-usage-codex'
        tool.write_text(f"#!/bin/sh\necho \"call $CODEX_HOME\" >> '{self.calls}'\necho '{payload}'\n")
        tool.chmod(0o700)

    def run_guard(self, event='PostToolUse', stdin=None):
        env = {'PATH': f"{self.bin}:{os.environ['PATH']}", 'XDG_STATE_HOME': str(self.root / 'state'), 'HOME': str(self.root)}
        return subprocess.run([sys.executable, '-B', str(ROOT / 'backend/usage_guard.py'), '--settings', str(self.settings),
                               '--agent', 'codex', '--codex-home', '/perfil/uno'],
                              input=json.dumps({'hook_event_name': event}) if stdin is None else stdin, capture_output=True, text=True, env=env, timeout=20)

    def cache(self):
        return next((self.root / 'state/omarchy-ai-team').glob('usage-guard*.json'))

    def test_default_limit_is_ninety_percent(self):
        self.usage(session=0.89); self.assertEqual(self.context(self.run_guard()), '')
        self.cache().unlink()
        self.usage(session=0.9); self.assertIn('90', self.context(self.run_guard()))

    def test_stale_reading_is_refreshed(self):
        self.run_guard(); os.utime(self.cache(), (1, 1)); self.run_guard()
        self.assertEqual(self.calls.read_text().count('call'), 2)

    def test_cache_is_private(self):
        self.run_guard()
        self.assertEqual(stat.S_IMODE(self.cache().stat().st_mode), 0o600)

    def test_never_breaks_a_session(self):
        for body in ("#!/bin/sh\nexit 3\n", "#!/bin/sh\necho nope\n", "#!/bin/sh\necho '{}'\n"):
            with self.subTest(body):
                (self.bin / 'omarchy-agent-usage-codex').write_text(body)
                for cache in (self.root / 'state').rglob('usage-guard*.json'): cache.unlink()
                self.assertEqual(self.context(self.run_guard()), '')

    def test_reads_the_usage_of_its_own_account(self):
        self.run_guard()
        self.assertIn('/perfil/uno', self.calls.read_text())

    def test_each_account_has_its_own_reading(self):
        self.usage(session=0.99); self.run_guard()
        env = {'PATH': f"{self.bin}:{os.environ['PATH']}", 'XDG_STATE_HOME': str(self.root / 'state'), 'HOME': str(self.root)}
        subprocess.run([sys.executable, '-B', str(ROOT / 'backend/usage_guard.py'), '--settings', str(self.settings), '--agent', 'codex',
                        '--codex-home', '/perfil/dos'], input='{"hook_event_name":"PostToolUse"}', capture_output=True, text=True, env=env)
        self.assertIn('/perfil/dos', self.calls.read_text())


class CodexGuardHookTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.file = self.root / 'hooks.json'; self.config = self.root / 'plugin.json'

    def test_hook_names_the_agent_and_its_account(self):
        native_team.set_guard_hook(self.file, self.config, True, agent='codex', home=self.root / 'mi perfil')
        command = json.loads(self.file.read_text())['hooks']['PostToolUse'][0]['hooks'][0]['command']
        self.assertIn('--agent codex', command)
        self.assertIn('mi perfil', command)

    def test_the_command_does_not_change_between_runs_so_approval_stays_valid(self):
        native_team.set_guard_hook(self.file, self.config, True, agent='codex', home=self.root)
        before = self.file.read_text()
        native_team.set_guard_hook(self.file, self.config, True, agent='codex', home=self.root)
        self.assertEqual(self.file.read_text(), before)

    def test_a_foreign_hook_keeps_its_place_so_its_approval_stays_valid(self):
        foreign = {'hooks': [{'type': 'command', 'command': 'ajeno'}]}
        self.file.write_text(json.dumps({'hooks': {'PostToolUse': [foreign]}}))
        native_team.set_guard_hook(self.file, self.config, True, agent='codex', home=self.root)
        self.assertEqual(json.loads(self.file.read_text())['hooks']['PostToolUse'][0], foreign)


class CodexApprovalWarningTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)

    def test_warns_while_the_hook_is_not_approved(self):
        (self.root / 'config.toml').write_text('')
        (self.root / 'hooks.json').write_text(json.dumps({'hooks': {'PostToolUse': [{'hooks': [{'type': 'command', 'command': 'x'}]}]}}))
        self.assertFalse(native_team.guard_hook_trusted(self.root))

    def test_nothing_to_approve_without_hooks(self):
        self.assertTrue(native_team.guard_hook_trusted(self.root))
        (self.root / 'hooks.json').write_text('{}')
        self.assertTrue(native_team.guard_hook_trusted(self.root))

    def test_no_warning_once_approved(self):
        path = str(self.root / 'hooks.json')
        (self.root / 'hooks.json').write_text(json.dumps({'hooks': {'PostToolUse': [{'hooks': [{'type': 'command', 'command': 'x'}]}]}}))
        (self.root / 'config.toml').write_text(f'[hooks.state."{path}:post_tool_use:0:0"]\ntrusted_hash = "sha256:x"\n')
        self.assertTrue(native_team.guard_hook_trusted(self.root))

    def test_a_second_hook_without_approval_still_warns(self):
        path = str(self.root / 'hooks.json')
        hook = {'hooks': [{'type': 'command', 'command': 'x'}]}
        (self.root / 'hooks.json').write_text(json.dumps({'hooks': {'PostToolUse': [hook], 'UserPromptSubmit': [hook]}}))
        (self.root / 'config.toml').write_text(f'[hooks.state."{path}:post_tool_use:0:0"]\ntrusted_hash = "sha256:x"\n')
        self.assertFalse(native_team.guard_hook_trusted(self.root))

    def test_an_unreadable_config_counts_as_not_approved(self):
        (self.root / 'hooks.json').write_text(json.dumps({'hooks': {'PostToolUse': [{'hooks': [{'type': 'command', 'command': 'x'}]}]}}))
        (self.root / 'config.toml').write_text('= roto')
        self.assertFalse(native_team.guard_hook_trusted(self.root))


class CodexPendingStatusTests(unittest.TestCase):
    def test_each_profile_is_checked_in_its_own_home(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); root = Path(temp.name)
        hook = json.dumps({'hooks': {'PostToolUse': [{'hooks': [{'type': 'command', 'command': 'x'}]}]}})
        for name in ('.codex', '.codex-2'):
            (root / name).mkdir(); (root / name / 'hooks.json').write_text(hook); (root / name / 'config.toml').write_text('')
        path = root / '.codex/hooks.json'
        (root / '.codex/config.toml').write_text(f'[hooks.state."{path}:post_tool_use:0:0"]\ntrusted_hash = "sha256:x"\n')
        config = {'agents': {'codex': True, 'codex-2': True}, 'team': {'enabled': True, 'roles': {}}}
        with patch.dict(os.environ, {'HOME': str(root)}), patch.object(harness.Path, 'home', return_value=root):
            os.environ.pop('CODEX_HOME', None)
            self.assertEqual(harness.team_state(config)['usageGuardPending'], ['Codex-2'])


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


class ClaudeAccountGuardTests(unittest.TestCase):
    """The guard measures the Claude account of the session that runs it, never another one."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.settings = self.root / 'settings.json'
        self.bin = self.root / 'bin'; self.bin.mkdir()
        self.calls = self.root / 'calls'

    def run_guard(self, stdin, environment=None):
        env = {'PATH': f"{self.bin}:{os.environ['PATH']}", 'XDG_STATE_HOME': str(self.root / 'state'), 'HOME': str(self.root),
               **(environment or {})}
        return subprocess.run([sys.executable, '-B', str(ROOT / 'backend/usage_guard.py'), '--settings', str(self.settings)],
                              input=stdin, capture_output=True, text=True, env=env, timeout=20)

    def context(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['hookSpecificOutput']['additionalContext'] if result.stdout.strip() else ''

    def usage_by_account(self, main=0.98, second=0.04):
        """A collector whose reading depends on the Claude directory it is run with."""
        tool = self.bin / 'omarchy-agent-usage-claude'
        tool.write_text('#!/bin/sh\necho "dir=$CLAUDE_CONFIG_DIR" >> \'' + str(self.calls) + '\'\n'
                        'if [ -n "$CLAUDE_CONFIG_DIR" ]; then p=' + str(second) + '; else p=' + str(main) + '; fi\n'
                        'echo "{\\"limits\\": [{\\"label\\": \\"Weekly (7-day)\\", \\"percent\\": $p}]}"\n')
        tool.chmod(0o700)

    def event_of(self, profile):
        folder = self.root / profile / 'projects' / 'p'
        return json.dumps({'hook_event_name': 'PostToolUse', 'transcript_path': str(folder / 'session.jsonl')})

    def test_a_second_account_session_reads_its_own_limits_not_the_main_ones(self):
        self.usage_by_account(main=0.98, second=0.04)
        self.assertEqual(self.context(self.run_guard(self.event_of('.claude-2'))), '')
        self.assertIn('dir=' + str(self.root / '.claude-2'), self.calls.read_text())

    def test_the_two_claude_accounts_never_share_a_cached_reading(self):
        self.usage_by_account(main=0.98, second=0.04)
        self.assertIn('98', self.context(self.run_guard(self.event_of('.claude'))))
        self.assertEqual(self.context(self.run_guard(self.event_of('.claude-2'))), '')
        self.assertIn('98', self.context(self.run_guard(self.event_of('.claude'))))

    def test_each_account_gets_its_own_collector_cache(self):
        # The official collector keeps its limits in $XDG_CACHE_HOME/omarchy/agent-usage and reuses them for a while.
        tool = self.bin / 'omarchy-agent-usage-claude'
        tool.write_text('#!/bin/sh\n'
                        'cache="${XDG_CACHE_HOME:-$HOME/.cache}/shared-limits"\n'
                        'if [ -f "$cache" ]; then cat "$cache"; exit 0; fi\n'
                        'mkdir -p "$(dirname "$cache")"\n'
                        'if [ -n "$CLAUDE_CONFIG_DIR" ]; then p=0.04; else p=0.98; fi\n'
                        'out="{\\"limits\\": [{\\"label\\": \\"Weekly (7-day)\\", \\"percent\\": $p}]}"\n'
                        'echo "$out" > "$cache"; echo "$out"\n')
        tool.chmod(0o700)
        self.assertIn('98', self.context(self.run_guard(self.event_of('.claude'))))
        self.assertEqual(self.context(self.run_guard(self.event_of('.claude-2'))), '')
        cache = self.root / '.cache/omarchy-ai-team/usage'
        self.assertEqual(cache.stat().st_mode & 0o777, 0o700)

    def test_the_main_account_is_read_without_a_config_directory_even_if_the_environment_has_one(self):
        self.usage_by_account(main=0.98, second=0.04)
        result = self.run_guard(self.event_of('.claude'), {'CLAUDE_CONFIG_DIR': str(self.root / '.claude-2')})
        self.assertIn('98', self.context(result))

    def test_without_a_transcript_the_environment_decides_the_account(self):
        self.usage_by_account(main=0.98, second=0.04)
        result = self.run_guard(json.dumps({'hook_event_name': 'PostToolUse'}), {'CLAUDE_CONFIG_DIR': str(self.root / '.claude-2')})
        self.assertEqual(self.context(result), '')


if __name__ == '__main__':
    unittest.main()
