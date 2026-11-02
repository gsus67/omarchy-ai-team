import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import role_usage

AGENTS = [{'id': 'claude', 'enabled': True}, {'id': 'codex', 'enabled': False}]


class RoleUsageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patcher = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root)}); patcher.start(); self.addCleanup(patcher.stop)

    def breakdown(self, agent, tokens):
        folder = self.root / 'omarchy/agents/breakdown'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (agent + '.json')).write_text(json.dumps({
            'schemaVersion': 1, 'byKind': {'input': tokens, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0},
            'byProject': [], 'byTool': [], 'costUsd': None, 'unpricedModels': []}))

    def counters(self, inputs=100, output=20):
        return {'total_input': inputs, 'cached_read': 0, 'new_input': inputs, 'cache_creation': 0, 'output': output}

    def test_without_a_breakdown_there_is_no_total(self):
        self.assertIsNone(role_usage.summary(AGENTS))

    def test_the_total_is_the_chief_plus_the_workers(self):
        self.breakdown('claude', 1000)
        role_usage.record('review', 'claude', self.counters(300, 100))
        role_usage.record('code', 'claude', self.counters(100, 0))
        result = role_usage.summary(AGENTS)
        shares = {row['role']: row['tokens'] for row in result['roles']}
        self.assertEqual(shares, {'jefe': '1.0K', 'plan': '0', 'code': '100', 'review': '400', 'simple': '0'})
        self.assertEqual(result['total'], '1.5K')
        self.assertEqual(result['since'], datetime.now(timezone.utc).date().isoformat())

    def test_unknown_counters_and_the_chief_role_are_not_recorded(self):
        role_usage.record('review', 'claude', {'total_input': None, 'output': 5})
        role_usage.record('jefe', 'claude', self.counters())
        self.assertFalse(role_usage.ledger().exists())

    def test_old_malformed_and_other_account_lines_are_ignored(self):
        self.breakdown('claude', 50)
        now = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        path = role_usage.ledger(); path.parent.mkdir(parents=True)
        path.write_text('\n'.join([json.dumps({'at': old, 'role': 'code', 'agent': 'claude', 'tokens': 999}), 'not json', '[]',
                                   json.dumps({'at': now}), json.dumps({'at': 'x', 'role': 'code', 'agent': 'claude', 'tokens': 1}),
                                   json.dumps({'at': now, 'role': 'code', 'agent': 'codex', 'tokens': 700}),
                                   json.dumps({'at': now, 'role': 'code', 'agent': 'claude', 'tokens': 80})]))
        roles = {row['role']: row['tokens'] for row in role_usage.summary(AGENTS)['roles']}
        self.assertEqual((roles['code'], roles['jefe']), ('80', '50'))

    def test_a_fifo_in_place_of_the_ledger_never_blocks(self):
        self.breakdown('claude', 50)
        path = role_usage.ledger(); path.parent.mkdir(parents=True)
        os.mkfifo(path)
        self.assertEqual(role_usage.summary(AGENTS)['roles'][0]['tokens'], '50')
        with self.assertRaises(OSError):  # no reader: refused at once, not blocked
            role_usage.record('plan', 'claude', self.counters())

    def test_the_ledger_is_private(self):
        role_usage.record('plan', 'claude', self.counters())
        role_usage.ledger().chmod(0o666)
        role_usage.record('plan', 'claude', self.counters())
        self.assertEqual(role_usage.ledger().stat().st_mode & 0o777, 0o600)
        self.assertEqual(role_usage.ledger().parent.stat().st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
