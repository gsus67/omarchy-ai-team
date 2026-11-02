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

    def test_the_chief_is_the_rest_after_the_workers(self):
        self.breakdown('claude', 1000)
        role_usage.record('review', self.counters(300, 100))
        role_usage.record('code', self.counters(100, 0))
        result = role_usage.summary(AGENTS)
        shares = {row['role']: row['tokens'] for row in result['roles']}
        self.assertEqual(shares, {'jefe': '500', 'plan': '0', 'code': '100', 'review': '400', 'simple': '0'})
        self.assertEqual(result['total'], '1.0K')
        self.assertEqual(result['since'], datetime.now(timezone.utc).date().isoformat())

    def test_unknown_counters_and_the_chief_role_are_not_recorded(self):
        role_usage.record('review', {'total_input': None, 'output': 5})
        role_usage.record('jefe', self.counters())
        self.assertFalse(role_usage.ledger().exists())

    def test_old_and_malformed_lines_are_ignored_and_the_chief_never_goes_negative(self):
        self.breakdown('claude', 50)
        old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        path = role_usage.ledger(); path.parent.mkdir(parents=True)
        path.write_text('\n'.join([json.dumps({'at': old, 'role': 'code', 'tokens': 999}), 'not json',
                                   json.dumps({'at': 'x', 'role': 'code', 'tokens': 1}),
                                   json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'role': 'code', 'tokens': 80})]))
        roles = {row['role']: row['tokens'] for row in role_usage.summary(AGENTS)['roles']}
        self.assertEqual((roles['code'], roles['jefe']), ('80', '0'))

    def test_the_ledger_is_private(self):
        role_usage.record('plan', self.counters())
        self.assertEqual(role_usage.ledger().stat().st_mode & 0o777, 0o600)
        self.assertEqual(role_usage.ledger().parent.stat().st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
