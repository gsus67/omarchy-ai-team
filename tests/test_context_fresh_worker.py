"""Every subtask is a new native process: no resume/continue and no persisted session."""
import importlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import native_team

REPORT = {'status': 'done', 'references': 'backend/a.py:1', 'tests': '1 passed', 'error': '', 'pending': ''}
CODEX = ['codex', 'exec', '-C', '.', '-s', 'read-only', '-m', 'chosen',
         '-c', 'model_reasoning_effort="high"', 'task']
CLAUDE = ['claude', '-p', 'task', '--model', 'chosen', '--effort', 'high', '--permission-mode', 'plan']


class FreshWorkerTests(unittest.TestCase):
    def setUp(self):
        self.run = importlib.import_module('context_run')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'resume').symlink_to(self.root, target_is_directory=True)

    def test_each_subtask_gets_its_own_instance_and_a_non_persistent_native_session(self):
        for agent, command, flag in [('codex', CODEX, '--ephemeral'), ('codex-2', CODEX, '--ephemeral'),
                                     ('claude', CLAUDE, '--no-session-persistence')]:
            first, _, one = self.run.prepare(command, agent, 'code', {}, self.root)
            second, _, two = self.run.prepare(command, agent, 'code', {}, self.root)
            with self.subTest(agent=agent):
                self.assertNotEqual(one['id'], two['id'])
                self.assertEqual(first.count(flag), 1)
                self.assertEqual(second.count(flag), 1)
                for text in ('resume', 'fork', '--resume', '--continue'):
                    self.assertNotIn(text, first)
                if agent != 'claude':
                    self.assertEqual(first.index(flag), first.index('exec') + 1)

    def test_model_effort_sandbox_and_permissions_are_preserved(self):
        codex, _, _ = self.run.prepare(CODEX, 'codex', 'review', {}, self.root)
        for token in ('-s', 'read-only', '-m', 'chosen', '-C', '.', 'model_reasoning_effort="high"'):
            self.assertIn(token, codex)
        claude, _, _ = self.run.prepare(CLAUDE, 'claude', 'review', {}, self.root)
        for token in ('--model', 'chosen', '--effort', 'high', '--permission-mode', 'plan'):
            self.assertIn(token, claude)
        self.assertNotIn('bypass', ' '.join(codex + claude))

    def test_continuation_is_rejected_per_cli_but_codex_dash_c_override_is_valid(self):
        rejected = [('codex', ['codex', 'resume', '--last']), ('codex', ['codex', 'exec', 'resume', 'id', 'task']),
                    ('codex-2', ['codex', 'fork', 'id']),
                    ('claude', ['claude', '-p', 'x', '--resume', 'id']), ('claude', ['claude', '-p', 'x', '-r']),
                    ('claude', ['claude', '-p', 'x', '--continue']), ('claude', ['claude', '-p', 'x', '-c']),
                    ('claude', ['claude', '-p', 'x', '--fork-session'])]
        for agent, command in rejected:
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.run.prepare(command, agent, 'code', {}, self.root)
        prepared, _, _ = self.run.prepare(CODEX, 'codex', 'code', {}, self.root)
        self.assertIn('model_reasoning_effort="high"', prepared)

    def test_continuation_is_rejected_wherever_it_appears_among_the_arguments(self):
        rejected = [('codex', ['codex', '-c', 'model_reasoning_effort=high', 'exec', 'resume', '--last', 'task']),
                    ('codex', ['codex', '-m', 'x', '-s', 'read-only', 'resume', '--last']),
                    ('codex', ['codex', '--model=x', 'fork', 'id']),
                    ('codex-2', ['codex', '-C', '/work', 'exec', '--skip-git-repo-check', 'resume', 'id']),
                    ('codex', ['codex', 'exec', '-C', '/work', '-c', 'k=v', 'resume', '--last', 'task']),
                    ('codex', ['codex', 'e', '-c', 'k=v', 'resume', 'id']),
                    ('claude', ['claude', '-c', '-p', 'x']),
                    ('claude', ['claude', '--model', 'x', '-p', 'x', '-c']),
                    ('claude', ['claude', '--permission-mode', 'plan', '--resume=abc', '-p', 'x']),
                    ('claude', ['claude', '-p', 'x', '--from-pr', '12'])]
        for agent, command in rejected:
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.run.prepare(command, agent, 'code', {}, self.root)

    def test_codex_option_values_and_prompt_text_are_not_mistaken_for_continuation(self):
        accepted = [['codex', '-c', 'model_reasoning_effort=high', 'exec', 'task'],
                    ['codex', 'exec', '-C', 'resume', 'task'],
                    ['codex', 'exec', '-c', 'resume=1', 'resume the plan'],
                    ['codex', 'exec', '--', 'resume'],
                    ['codex', 'exec', '--', 'fork'],
                    # -i takes several files: the word after the first one is another image, not a subcommand
                    ['codex', 'exec', '-i', 'one.png', 'resume', '-m', 'chosen', 'task'],
                    ['codex', 'exec', '--image', 'one.png', 'two.png', 'fork', '-m', 'chosen', 'task']]
        for command in accepted:
            with self.subTest(command=command):
                prepared, _, _ = self.run.prepare(command, 'codex', 'code', {}, self.root)
                self.assertEqual(prepared.count('--ephemeral'), 1)
                self.assertEqual(prepared[prepared.index('--ephemeral') - 1], 'exec')

    def test_a_literal_ephemeral_prompt_after_the_delimiter_does_not_hide_the_real_option(self):
        prepared, _, _ = self.run.prepare(['codex', 'exec', '--', '--ephemeral'], 'codex', 'code', {}, self.root)
        self.assertEqual(prepared[prepared.index('exec') + 1], '--ephemeral')
        self.assertEqual(prepared.index('--ephemeral'), prepared.index('exec') + 1)
        self.assertLess(prepared.index('--ephemeral'), prepared.index('--'))

    def test_codex_context_rejects_protected_config_keys(self):
        for agent in ('codex', 'codex-2'):
            for key, value in (('model_auto_compact_token_limit', '123'), ('features.hooks', 'false'),
                               ('features.hooks', 'true')):
                command = ['codex', 'exec', '-c', f'{key}={value}', 'task']
                with self.subTest(agent=agent, override=f'{key}={value}'), \
                        self.assertRaisesRegex(ValueError, re.escape(key)):
                    self.run.prepare(command, agent, 'code', {}, self.root)

    def test_codex_context_preserves_unrelated_overrides(self):
        command = ['codex', 'exec', '-c', 'model_reasoning_effort="high"', '-c', 'other.key=1', '-m', 'chosen', 'task']
        for agent in ('codex', 'codex-2'):
            prepared, _, _ = self.run.prepare(command, agent, 'code', {}, self.root)
            tail = prepared[prepared.index('exec'):]
            self.assertEqual([item for item in tail if item != '--ephemeral'][-len(command) + 1:], command[1:])
            self.assertLess(prepared.index('-c'), prepared.index('exec'))  # injected ones stay in front

    def test_codex_context_rejects_equivalent_config_forms(self):
        key = 'model_auto_compact_token_limit'
        forms = [['-c', f'{key}=1'], ['--config', f'{key}=1'], [f'-c{key}=1'], [f'-c={key}=1'], [f'--config={key}=1']]
        for agent in ('codex', 'codex-2'):
            for form in forms:
                for command in (['codex', *form, 'exec', 'task'], ['codex', 'exec', *form, 'task'],
                                ['codex', 'exec', 'task', *form], ['codex', 'e', *form, 'task']):
                    with self.subTest(agent=agent, command=command), self.assertRaisesRegex(ValueError, key):
                        self.run.prepare(command, agent, 'code', {}, self.root)

    def test_codex_context_rejects_hook_feature_switches(self):
        for agent in ('codex', 'codex-2'):
            for flag in ('--enable', '--disable'):
                for name in ('hooks', 'features.hooks'):
                    for form in ([flag, name], [f'{flag}={name}']):
                        for command in (['codex', *form, 'exec', 'task'], ['codex', 'exec', *form, 'task'],
                                        ['codex', 'exec', 'task', *form], ['codex', 'e', *form, 'task']):
                            with self.subTest(agent=agent, command=command), \
                                    self.assertRaisesRegex(ValueError, re.escape('features.hooks')):
                                self.run.prepare(command, agent, 'code', {}, self.root)

    def test_codex_context_rejects_features_parent_overrides(self):
        # `features={hooks=false}` replaces the whole table and drops the injected features.hooks.
        values = ['features={hooks=false}', 'features = {hooks = false}', 'features={hooks=true}', 'features={}',
                  'features={other=true}', 'features={other={hooks=false}}', 'features={hooks=false,}']
        for agent in ('codex', 'codex-2'):
            for value in values:
                for command in (['codex', 'exec', '-c', value, 'task'], ['codex', 'exec', '--config', value, 'task'],
                                ['codex', 'exec', f'-c{value}', 'task'], ['codex', 'exec', f'--config={value}', 'task']):
                    with self.subTest(agent=agent, command=command), \
                            self.assertRaisesRegex(ValueError, re.escape('features.hooks')):
                        self.run.prepare(command, agent, 'code', {}, self.root)

    def test_codex_context_config_key_boundaries(self):
        rejected = ['features.hooks=false=extra', 'features.hooks=', ' features.hooks = false ',
                    ' model_auto_compact_token_limit = 123 ']
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.run.prepare(['codex', 'exec', '-c', value, 'task'], 'codex', 'code', {}, self.root)
        accepted = ['other="features.hooks=false"', 'FEATURES.HOOKS=false', 'features.Hooks=false',
                    'MODEL_AUTO_COMPACT_TOKEN_LIMIT=1', 'features . hooks=false',
                    'model_auto_compact_token_limit_scope="x"', 'features.hooks_extra=1',
                    'features.hooks']
        for value in accepted:
            with self.subTest(value=value):
                command = ['codex', 'exec', '-c', value, 'task']
                prepared, _, _ = self.run.prepare(command, 'codex', 'code', {}, self.root)
                self.assertEqual(prepared[-3:], command[2:])
        for command in (['codex', 'exec', 'task', '-c'], ['codex', 'exec', '--config=', 'task'],
                        ['codex', 'exec', 'task', '--enable'], ['codex', 'exec', 'task', '--disable']):
            with self.subTest(command=command):
                self.run.prepare(command, 'codex', 'code', {}, self.root)  # a syntax error is left to Codex

    def test_codex_context_checks_every_repeated_override(self):
        for command in (['codex', 'exec', '-c', 'features.hooks=false', '-c', 'other=1', 'task'],
                        ['codex', 'exec', '-c', 'other=1', '-c', 'features.hooks=false', 'task'],
                        ['codex', 'exec', '-c', 'features.hooks=true', '-c', 'features.hooks=false', 'task'],
                        ['codex', 'exec', '-c', 'features.hooks=false', '-c', 'features.hooks=true', 'task'],
                        ['codex', 'exec', '-c', 'model_auto_compact_token_limit=1', '--enable', 'hooks', 'task']):
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.run.prepare(command, 'codex', 'code', {}, self.root)

    def test_codex_context_ignores_prompt_and_option_values(self):
        accepted = [['codex', 'exec', 'explica -c features.hooks=false'],
                    ['codex', 'exec', '--', '-c', 'features.hooks=false'],
                    ['codex', 'exec', 'task', '--', '--enable', 'hooks'],
                    ['codex', 'exec', '--model', '-cfeatures.hooks=false', 'task'],
                    ['codex', 'exec', '-m=features.hooks=false', 'task'],
                    ['codex', 'exec', '--output-last-message', '--enable', 'task'],
                    ['codex', 'exec', '-o', 'features.hooks=false', 'task'],
                    ['codex', 'exec', '-c', 'other=1', 'task', '-c', 'k=-cfeatures.hooks=false'],
                    ['codex', 'exec', '--config', '-cmodel_auto_compact_token_limit=1', 'task'],
                    ['codex', 'exec', '-i', 'one.png', 'two.png', '--', '--enable', 'hooks']]
        for command in accepted:
            with self.subTest(command=command):
                prepared, _, _ = self.run.prepare(command, 'codex', 'code', {}, self.root)
                self.assertEqual(prepared[-len(command) + 2:], command[2:])
        for command in (['codex', 'exec', '-i', 'one.png', '-c', 'features.hooks=false', 'task'],
                        ['codex', 'exec', '--image', 'one.png', 'two.png', '--enable', 'hooks', 'task'],
                        ['codex', 'exec', '-m', 'chosen', 'task', '-cfeatures.hooks=false']):
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.run.prepare(command, 'codex', 'code', {}, self.root)

    def test_setting_off_keeps_the_previous_recipe_and_allows_continuation(self):
        config = {'context': {'fresh_worker_per_subtask': False}}
        prepared, _, _ = self.run.prepare(CODEX, 'codex', 'code', config, self.root)
        self.assertNotIn('--ephemeral', prepared)
        prepared, _, _ = self.run.prepare(CLAUDE, 'claude', 'code', config, self.root)
        self.assertNotIn('--no-session-persistence', prepared)
        prepared, _, _ = self.run.prepare(['claude', '-p', 'x', '--continue'], 'claude', 'code', config, self.root)
        self.assertIn('--continue', prepared)

    def test_pi_no_session_is_a_real_flag_once_even_when_the_text_looks_like_it(self):
        # A value-taking option may swallow a literal '--no-session' as its argument.
        prepared, _, _ = self.run.prepare(['pi', '--append-system-prompt', '--no-session', '-p', 'task'], 'pi', 'code', {}, self.root)
        self.assertEqual(prepared[prepared.index('--no-session') - 1] in ('--append-system-prompt',), False)
        flag = prepared.index('--no-session')
        self.assertLess(flag, len(prepared) - prepared[::-1].index('--append-system-prompt') - 1)
        self.assertEqual(prepared[-4:], ['--append-system-prompt', '--no-session', '-p', 'task'])
        prepared, _, _ = self.run.prepare(['pi', '--no-session', '--no-session', '-p', 'task'], 'pi', 'code', {}, self.root)
        self.assertEqual(prepared.count('--no-session'), 1)

    def test_pi_fresh_sessions_reject_continuations_and_preserve_recipe(self):
        for flag in ('--continue', '-c', '--resume', '-r', '--session', '--fork', '--session=id', '--fork=id'):
            for args in ([flag, '-p', 'task'], ['-p', 'task', flag]):
                with self.subTest(args=args), self.assertRaisesRegex(ValueError, 'no se permite reanudar ni bifurcar'):
                    self.run.prepare(['pi', *args], 'pi', 'code', {}, self.root)
        for command in (['pi', '-p', 'task', '--model', 'chosen'],
                        ['pi', '--no-session', '-p', 'task', '--model', 'chosen']):
            prepared, _, _ = self.run.prepare(command, 'pi', 'code', {}, self.root)
            self.assertEqual(prepared.count('--no-session'), 1)
            self.assertEqual(prepared[-4:], ['-p', 'task', '--model', 'chosen'])
        config = {'context': {'fresh_worker_per_subtask': False}}
        prepared, _, _ = self.run.prepare(['pi', '-p', 'task', '-c'], 'pi', 'code', config, self.root)
        self.assertNotIn('--no-session', prepared)
        self.assertEqual(prepared[-3:], ['-p', 'task', '-c'])
        prepared, _, _ = self.run.prepare(CODEX, 'codex', 'code', {}, self.root)
        self.assertIn('model_reasoning_effort="high"', prepared)

    def test_wrapper_runs_the_child_with_the_ephemeral_flag_and_keeps_the_five_field_filter(self):
        argv_file = self.root / 'argv.json'
        fake = self.root / 'native'
        event = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(REPORT)}}
        fake.write_text('#!' + sys.executable + '\nimport json,sys\nopen(' + repr(str(argv_file))
                        + ',"w").write(json.dumps(sys.argv[1:]))\nprint(' + repr(json.dumps(event)) + ')\n')
        fake.chmod(0o700)
        out = io.StringIO()
        code = self.run.run([str(fake), 'exec', '-s', 'read-only', '-m', 'chosen', 'task'], 'codex', 'review', {},
                            self.root, output=out)
        argv = json.loads(argv_file.read_text())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), REPORT)
        self.assertIn('--ephemeral', argv)
        self.assertEqual(argv[-5:], ['-s', 'read-only', '-m', 'chosen', 'task'])

    def test_visible_recipes_carry_no_continuation_and_keep_controls(self):
        for agent in ('codex', 'claude'):
            row = {'agent': agent, 'model': 'chosen', 'effort': 'high'}
            for role in ('plan', 'code', 'review', 'simple'):
                for line in native_team.role_commands(role, row, ['codex-2'] if agent == 'codex' else ['claude']):
                    with self.subTest(agent=agent, role=role):
                        for text in ('resume', ' fork', '--resume', '--continue', '--fork-session'):
                            self.assertNotIn(text, line)
                        self.assertIn('chosen', line)
                        self.assertIn('context_run.py', line)


if __name__ == '__main__':
    unittest.main()
