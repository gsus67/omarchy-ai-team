"""Every subtask is a new native process: no resume/continue and no persisted session."""
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import native_team

REPORT = {'status': 'done', 'references': 'backend/a.py:1', 'tests': '1 passed', 'error': '', 'pending': ''}
CODEX = ['codex', 'exec', '-C', '/work', '-s', 'read-only', '-m', 'chosen',
         '-c', 'model_reasoning_effort="high"', 'task']
CLAUDE = ['claude', '-p', 'task', '--model', 'chosen', '--effort', 'high', '--permission-mode', 'plan']


class FreshWorkerTests(unittest.TestCase):
    def setUp(self):
        self.run = importlib.import_module('context_run')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

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
        for token in ('-s', 'read-only', '-m', 'chosen', '-C', '/work', 'model_reasoning_effort="high"'):
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
                    ['codex', 'exec', '--', 'fork']]
        for command in accepted:
            with self.subTest(command=command):
                prepared, _, _ = self.run.prepare(command, 'codex', 'code', {}, self.root)
                self.assertEqual(prepared.count('--ephemeral'), 1)
                self.assertEqual(prepared[prepared.index('--ephemeral') - 1], 'exec')

    def test_setting_off_keeps_the_previous_recipe_and_allows_continuation(self):
        config = {'context': {'fresh_worker_per_subtask': False}}
        prepared, _, _ = self.run.prepare(CODEX, 'codex', 'code', config, self.root)
        self.assertNotIn('--ephemeral', prepared)
        prepared, _, _ = self.run.prepare(CLAUDE, 'claude', 'code', config, self.root)
        self.assertNotIn('--no-session-persistence', prepared)
        prepared, _, _ = self.run.prepare(['claude', '-p', 'x', '--continue'], 'claude', 'code', config, self.root)
        self.assertIn('--continue', prepared)

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
