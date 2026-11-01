"""Launch policy contracts; all state and profiles in these tests are synthetic."""

from contextlib import ExitStack
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import harness
import native_launcher


class ContextSettingsTests(unittest.TestCase):
    def setUp(self):
        # A missing implementation is an assertion failure in the first RED run.
        self.assertIsNotNone(importlib.util.find_spec('context_policy'),
                             'pure context_policy module is missing')
        import context_policy
        self.policy = context_policy

    def test_pi_invocation_injects_process_extension_and_capsule(self):
        import context_state
        args, env = native_launcher.invocation('pi', config={}, role='code', cwd=Path.cwd())
        self.assertEqual(args[0], '--extension')
        self.assertTrue(Path(args[1]).is_file())
        self.assertEqual(args[2], '--append-system-prompt')
        self.assertEqual(len(args), 4)
        capsule = json.loads(next(item.partition('=')[2] for item in env if item.startswith('AI_TEAM_CONTEXT=')))
        self.assertEqual(args[3], context_state.instructions(capsule))
        self.assertEqual(capsule['policy']['compact_at_tokens'], 150000)

    def test_pi_extension_enforces_threshold_floor_and_unknown_usage(self):
        import subprocess
        extension = Path(__file__).resolve().parents[1] / 'extensions/pi-context-policy.ts'
        script = """
const {default: extension} = await import(process.argv[1]);
const handlers = {}; const calls = []; const messages = [];
extension({on: (event, fn) => handlers[event] = fn});
let tokens = null;
const ctx = {hasUI: true, ui: {notify: message => messages.push(message)},
 getContextUsage: () => ({tokens}), compact: options => calls.push(options)};
handlers.turn_end({}, ctx);
tokens = 150000; handlers.turn_end({}, ctx);
tokens = 150001; handlers.turn_end({}, ctx);
const low = await handlers.session_before_compact({preparation: {tokensBefore: 69999}}, ctx);
const enough = await handlers.session_before_compact({preparation: {tokensBefore: 70000}}, ctx);
console.log(JSON.stringify({calls, messages, low, enough: enough ?? null}));
"""
        capsule = {'policy': {'compact_at_tokens': 150000, 'compact_floor_tokens': 70000, 'dump_state_before': False}}
        result = subprocess.run(['node', '--input-type=module', '-e', script, extension.as_uri()],
                                env={**os.environ, 'AI_TEAM_CONTEXT': json.dumps(capsule)},
                                capture_output=True, text=True, check=True)
        value = json.loads(result.stdout)
        self.assertEqual(len(value['calls']), 1)
        self.assertIn('archivos:líneas', value['calls'][0]['customInstructions'])
        self.assertEqual(value['low'], {'cancel': True})
        self.assertIsNone(value['enough'])
        self.assertTrue(any('desconocidos' in message for message in value['messages']))

    def test_pi_precompact_failure_is_reported_without_cancelling(self):
        import subprocess
        import tempfile
        import shutil
        extension = Path(__file__).resolve().parents[1] / 'extensions/pi-context-policy.ts'
        script = """
const {default: extension} = await import(process.argv[1]);
const handlers = {}; const messages = [];
extension({on: (event, fn) => handlers[event] = fn});
const result = await handlers.session_before_compact({preparation: {tokensBefore: 150001}},
 {hasUI: true, ui: {notify: message => messages.push(message)}});
console.log(JSON.stringify({result: result ?? null, messages}));
"""
        capsule = {'policy': {'compact_at_tokens': 150000, 'compact_floor_tokens': 70000, 'dump_state_before': True}}
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'PATH': directory, 'AI_TEAM_CONTEXT': json.dumps(capsule)}
            result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script, extension.as_uri()],
                                    env=env, capture_output=True, text=True, check=True)
        value = json.loads(result.stdout)
        self.assertIsNone(value['result'])
        self.assertIn('failed', value['messages'][0])

    def test_pi_precompact_slow_child_does_not_block_event_loop(self):
        import shutil
        import subprocess
        extension = Path(__file__).resolve().parents[1] / 'extensions/pi-context-policy.ts'
        script = """
const {default: extension} = await import(process.argv[1]);
const handlers = {}; let ticked = false;
extension({on: (event, fn) => handlers[event] = fn});
setTimeout(() => { ticked = true; }, 10);
const result = await handlers.session_before_compact({preparation: {tokensBefore: 150001}},
 {hasUI: true, ui: {notify: () => {}}});
console.log(JSON.stringify({ticked, result: result ?? null}));
"""
        capsule = {'policy': {'compact_floor_tokens': 70000, 'dump_state_before': True}}
        with tempfile.TemporaryDirectory() as directory:
            child = Path(directory) / 'python3'
            child.write_text('#!/usr/bin/python3\nimport time\ntime.sleep(0.3)\nprint(\'{"status":"ok"}\')\n')
            child.chmod(0o700)
            env = {**os.environ, 'PATH': directory, 'AI_TEAM_CONTEXT': json.dumps(capsule)}
            result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script, extension.as_uri()],
                                    env=env, capture_output=True, text=True, check=True, timeout=5)
        self.assertEqual(json.loads(result.stdout), {'ticked': True, 'result': None})

    def test_defaults_resolve_every_role_without_mutating_input_or_shared_defaults(self):
        config = {'foreign': {'keep': True}}
        original = deepcopy(config)
        for role, threshold in [('jefe', 100000), ('plan', 150000), ('code', 150000),
                                ('review', 150000), ('simple', 150000)]:
            with self.subTest(role=role):
                resolved = self.policy.resolve(config, role)
                self.assertEqual(resolved['compact_at_tokens'], threshold)
                self.assertEqual(resolved['compact_floor_tokens'], 70000)
                self.assertEqual(resolved['worker_report_max_tokens'], 800)
                self.assertIs(resolved['enabled'], True)
                self.assertEqual(resolved['apply'], 'launch')
        self.assertEqual(config, original)
        first = self.policy.settings(config)
        first['roles']['jefe']['compact_at_tokens'] = 200000
        first['worker_report_on'].append('foreign')
        self.assertEqual(self.policy.resolve(config, 'jefe')['compact_at_tokens'], 100000)
        self.assertEqual(self.policy.settings(config)['worker_report_on'], ['milestone', 'blocked'])

    def test_user_then_role_then_reasoned_invocation_precedence_without_saving(self):
        config = {'context': {'compact_at_tokens': 180000, 'compact_floor_tokens': 60000,
                              'worker_report_max_tokens': 900, 'roles': {
                                  'jefe': {'compact_at_tokens': 120000},
                                  'plan': {'compact_at_tokens': 160000},
                                  'code': {'compact_floor_tokens': 80000},
                                  'review': {'worker_report_max_tokens': 700},
                                  'simple': {'compact_at_tokens': 130000}}}}
        original = deepcopy(config)
        expected = [('jefe', 120000, 60000, 900), ('plan', 160000, 60000, 900),
                    ('code', 180000, 80000, 900), ('review', 180000, 60000, 700),
                    ('simple', 130000, 60000, 900)]
        for role, threshold, floor, report in expected:
            resolved = self.policy.resolve(config, role)
            self.assertEqual([resolved[k] for k in ('compact_at_tokens', 'compact_floor_tokens',
                                                   'worker_report_max_tokens')], [threshold, floor, report])
        override = {'compact_at_tokens': 220000, 'compact_floor_tokens': 90000,
                    'worker_report_max_tokens': 1000, 'reason': 'Finish the current debug step.'}
        resolved = self.policy.resolve(config, 'code', override=override)
        self.assertEqual(resolved['compact_at_tokens'], 220000)
        self.assertEqual(resolved['compact_floor_tokens'], 90000)
        self.assertEqual(resolved['worker_report_max_tokens'], 1000)
        self.assertEqual(resolved['reason'], override['reason'])
        self.assertEqual(config, original)
        self.assertEqual(self.policy.resolve(config, 'code')['compact_at_tokens'], 180000)

    def test_rejects_unknown_keys_wrong_types_ranges_and_inconsistent_roles(self):
        invalid = [None, [], {'extra': True}, {'enabled': 1}, {'enabled': 'false'},
                   {'apply': 'global'}, {'compact_at_tokens': True},
                   {'compact_at_tokens': 0}, {'compact_at_tokens': 1.5},
                   {'compact_at_tokens': 2**63}, {'compact_floor_tokens': -1},
                   {'compact_floor_tokens': '70000'}, {'compact_floor_tokens': 100000},
                   {'worker_report_max_tokens': 0}, {'worker_report_max_tokens': False},
                   {'dump_state_before': 'yes'}, {'chief_brief': 'full_body'},
                   {'worker_report_on': 'milestone'}, {'worker_report_on': []},
                   {'worker_report_on': ['blocked', 'blocked']},
                   {'worker_report_on': ['command']}, {'roles': []},
                   {'roles': {'unknown': {}}}, {'roles': {'code': None}},
                   {'roles': {'code': {'enabled': False}}},
                   {'roles': {'code': {'compact_at_tokens': 70000}}},
                   {'roles': {'plan': {'worker_report_max_tokens': '800'}}}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(self.policy.ContextPolicyError):
                self.policy.settings({'context': value})
        for role in ('chief', '', None):
            with self.subTest(role=role), self.assertRaises(self.policy.ContextPolicyError):
                self.policy.resolve({}, role)

    BOOLS = ('task_plan_with_pointers', 'fresh_worker_per_subtask', 'bounded_reads',
             'stable_prompt_prefix', 'measure_task_tokens', 'clear_between_tasks_below_floor')

    def test_new_savings_defaults_resolve_for_every_role_and_old_config_still_loads(self):
        old = {'context': {'compact_at_tokens': 180000}}
        original = deepcopy(old)
        for role in ('jefe', 'plan', 'code', 'review', 'simple'):
            resolved = self.policy.resolve(old, role)
            for key in self.BOOLS:
                self.assertIs(resolved[key], True, key)
            self.assertEqual(resolved['read_window_lines'], 120)
        self.assertEqual(old, original)
        self.assertEqual(self.policy.settings({})['read_window_lines'], 120)

    def test_new_keys_validate_types_and_read_window_range_strictly(self):
        invalid = [{key: 1} for key in self.BOOLS] + [{key: 'true'} for key in self.BOOLS]
        invalid += [{'read_window_lines': value} for value in (0, 2001, -1, True, 1.5, '120', None)]
        invalid += [{'roles': {'code': {'read_window_lines': 0}}},
                    {'roles': {'code': {'bounded_reads': 'no'}}},
                    {'roles': {'code': {'chief_brief': 'memory_index'}}}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(self.policy.ContextPolicyError):
                self.policy.settings({'context': value})
        for edge in (1, 2000):
            self.assertEqual(self.policy.settings({'context': {'read_window_lines': edge}})
                             ['read_window_lines'], edge)

    def test_new_keys_follow_user_role_then_reasoned_override_precedence(self):
        config = {'context': {'read_window_lines': 200, 'bounded_reads': False, 'roles': {
            'code': {'read_window_lines': 50, 'fresh_worker_per_subtask': False},
            'review': {'bounded_reads': True}}}}
        original = deepcopy(config)
        self.assertEqual(self.policy.resolve(config, 'plan')['read_window_lines'], 200)
        self.assertIs(self.policy.resolve(config, 'plan')['bounded_reads'], False)
        code = self.policy.resolve(config, 'code')
        self.assertEqual((code['read_window_lines'], code['fresh_worker_per_subtask']), (50, False))
        self.assertIs(self.policy.resolve(config, 'review')['bounded_reads'], True)
        forced = self.policy.resolve(config, 'code', override={
            'read_window_lines': 400, 'measure_task_tokens': False, 'reason': 'Wide refactor.'})
        self.assertEqual((forced['read_window_lines'], forced['measure_task_tokens']), (400, False))
        with self.assertRaises(self.policy.ContextPolicyError):
            self.policy.resolve(config, 'code', override={'read_window_lines': 0, 'reason': 'x'})
        with self.assertRaises(self.policy.ContextPolicyError):
            self.policy.resolve(config, 'code', override={'bounded_reads': 1, 'reason': 'x'})
        self.assertEqual(config, original)
        self.assertEqual(self.policy.resolve(config, 'code')['read_window_lines'], 50)

    def test_invocation_requires_reason_and_validates_effective_values(self):
        invalid = [{}, {'compact_at_tokens': 180000}, {'reason': ''}, {'reason': '  '},
                   {'reason': 42}, {'reason': 'why', 'apply': 'global'},
                   {'reason': 'why', 'roles': {}}, {'reason': 'why', 'enabled': 1},
                   {'reason': 'why', 'compact_at_tokens': 70000},
                   {'reason': 'why', 'compact_floor_tokens': 200000}]
        for override in invalid:
            with self.subTest(override=override), self.assertRaises(self.policy.ContextPolicyError):
                self.policy.resolve({}, 'code', override=override)
        self.assertFalse(self.policy.resolve({}, 'code', override={
            'enabled': False, 'reason': 'Explicit one-run exception.'})['enabled'])

    def test_harness_load_validates_without_materializing_or_rewriting_defaults(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'XDG_CONFIG_HOME': folder}):
            path = harness.config_path()
            self.assertNotIn('context', harness.load_config())
            self.assertFalse(path.exists())
            path.parent.mkdir()
            text = '{"schemaVersion": 1, "agents": {}, "context": {"enabled": false}}\n'
            path.write_text(text)
            self.assertEqual(harness.load_config()['context'], {'enabled': False})
            self.assertEqual(path.read_text(), text)
            path.write_text('{"schemaVersion": 1, "context": {"apply": "global"}}')
            before = path.read_bytes()
            with self.assertRaises(self.policy.ContextPolicyError):
                harness.load_config()
            self.assertEqual(path.read_bytes(), before)


class ContextLaunchTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(folder)
        self.stack.enter_context(patch.dict(os.environ, {
            'HOME': folder, 'XDG_CONFIG_HOME': str(self.root / 'config'),
            'CODEX_HOME': '/inherited/codex', 'CLAUDE_CONFIG_DIR': '/inherited/claude',
            'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '900000', 'UNRELATED': 'keep'}))
        self.stack.enter_context(patch.object(native_launcher.shutil, 'which', return_value='/usr/bin/omarchy'))
        self.popen = self.stack.enter_context(patch.object(native_launcher.subprocess, 'Popen'))

    def launch(self, agent, **kwargs):
        native_launcher.launch('/native/' + agent, agent, self.root / agent, **kwargs)
        return self.popen.call_args

    def test_claude_env_precedence_is_explicit_inside_terminal_and_parent_is_unchanged(self):
        before = dict(os.environ)
        call = self.launch('claude', config={}, role='code', arguments=('--continue',))
        args = call.args[0]
        self.assertIn('CLAUDE_CODE_AUTO_COMPACT_WINDOW=150000', args)
        self.assertIn('CLAUDE_CONFIG_DIR=' + str(self.root / 'claude'), args)
        index = args.index('/native/claude')
        self.assertLess(args.index('CLAUDE_CODE_AUTO_COMPACT_WINDOW=150000'), index)
        self.assertEqual(args[index + 1:index + 3], ['--autocompact', '150000'])
        self.assertEqual(args[-1], '--continue')
        self.assertEqual(dict(os.environ), before)

    def test_both_codex_profiles_get_total_scope_and_preserve_original_arguments(self):
        for agent in ('codex', 'codex-2'):
            with self.subTest(agent=agent):
                args = self.launch(agent, config={}, role='jefe', arguments=('resume', '--last')).args[0]
                self.assertIn('CODEX_HOME=' + str(self.root / agent), args)
                index = args.index('/native/' + agent)
                self.assertEqual(args[index + 1:index + 5], ['-c', 'model_auto_compact_token_limit=100000',
                                                   '-c', 'model_auto_compact_token_limit_scope="total"'])
                self.assertEqual(args[-2:], ['resume', '--last'])

    def test_disabled_matches_complete_legacy_launch_argv_and_environment(self):
        before = dict(os.environ)
        for agent in ('claude', 'codex', 'codex-2', 'pi'):
            with self.subTest(agent=agent):
                legacy = self.launch(agent, arguments=('original literal',))
                disabled = self.launch(agent, config={'context': {'enabled': False}},
                                       arguments=('original literal',))
                self.assertEqual(disabled, legacy)
                self.assertEqual(dict(os.environ), before)

    def test_override_reaches_only_current_launch(self):
        config = {'context': {'roles': {'review': {'compact_at_tokens': 170000}}}}
        original = deepcopy(config)
        args = self.launch('claude', config=config, role='review', context_override={
            'compact_at_tokens': 210000, 'reason': 'Review a large change.'}).args[0]
        self.assertIn('CLAUDE_CODE_AUTO_COMPACT_WINDOW=210000', args)
        args = self.launch('claude', config=config, role='review').args[0]
        self.assertIn('CLAUDE_CODE_AUTO_COMPACT_WINDOW=170000', args)
        self.assertEqual(config, original)

    def test_unsupported_native_ranges_fail_before_spawning(self):
        for agent, threshold in [('claude', 99999), ('claude', 1000001)]:
            with self.subTest(agent=agent, threshold=threshold):
                self.popen.reset_mock()
                with self.assertRaisesRegex(ValueError, 'Claude|Pi'):
                    self.launch(agent, config={'context': {'roles': {'code': {
                        'compact_at_tokens': threshold}}}}, role='code')
                self.popen.assert_not_called()

    def test_login_preserves_previous_invocation_without_policy(self):
        for agent, expected in [('claude', ['auth', 'login', '--claudeai']),
                                ('codex', ['login']), ('codex-2', ['login'])]:
            with self.subTest(agent=agent):
                native_launcher.authenticate('/native/' + agent, agent, self.root / agent)
                call = self.popen.call_args
                legacy = self.launch(agent, arguments=expected)
                self.assertEqual(call, legacy)
                self.assertNotIn('--autocompact', call.args[0])

    def test_harness_launch_with_drift_never_syncs_or_writes_global_integrations(self):
        profile = self.root / '.codex'
        profile.mkdir()
        drift = profile / 'config.toml'
        drift.write_text('# Deliberate unsynced user integration\n')
        before = drift.read_bytes()
        config = {'agents': {'codex': True}, 'team': {'enabled': True}, 'memoryEnabled': True}
        with patch.object(harness, 'mise_inventory', return_value=({}, '')), \
             patch.object(harness, 'agent_tool', return_value={'installation': 'installed', 'binary': '/native/codex'}), \
             patch.object(harness, 'status', return_value={'components': [{'id': 'memory', 'enabled': True}]}), \
             patch.object(harness, 'manage_component', side_effect=AssertionError('launch attempted global sync')), \
             patch.object(harness, 'save_config', side_effect=AssertionError('launch attempted settings write')):
            harness.launch_agent(config, 'codex')
        self.assertEqual(drift.read_bytes(), before)
        self.assertIn('model_auto_compact_token_limit=100000', self.popen.call_args.args[0])

    def test_harness_cli_passes_role_and_json_override_without_saving(self):
        config = {'agents': {'codex': True}, 'defaultAgent': 'codex'}
        with patch.object(harness, 'load_config', return_value=config), \
             patch.object(harness, 'mise_inventory', return_value=({}, '')), \
             patch.object(harness, 'agent_tool', return_value={'installation': 'installed', 'binary': '/native/codex'}), \
             patch.object(harness, 'status', return_value={}), \
             patch.object(harness, 'save_config', side_effect=AssertionError('override saved')), \
             patch('builtins.print'):
            for command in (['launch', 'codex'], ['launch-default']):
                result = harness.main(command + ['--role', 'plan', '--context-override', json.dumps({
                    'compact_at_tokens': 190000, 'reason': 'Plan a large migration.'})])
                self.assertEqual(result, 0)
                self.assertIn('model_auto_compact_token_limit=190000', self.popen.call_args.args[0])

    def test_explicit_null_cli_override_is_rejected_without_launch(self):
        with patch('sys.stderr'), self.assertRaises(SystemExit) as error:
            harness.main(['launch', 'codex', '--context-override', 'null'])
        self.assertEqual(error.exception.code, 2)
        self.popen.assert_not_called()

    def test_disabled_invocation_preserves_legacy_command(self):
        for agent in ('claude', 'codex', 'codex-2', 'pi'):
            with self.subTest(agent=agent):
                legacy = self.launch(agent)
                exception = self.launch(agent, config={}, context_override={
                    'enabled': False, 'reason': 'One-run exception.'})
                self.assertEqual(exception, legacy)

    def test_claude_native_range_includes_both_endpoints(self):
        for threshold in (100000, 1000000):
            with self.subTest(threshold=threshold):
                call = self.launch('claude', config={}, context_override={
                    'compact_at_tokens': threshold, 'reason': 'Check supported native range.'})
                self.assertIn('CLAUDE_CODE_AUTO_COMPACT_WINDOW=' + str(threshold), call.args[0])

    def test_pi_login_is_outside_policy_even_while_normal_pi_support_is_pending(self):
        config = {'agents': {'pi': True}}
        legacy = self.launch('pi')
        with patch.object(harness, 'load_config', return_value=config), \
             patch.object(harness, 'mise_inventory', return_value=({}, '')), \
             patch.object(harness, 'agent_tool', return_value={'installation': 'installed', 'binary': '/native/pi'}), \
             patch.object(harness.native_pi, 'home', return_value=self.root / 'pi'), \
             patch.object(harness, 'status', return_value={}), patch('builtins.print'):
            code = harness.main(['pi-auth'])
        self.assertEqual(code, 0)
        self.assertEqual(self.popen.call_args, legacy)


if __name__ == '__main__':
    unittest.main()
