import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/usage'))
import ai_team_usage

CLAUDE = '''
import os, json
AGENT_ID = "claude"
AGENT_NAME = "Claude Code"
def scan_pi_usage(age): return {"totalPrompts": 99}
def scan_opencode_usage(age): return {"totalPrompts": 99}
def main():
    print(json.dumps({"id": AGENT_ID, "dir": os.environ["CLAUDE_CONFIG_DIR"], "cache": os.environ["XDG_CACHE_HOME"],
                      "pi": scan_pi_usage(0), "opencode": scan_opencode_usage(0)}))
'''
CODEX = '''
import os, json
AGENT_ID = "codex"
def scan_pi_sessions(): raise AssertionError("pi must be off")
def scan_opencode_sessions(): raise AssertionError("opencode must be off")
def main():
    print(json.dumps({"id": AGENT_ID, "home": os.environ["CODEX_HOME"], "pi": scan_pi_sessions() , "oc": scan_opencode_sessions()}))
'''


class UsageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'bin').mkdir()
        (self.root / 'bin/omarchy-agent-usage-claude').write_text(CLAUDE)
        (self.root / 'bin/omarchy-agent-usage-codex').write_text(CODEX)
        patcher = patch.dict(os.environ, {'HOME': str(self.root / 'home'), 'OMARCHY_PATH': str(self.root),
                                          'XDG_STATE_HOME': str(self.root / 'state')})
        patcher.start(); self.addCleanup(patcher.stop)
        (self.root / 'home').mkdir()

    def test_claude_2_uses_its_own_directory_and_cache_without_pi_or_opencode(self):
        (self.root / 'home/.claude-2').mkdir()
        record = ai_team_usage.collect('claude-2')
        self.assertEqual((record['id'], record['name']), ('claude-2', 'Claude Code · 2'))
        self.assertEqual(record['dir'], str(self.root / 'home/.claude-2'))
        self.assertTrue(record['cache'].endswith('usage/claude-2'))
        self.assertEqual((record['pi'], record['opencode']), (None, None))

    def test_codex_2_has_no_pi_or_opencode_and_the_environment_is_restored(self):
        (self.root / 'home/.codex-2').mkdir()
        with patch.dict(os.environ, {'CODEX_HOME': '/keep'}):
            record = ai_team_usage.collect('codex-2')
            self.assertEqual(os.environ['CODEX_HOME'], '/keep')
        self.assertEqual((record['id'], record['home']), ('codex-2', str(self.root / 'home/.codex-2')))
        self.assertEqual(record['name'], 'Codex · 2')

    def test_missing_profile_writes_nothing_and_removes_a_stale_record(self):
        state = ai_team_usage.state_dir()
        state.mkdir(parents=True)
        (state / 'claude-2.json').write_text('{}')
        self.assertEqual(ai_team_usage.main([]), 0)
        self.assertEqual(list(state.glob('*.json')), [])

    def test_main_writes_one_record_per_existing_account_and_keeps_going_after_a_failure(self):
        (self.root / 'home/.claude-2').mkdir(); (self.root / 'home/.codex-2').mkdir()
        (self.root / 'bin/omarchy-agent-usage-claude').write_text('raise RuntimeError("boom")')
        self.assertEqual(ai_team_usage.main([]), 1)
        state = ai_team_usage.state_dir()
        self.assertEqual(json.loads((state / 'codex-2.json').read_text())['id'], 'codex-2')
        self.assertFalse((state / 'claude-2.json').exists())

    def test_cache_is_private_and_a_failed_write_leaves_no_temporary_file(self):
        (self.root / 'home/.claude-2').mkdir()
        ai_team_usage.collect('claude-2')
        cache = self.root / 'home/.cache/omarchy-ai-team/usage'
        self.assertEqual((cache.stat().st_mode & 0o777, (cache / 'claude-2').stat().st_mode & 0o777), (0o700, 0o700))
        state = self.root / 'out'
        with patch.object(ai_team_usage.os, 'replace', side_effect=OSError):
            with self.assertRaises(OSError):
                ai_team_usage.write('claude-2', {'id': 'claude-2'}, state)
        self.assertEqual(list(state.iterdir()), [])

    def transcript(self, folder, name='a.jsonl'):
        directory = self.root / 'home' / folder / 'projects' / 'p'
        directory.mkdir(parents=True)
        line = {'type': 'assistant', 'cwd': '/w/a', 'timestamp': datetime.now(timezone.utc).isoformat(), 'message': {
            'id': 'm1', 'model': 'claude-opus-5-5', 'usage': {'input_tokens': 1_000_000, 'output_tokens': 0,
                                                              'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0},
            'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Bash'}]}}
        (directory / name).write_text(json.dumps(line) + '\n')

    def test_breakdowns_are_written_per_account_with_schema_id_and_cost(self):
        self.transcript('.claude-2')
        ai_team_usage.write_breakdowns()
        written = json.loads((ai_team_usage.breakdown_dir() / 'claude-2.json').read_text())
        self.assertEqual((written['schemaVersion'], written['id'], written['windowDays']), (1, 'claude-2', 30))
        self.assertTrue(written['generatedAt'])
        self.assertAlmostEqual(written['costUsd'], 4.0)
        self.assertEqual(written['byTool'][0]['name'], 'Bash')
        self.assertFalse((ai_team_usage.breakdown_dir() / 'claude.json').exists())

    def test_missing_profile_or_empty_scan_removes_a_stale_breakdown(self):
        stale = ai_team_usage.breakdown_dir()
        stale.mkdir(parents=True)
        (stale / 'claude.json').write_text('{}')
        (self.root / 'home/.codex-2/sessions').mkdir(parents=True)
        (stale / 'codex-2.json').write_text('{}')
        ai_team_usage.write_breakdowns()
        self.assertEqual(list(stale.glob('*.json')), [])

    def test_main_writes_breakdowns_even_if_a_collector_fails(self):
        self.transcript('.claude-2')
        (self.root / 'home/.claude-2').mkdir(exist_ok=True)
        (self.root / 'bin/omarchy-agent-usage-claude').write_text('raise RuntimeError("boom")')
        self.assertEqual(ai_team_usage.main([]), 1)
        self.assertTrue((ai_team_usage.breakdown_dir() / 'claude-2.json').exists())

    def test_breakdown_files_are_private(self):
        self.transcript('.claude-2')
        ai_team_usage.write_breakdowns()
        folder = ai_team_usage.breakdown_dir()
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual((folder / 'claude-2.json').stat().st_mode & 0o777, 0o600)

    def test_codex_archived_sessions_are_part_of_the_breakdown(self):
        archived = self.root / 'home/.codex-2/archived_sessions'
        archived.mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        usage = {'input_tokens': 500, 'cached_input_tokens': 0, 'cache_write_input_tokens': 0, 'output_tokens': 0}
        line = {'timestamp': now, 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'last_token_usage': usage, 'total_token_usage': usage}}}
        (archived / 'old.jsonl').write_text(json.dumps(line) + '\n')
        ai_team_usage.write_breakdowns()
        written = json.loads((ai_team_usage.breakdown_dir() / 'codex-2.json').read_text())
        self.assertEqual(written['byKind']['input'], 500)

    def test_a_failed_scan_keeps_the_last_good_breakdown(self):
        self.transcript('.claude-2')
        ai_team_usage.write_breakdowns()
        target = ai_team_usage.breakdown_dir() / 'claude-2.json'
        good = target.read_text()
        transcript = next((self.root / 'home/.claude-2').rglob('a.jsonl'))
        transcript.chmod(0)
        self.addCleanup(transcript.chmod, 0o600)
        if os.access(transcript, os.R_OK):
            self.skipTest('running with privileges that ignore file modes')
        self.assertEqual(ai_team_usage.write_breakdowns(), ['claude-2'])
        self.assertEqual(target.read_text(), good)

    def test_prices_file_is_created_once_and_user_edits_survive(self):
        ai_team_usage.write_breakdowns()
        path = ai_team_usage.prices_path()
        self.assertTrue(path.exists())
        edited = json.loads(path.read_text())
        edited['gpt-6.1-sol'] = {'input': 1.0, 'output': 2.0, 'cacheRead': 0.1, 'cacheWrite': 1.0}
        path.write_text(json.dumps(edited))
        ai_team_usage.write_breakdowns()
        self.assertEqual(json.loads(path.read_text())['gpt-6.1-sol']['input'], 1.0)


if __name__ == '__main__':
    unittest.main()
