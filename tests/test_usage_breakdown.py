import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/usage'))
import breakdown
import prices

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def stamp(days_ago=0):
    return (NOW - timedelta(days=days_ago)).isoformat().replace('+00:00', 'Z')


def claude_line(message_id, *, model='claude-opus-5-5', cwd='/home/u/Work/a', tools=(), days_ago=0,
                usage=(10, 20, 30, 40)):
    entry = {'type': 'assistant', 'timestamp': stamp(days_ago), 'message': {
        'id': message_id, 'model': model,
        'usage': {'input_tokens': usage[0], 'output_tokens': usage[1],
                  'cache_read_input_tokens': usage[2], 'cache_creation_input_tokens': usage[3]},
        'content': [{'type': 'tool_use', 'name': name} for name in tools] or [{'type': 'text', 'text': 'hi'}]}}
    if cwd is not None:
        entry['cwd'] = cwd
    return json.dumps(entry)


class ClaudeScanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def write(self, *lines, name='session.jsonl'):
        folder = self.root / '-home-u-Work-a'
        folder.mkdir(exist_ok=True)
        (folder / name).write_text('\n'.join(lines) + '\n')

    def scan(self, days=30):
        return breakdown.scan_claude(self.root, prices.DEFAULT_PRICES, days, NOW)

    def test_a_message_repeated_per_tool_call_is_counted_once(self):
        self.write(claude_line('m1', tools=['Bash']), claude_line('m1', tools=['Bash']))
        result = self.scan()
        self.assertEqual(result['byKind'], {'input': 10, 'output': 20, 'cacheRead': 30, 'cacheWrite': 40})

    def test_tokens_are_split_by_kind_project_and_model(self):
        self.write(claude_line('m1'), claude_line('m2', model='claude-sonnet-5-5', cwd='/home/u/Work/b', usage=(1, 2, 3, 4)))
        result = self.scan()
        self.assertEqual(result['byKind'], {'input': 11, 'output': 22, 'cacheRead': 33, 'cacheWrite': 44})
        self.assertEqual({row['model']: row['total'] for row in result['byModel']},
                         {'claude-opus-5-5': 100, 'claude-sonnet-5-5': 10})
        self.assertEqual({row['path']: row['total'] for row in result['byProject']},
                         {'/home/u/Work/a': 100, '/home/u/Work/b': 10})
        self.assertEqual(result['windowDays'], 30)

    def test_cost_is_priced_per_model_and_summed(self):
        self.write(claude_line('m1', usage=(1_000_000, 0, 0, 0)))
        result = self.scan()
        self.assertAlmostEqual(result['costUsd'], 4.00)
        self.assertAlmostEqual(result['byProject'][0]['costUsd'], 4.00)

    def test_tools_in_later_lines_of_the_same_message_are_attributed(self):
        # Claude writes each content block of one message as its own line, all with the same id and usage.
        text_first = claude_line('m1', usage=(100, 100, 50, 50))
        tool_later = claude_line('m1', tools=['Bash'], usage=(100, 100, 50, 50))
        self.write(text_first, tool_later)
        result = self.scan()
        self.assertEqual({row['name']: row['total'] for row in result['byTool']}, {'Bash': 300})
        self.assertEqual(result['byKind']['input'], 100)

    def test_the_largest_usage_of_a_repeated_message_wins(self):
        self.write(claude_line('m1', usage=(10, 5, 0, 0)), claude_line('m1', usage=(10, 20, 0, 0)))
        self.assertEqual(self.scan()['byKind'], {'input': 10, 'output': 20, 'cacheRead': 0, 'cacheWrite': 0})

    def test_tools_share_a_messages_tokens_equally(self):
        self.write(claude_line('m1', tools=['Bash', 'Read'], usage=(100, 100, 50, 50)), claude_line('m2', usage=(1, 1, 1, 1)))
        tools = {row['name']: row['total'] for row in self.scan()['byTool']}
        self.assertEqual(tools, {'Bash': 150, 'Read': 150, '(solo texto)': 4})

    def test_messages_outside_the_window_are_ignored(self):
        self.write(claude_line('old', days_ago=31), claude_line('new', days_ago=1))
        self.assertEqual(self.scan()['byKind']['input'], 10)

    def test_corrupt_and_truncated_lines_are_skipped(self):
        self.write(claude_line('m1'), '{"type":"assist', '', 'not json at all', claude_line('m2'))
        self.assertEqual(self.scan()['byKind']['input'], 20)

    def test_missing_cwd_goes_to_no_project(self):
        self.write(claude_line('m1', cwd=None))
        self.assertEqual(self.scan()['byProject'][0]['path'], '(sin proyecto)')

    def test_unpriced_model_has_null_cost_and_is_listed(self):
        self.write(claude_line('m1', usage=(1_000_000, 0, 0, 0)), claude_line('m2', model='<synthetic>'))
        result = self.scan()
        synthetic = next(row for row in result['byModel'] if row['model'] == '<synthetic>')
        self.assertIsNone(synthetic['costUsd'])
        self.assertEqual(result['unpricedModels'], ['<synthetic>'])
        self.assertAlmostEqual(result['costUsd'], 4.00)

    def test_all_unpriced_gives_null_total(self):
        self.write(claude_line('m1', model='<synthetic>'))
        self.assertIsNone(self.scan()['costUsd'])

    def test_top_eight_projects_and_tools(self):
        lines = [claude_line('m%d' % n, cwd='/p/%d' % n, tools=['T%d' % n], usage=(n + 1, 0, 0, 0)) for n in range(10)]
        self.write(*lines)
        result = self.scan()
        self.assertEqual(len(result['byProject']), 8)
        self.assertEqual(len(result['byTool']), 8)
        self.assertEqual([row['path'] for row in result['byProject']][:2], ['/p/9', '/p/8'])

    def test_message_without_usage_is_ignored(self):
        broken = json.dumps({'type': 'assistant', 'timestamp': stamp(), 'message': {'id': 'x', 'model': 'm', 'content': None}})
        self.write(broken, claude_line('m1'))
        self.assertEqual(self.scan()['byKind']['input'], 10)

    def test_no_messages_returns_none(self):
        self.assertIsNone(self.scan())
        self.write('{}', '[1,2]')
        self.assertIsNone(self.scan())


if __name__ == '__main__':
    unittest.main()
