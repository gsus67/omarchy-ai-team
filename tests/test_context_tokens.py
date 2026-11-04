"""Per-task token counters from native events; fixtures are synthetic and nothing here calls a model."""
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

REPORT = {'status': 'done', 'references': 'a.py:1', 'tests': 'ok', 'error': '', 'pending': ''}
CODEX_TURN = {'type': 'turn.completed', 'usage': {'input_tokens': 10000, 'cached_input_tokens': 9000, 'output_tokens': 50}}
CLAUDE_RESULT = {'type': 'result', 'usage': {'input_tokens': 1000, 'cache_read_input_tokens': 9000,
                                             'cache_creation_input_tokens': 500, 'output_tokens': 70}}


def snapshot(agent='codex', schema=1, **counters):
    base = {'total_input': 0, 'cached_read': 0, 'new_input': 0, 'cache_creation': 0, 'output': 0}
    return {'schema': schema, 'agent': agent, 'turns': 0, 'counters': {**base, **counters}}


class TokenUsageTests(unittest.TestCase):
    def setUp(self):
        self.run = importlib.import_module('context_run')

    def test_codex_input_includes_cached_so_new_input_is_the_difference(self):
        usage = self.run.token_usage('codex', CODEX_TURN)
        self.assertEqual((usage['total_input'], usage['cached_read'], usage['new_input']), (10000, 9000, 1000))
        self.assertIsNone(usage['cache_creation'])
        self.assertEqual(usage['output'], 50)
        self.assertEqual(self.run.token_usage('codex-2', CODEX_TURN), usage)

    def test_claude_total_adds_new_cache_read_and_creation(self):
        usage = self.run.token_usage('claude', CLAUDE_RESULT)
        self.assertEqual((usage['new_input'], usage['cached_read'], usage['cache_creation'], usage['total_input']),
                         (1000, 9000, 500, 10500))
        self.assertEqual(usage['output'], 70)

    def test_other_events_agents_and_pi_have_no_counters(self):
        for agent, event in [('codex', {'type': 'item.completed', 'usage': CODEX_TURN['usage']}),
                             ('claude', {'type': 'assistant', 'usage': CLAUDE_RESULT['usage']}),
                             ('codex', {'type': 'turn.completed'}), ('pi', CODEX_TURN), ('claude', 'text'), ('codex', None)]:
            with self.subTest(agent=agent, event=event):
                self.assertIsNone(self.run.token_usage(agent, event))

    def test_absent_or_invalid_counters_are_unknown_never_zero(self):
        usage = self.run.token_usage('codex', {'type': 'turn.completed', 'usage': {'input_tokens': 10000}})
        self.assertEqual((usage['total_input'], usage['cached_read'], usage['new_input']), (10000, None, None))
        for bad in (True, -1, '9000', 1.5, None):
            usage = self.run.token_usage('claude', {'type': 'result', 'usage': {
                'input_tokens': 1000, 'cache_read_input_tokens': bad, 'cache_creation_input_tokens': 500}})
            with self.subTest(bad=bad):
                self.assertIsNone(usage['cached_read'])
                self.assertIsNone(usage['total_input'])
                self.assertEqual(usage['new_input'], 1000)

    def test_delta_subtracts_and_keeps_unknown_reset_and_incompatible_distinct(self):
        delta = self.run.token_delta(snapshot(total_input=100, cached_read=90, new_input=10, output=5),
                                     snapshot(total_input=10100, cached_read=9090, new_input=1010, output=55))
        self.assertEqual((delta['total_input'], delta['cached_read'], delta['new_input'], delta['output']),
                         (10000, 9000, 1000, 50))
        self.assertFalse(delta['reset'])
        unknown = self.run.token_delta(snapshot(cache_creation=None), snapshot(cache_creation=None, total_input=5))
        self.assertIsNone(unknown['cache_creation'])
        self.assertIn('cache_creation', unknown['unknown'])
        reset = self.run.token_delta(snapshot(total_input=500), snapshot(total_input=120))
        self.assertTrue(reset['reset'])
        self.assertEqual(reset['total_input'], 120)
        for before, after in [(snapshot('codex'), snapshot('claude')), (snapshot(schema=1), snapshot(schema=2)),
                              (None, snapshot()), ({'counters': 'x'}, snapshot()), (snapshot(), [])]:
            with self.subTest(before=before, after=after), self.assertRaises(ValueError):
                self.run.token_delta(before, after)


class TaskSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.run = importlib.import_module('context_run')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'state')})
        patcher.start()
        self.addCleanup(patcher.stop)

    def child(self, events, private=''):
        path = self.root / 'native'
        lines = ''.join('print(' + repr(json.dumps(event)) + ')\n' for event in events)
        path.write_text('#!' + sys.executable + '\nimport sys\nprint(' + repr(private) + ',file=sys.stderr)\n' + lines)
        path.chmod(0o700)
        return str(path)

    def folders(self):
        return sorted((self.root / 'state/omarchy-ai-team/context-usage').glob('*'))

    def test_two_turns_are_summed_once_and_snapshots_are_private_numeric_files(self):
        final = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(REPORT)}}
        noise = {'type': 'item.completed', 'usage': CODEX_TURN['usage'], 'item': {'type': 'command_execution', 'output': 'PRIVATE TOOL'}}
        fake = self.child([CODEX_TURN, noise, CODEX_TURN, final], private='PRIVATE STDERR')
        out = io.StringIO()
        code = self.run.run([fake, 'exec', 'PRIVATE PROMPT'], 'codex', 'code', {}, self.root, output=out)
        self.assertEqual((code, json.loads(out.getvalue())), (0, REPORT))
        (folder,) = self.folders()
        before, after = (json.loads((folder / name).read_text()) for name in ('before.json', 'after.json'))
        self.assertEqual(before['counters']['total_input'], 0)
        self.assertEqual((after['counters']['total_input'], after['counters']['cached_read'], after['turns']), (20000, 18000, 2))
        self.assertEqual(self.run.token_delta(before, after)['new_input'], 2000)
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        for name in ('before.json', 'after.json'):
            self.assertEqual(stat.S_IMODE((folder / name).stat().st_mode), 0o600)
            self.assertNotIn('PRIVATE', (folder / name).read_text())
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ['after.json', 'before.json'])
        (entry,) = (json.loads(line) for line in (self.root / 'state/omarchy-ai-team/role-usage.jsonl').read_text().splitlines())
        self.assertEqual((entry['role'], entry['tokens'], entry['cache']),
                         ('code', 2000 + after['counters']['output'], 18000))
        self.assertNotIn('PRIVATE', json.dumps(entry))

    def ledger(self):
        path = self.root / 'state/omarchy-ai-team/role-usage.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_secondary_profile_is_recorded_as_secondary_account(self):
        final = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(REPORT)}}
        cases = [('claude', 'claude', 'claude'), ('claude', 'claude-2', 'claude-2'), ('claude', None, 'claude'),
                 ('codex', 'codex', 'codex'), ('codex', 'codex-2', 'codex-2'), ('codex', None, 'codex')]
        for index, (agent, profile, expected) in enumerate(cases, 1):
            with self.subTest(agent=agent, profile=profile):
                if agent == 'claude':
                    fake, command = self.child([{**CLAUDE_RESULT, 'structured_output': REPORT}]), ['-p', 'task']
                else:
                    fake, command = self.child([CODEX_TURN, final]), ['exec', 'task']
                code = self.run.run([fake, *command], agent, 'review', {}, self.root, output=io.StringIO(), profile=profile)
                self.assertEqual(code, 0)
                entries = self.ledger()
                self.assertEqual(len(entries), index)
                self.assertEqual((entries[-1]['agent'], entries[-1]['role']), (expected, 'review'))
                self.assertIsInstance(entries[-1]['tokens'], int)
                self.assertGreater(entries[-1]['tokens'], 0)

    def test_profile_fix_does_not_rewrite_historical_ledger(self):
        state = self.root / 'state/omarchy-ai-team'
        state.mkdir(parents=True, mode=0o700)
        old = json.dumps({'at': '2026-01-01T00:00:00+00:00', 'role': 'review', 'agent': 'claude', 'tokens': 7}) + '\n'
        (state / 'role-usage.jsonl').write_text(old)
        (state / 'role-usage.jsonl').chmod(0o600)
        fake = self.child([{**CLAUDE_RESULT, 'structured_output': REPORT}])
        self.run.run([fake, '-p', 'task'], 'claude', 'review', {}, self.root, output=io.StringIO(), profile='claude-2')
        text = (state / 'role-usage.jsonl').read_text()
        self.assertTrue(text.startswith(old))
        entries = self.ledger()
        self.assertEqual([entry['agent'] for entry in entries], ['claude', 'claude-2'])
        self.assertEqual(entries[0]['tokens'], 7)

    def test_claude_result_counters_and_missing_counters_stay_unknown(self):
        fake = self.child([{**CLAUDE_RESULT, 'structured_output': REPORT}])
        self.run.run([fake, '-p', 'task'], 'claude', 'code', {}, self.root, output=io.StringIO())
        after = json.loads((self.folders()[0] / 'after.json').read_text())
        self.assertEqual(after['counters']['total_input'], 10500)
        fake = self.child([{'type': 'result', 'structured_output': REPORT}])
        self.run.run([fake, '-p', 'task'], 'claude', 'code', {}, self.root, output=io.StringIO())
        newest = max(self.folders(), key=lambda path: (path / 'after.json').stat().st_mtime_ns)
        self.assertIsNone(json.loads((newest / 'after.json').read_text())['counters']['total_input'])

    def test_measurement_off_or_policy_off_writes_nothing(self):
        fake = self.child([CODEX_TURN, {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(REPORT)}}])
        self.run.run([fake, 'exec', 'task'], 'codex', 'code', {'context': {'measure_task_tokens': False}}, self.root,
                     output=io.StringIO())
        self.assertFalse((self.root / 'state').exists())
        with patch.object(self.run.os, 'execvpe'):
            self.run.run([fake, 'exec', 'task'], 'codex', 'code', {'context': {'enabled': False}}, self.root)
        self.assertFalse((self.root / 'state').exists())


class TaskTokensCliTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('session_usage_cli', ROOT / 'tools/session_usage.py')
        self.usage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.usage)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def invoke(self, *argv):
        out = io.StringIO()
        with patch.object(sys, 'argv', ['session_usage.py', *argv]), patch('sys.stdout', out), \
                patch.object(self.usage.harness, 'mise_inventory', side_effect=AssertionError('quota path used')), \
                patch.object(self.usage.native_plugins, 'codex_request', side_effect=AssertionError('quota path used')):
            code = self.usage.main()
        return code, json.loads(out.getvalue())

    def test_task_tokens_mode_reads_two_snapshots_without_quota_auth_or_inference(self):
        before, after = self.root / 'before.json', self.root / 'after.json'
        before.write_text(json.dumps(snapshot()))
        after.write_text(json.dumps(snapshot(total_input=10000, cached_read=9000, new_input=1000)))
        code, reply = self.invoke('--task-tokens', '--before', str(before), '--after', str(after))
        self.assertEqual(code, 0)
        self.assertIs(reply['ok'], True)
        self.assertEqual((reply['tokens']['total_input'], reply['tokens']['cached_read'], reply['tokens']['new_input']),
                         (10000, 9000, 1000))
        self.assertIsNone(reply['cost'])

    def test_task_tokens_errors_are_literal_and_nonzero(self):
        bad = self.root / 'bad.json'
        bad.write_text('{not json')
        for argv in [('--task-tokens', '--before', str(bad), '--after', str(bad)),
                     ('--task-tokens', '--before', str(self.root / 'missing'), '--after', str(bad))]:
            code, reply = self.invoke(*argv)
            self.assertEqual(code, 1)
            self.assertIs(reply['ok'], False)
            self.assertTrue(reply['error'])


if __name__ == '__main__':
    unittest.main()
