import json
import os
import sys
import tempfile
import unittest
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


if __name__ == '__main__':
    unittest.main()
