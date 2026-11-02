import json
import os
import sys
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import usage_summary


class UsageSummaryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.records = self.root / 'omarchy/agents/usage'
        self.records.mkdir(parents=True)
        patcher = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root)}); patcher.start(); self.addCleanup(patcher.stop)

    def record(self, agent, *limits):
        (self.records / (agent + '.json')).write_text(json.dumps({'id': agent, 'limits': [
            {'label': label, 'percent': percent} for label, percent in limits]}))

    def agents(self, *ids, enabled=True):
        return [{'id': agent, 'name': agent.title(), 'enabled': enabled} for agent in ids]

    def test_five_hour_and_weekly_percent_of_each_account(self):
        self.record('claude', ('Session (5-hour)', 0.01), ('Weekly (7-day)', 0.98))
        self.record('codex-2', ('5h window', 0.08), ('Weekly (7-day)', 0.344))
        rows = usage_summary.rows(self.agents('claude', 'codex-2'))
        self.assertEqual(rows, [{'id': 'claude', 'name': 'Claude', 'five': 1, 'weekly': 98},
                                {'id': 'codex-2', 'name': 'Codex 2', 'five': 8, 'weekly': 34}])

    def test_model_scoped_limits_do_not_replace_the_account_windows(self):
        self.record('claude', ('Opus 5 (1M context)', 0.7), ('Session (5-hour)', 0.2), ('Weekly (7-day)', 0.5), ('Weekly (7-day) Sonnet', 0.9))
        row = usage_summary.rows(self.agents('claude'))[0]
        self.assertEqual((row['five'], row['weekly']), (20, 50))

    def test_a_missing_window_is_null_not_zero(self):
        self.record('claude', ('Weekly (7-day)', 0.5))
        self.assertEqual(usage_summary.rows(self.agents('claude'))[0]['five'], None)

    def test_only_selected_accounts_with_a_readable_record_appear(self):
        self.record('claude', ('Session (5-hour)', 0.1))
        (self.records / 'codex.json').write_text('{broken')
        self.assertEqual([row['id'] for row in usage_summary.rows(self.agents('claude', 'codex', 'claude-2', 'pi'))], ['claude'])
        self.assertEqual(usage_summary.rows(self.agents('claude', enabled=False)), [])

    def test_a_record_without_limits_or_with_bad_percentages_is_skipped(self):
        (self.records / 'claude.json').write_text(json.dumps({'id': 'claude', 'limits': []}))
        self.record('codex', ('5h window', 'lots'), ('Weekly (7-day)', None))
        self.assertEqual(usage_summary.rows(self.agents('claude', 'codex')), [])

    def test_a_window_that_already_reset_counts_as_zero(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        (self.records / 'claude.json').write_text(json.dumps({'id': 'claude', 'limits': [
            {'label': 'Session (5-hour)', 'percent': 1.0, 'resetsAt': past},
            {'label': 'Weekly (7-day)', 'percent': 0.5, 'resetsAt': future}]}))
        row = usage_summary.rows(self.agents('claude'))[0]
        self.assertEqual((row['five'], row['weekly']), (0, 50))

    def test_percentages_are_fractions_between_zero_and_one(self):
        self.record('claude', ('Session (5-hour)', 1.5), ('Weekly (7-day)', 10))
        self.assertEqual(usage_summary.rows(self.agents('claude')), [])
        self.record('codex', ('5h window', 1), ('Weekly (7-day)', 0))
        row = usage_summary.rows(self.agents('codex'))[0]
        self.assertEqual((row['five'], row['weekly']), (100, 0))

    def test_only_regular_files_are_read_and_links_are_ignored(self):
        outside = self.root / 'outside.json'
        outside.write_text(json.dumps({'limits': [{'label': 'Session (5-hour)', 'percent': 0.5}]}))
        (self.records / 'claude.json').symlink_to(outside)
        os.mkfifo(self.records / 'codex.json')
        self.assertEqual(usage_summary.rows(self.agents('claude', 'codex')), [])

    def test_a_deeply_nested_record_is_skipped_instead_of_aborting(self):
        (self.records / 'claude.json').write_text('{"limits": ' + '[' * 200000 + ']' * 200000 + '}')
        self.assertEqual(usage_summary.rows(self.agents('claude')), [])

    def test_nothing_is_read_outside_the_state_directory(self):
        self.record('claude', ('Session (5-hour)', 0.1))
        (self.root / 'omarchy/agents/usage' / '..' / 'claude.json').resolve().write_text('{}')
        self.assertEqual(usage_summary.rows([{'id': '../claude', 'name': 'x', 'enabled': True}]), [])


if __name__ == '__main__':
    unittest.main()
