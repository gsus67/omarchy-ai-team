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
        self.assertEqual([{key: row[key] for key in ('id', 'name', 'five', 'weekly')} for row in rows],
                         [{'id': 'claude', 'name': 'Claude', 'five': 1, 'weekly': 98},
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

    def test_an_account_whose_limits_could_not_be_read_stays_listed_with_empty_bars(self):
        (self.records / 'claude.json').write_text(json.dumps({'id': 'claude', 'limits': []}))
        self.record('codex', ('5h window', 'lots'), ('Weekly (7-day)', None))
        rows = usage_summary.rows(self.agents('claude', 'codex'))
        self.assertEqual([(row['id'], row['five'], row['weekly']) for row in rows], [('claude', None, None), ('codex', None, None)])

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
        row = usage_summary.rows(self.agents('claude'))[0]
        self.assertEqual((row['five'], row['weekly']), (None, None))  # out of range is unknown, never clipped
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

class DetailTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.usage = self.root / 'omarchy/agents/usage'; self.usage.mkdir(parents=True)
        self.breakdown = self.root / 'omarchy/agents/breakdown'; self.breakdown.mkdir(parents=True)
        patcher = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root), 'HOME': '/home/u'}); patcher.start(); self.addCleanup(patcher.stop)

    def row(self, record, breakdown=None):
        (self.usage / 'claude.json').write_text(json.dumps(record))
        if breakdown is not None:
            (self.breakdown / 'claude.json').write_text(json.dumps(breakdown))
        return usage_summary.rows([{'id': 'claude', 'name': 'Claude', 'enabled': True}])[0]

    def test_duration_and_token_text(self):
        self.assertEqual(usage_summary.duration_text(7680), '2 h 8 min')
        self.assertEqual(usage_summary.duration_text(3 * 86400 + 20 * 3600 + 120), '3 d 20 h')
        self.assertEqual(usage_summary.duration_text(90), '1 min')
        self.assertEqual(usage_summary.duration_text(20), '<1 min')
        self.assertEqual([usage_summary.tokens_text(n) for n in (999, 1500, 311_000_000, 1_500_000_000)], ['999', '1.5K', '311.0M', '1.5B'])
        self.assertEqual([usage_summary.money_text(v) for v in (None, 0, 0.004, 12.4)], ['—', '$0.00', '<$0.01', '$12.40'])

    def test_projects_are_shortened_for_a_narrow_screen(self):
        self.assertEqual(usage_summary.short_path('/home/u/Work/omarchy-ai-team'), '~/Work/omarchy-ai-team')
        self.assertEqual(usage_summary.short_path('/home/u/a/b/c/d'), '…/c/d')
        self.assertEqual(usage_summary.short_path('(sin proyecto)'), '(sin proyecto)')

    def test_windows_carry_a_label_the_percent_and_the_time_left(self):
        soon = (datetime.now(timezone.utc) + timedelta(hours=2, minutes=8, seconds=30)).isoformat()
        row = self.row({'tierLabel': 'Pro', 'limits': [
            {'label': 'Session (5-hour)', 'percent': 0.2, 'resetsAt': soon},
            {'label': 'Weekly (7-day)', 'percent': 0.5},
            {'label': 'Opus 5 (1M context)', 'percent': 0.7}]})
        self.assertEqual(row['tier'], 'Pro')
        self.assertEqual([(w['label'], w['percent']) for w in row['windows']], [('5 h', 20), ('Semanal', 50), ('Opus 5 (1M context)', 70)])
        self.assertEqual(row['windows'][0]['reset'], 'Reinicia en 2 h 8 min')
        self.assertEqual(row['windows'][1]['reset'], '')

    def test_models_are_the_four_heaviest_with_formatted_tokens(self):
        usage = {name: {'inputTokens': n, 'outputTokens': 0, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0}
                 for name, n in (('a', 5), ('b', 4_000_000), ('c', 3), ('d', 2), ('e', 1_000))}
        row = self.row({'limits': [{'label': 'Weekly (7-day)', 'percent': 0.1}], 'modelUsage': usage})
        self.assertEqual([m['name'] for m in row['models']], ['b', 'e', 'a', 'c'])
        self.assertEqual(row['models'][0]['tokens'], '4.0M')

    def test_where_tokens_go_comes_from_a_valid_breakdown(self):
        good = {'schemaVersion': 1, 'byKind': {'input': 10, 'output': 2_000, 'cacheRead': 3_000_000, 'cacheWrite': 4},
                'byProject': [{'path': '/home/u/Work/a', 'total': 5_000, 'costUsd': 1.5}, {'path': '(sin proyecto)', 'total': 1, 'costUsd': None}],
                'byTool': [{'name': 'Bash', 'total': 9_000, 'costUsd': 0.004}, {'name': '(solo texto)', 'total': 10, 'costUsd': None}],
                'byModel': [], 'costUsd': 553.917, 'unpricedModels': ['gpt-6.1-sol']}
        row = self.row({'limits': [{'label': 'Weekly (7-day)', 'percent': 0.1}]}, good)
        spend = row['spend']
        self.assertEqual(spend['cost'], '$553.92')
        self.assertEqual([k['tokens'] for k in spend['kinds']], ['10', '2.0K', '3.0M', '4'])
        self.assertEqual([k['name'] for k in spend['kinds']], ['Entrada', 'Salida', 'Caché leída', 'Caché escrita'])
        self.assertEqual(spend['projects'][0], {'name': '~/Work/a', 'tokens': '5.0K', 'cost': '$1.50'})
        self.assertEqual(spend['projects'][1]['cost'], '—')
        self.assertEqual(spend['tools'][0], {'name': 'Bash', 'tokens': '9.0K', 'cost': '<$0.01'})
        self.assertEqual(spend['unpriced'], 'gpt-6.1-sol')

    def test_a_breakdown_with_non_finite_or_absurd_numbers_is_skipped_not_fatal(self):
        base = {'schemaVersion': 1, 'byKind': {'input': 1, 'output': 1, 'cacheRead': 1, 'cacheWrite': 1},
                'byProject': [], 'byTool': [], 'byModel': [], 'costUsd': None, 'unpricedModels': []}
        record = {'limits': [{'label': 'Weekly (7-day)', 'percent': 0.1}]}
        (self.usage / 'claude.json').write_text(json.dumps(record))
        for bad in (float('nan'), float('inf'), 10 ** 400):
            data = {**base, 'byKind': {**base['byKind'], 'input': bad}}
            (self.breakdown / 'claude.json').write_text(json.dumps(data))
            row = usage_summary.rows([{'id': 'claude', 'name': 'Claude', 'enabled': True}])[0]
            self.assertIsNone(row['spend'], repr(bad))

    def test_model_tokens_count_only_the_four_usage_buckets_not_capacity_metadata(self):
        usage = {'m': {'inputTokens': 100, 'outputTokens': 50, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0, 'maxOutputTokens': 32000}}
        row = self.row({'limits': [{'label': 'Weekly (7-day)', 'percent': 0.1}], 'modelUsage': usage})
        self.assertEqual(row['models'], [{'name': 'm', 'tokens': '150'}])

    def test_an_invalid_or_missing_breakdown_gives_no_spend_section(self):
        record = {'limits': [{'label': 'Weekly (7-day)', 'percent': 0.1}]}
        self.assertIsNone(self.row(record)['spend'])
        self.assertIsNone(self.row(record, {'schemaVersion': 1})['spend'])
        self.assertIsNone(self.row(record, {'schemaVersion': 2, 'byKind': {}})['spend'])


START = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


class LegacyTotalTests(unittest.TestCase):
    def test_a_schema_one_breakdown_without_a_measurement_still_supports_total_tokens(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_STATE_HOME': temp}):
            folder = Path(temp) / 'omarchy/agents/breakdown'; folder.mkdir(parents=True)
            (folder / 'claude.json').write_text(json.dumps({'schemaVersion': 1, 'byKind': {'input': 1, 'output': 1, 'cacheRead': 1, 'cacheWrite': 1},
                                                            'byProject': [], 'byTool': [], 'costUsd': None, 'unpricedModels': []}))
            self.assertEqual(usage_summary.total_tokens([{'id': 'claude', 'enabled': True}]), 4)


AS_OF = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
OWN_START = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / 'omarchy/agents/breakdown'
        self.folder.mkdir(parents=True)
        patcher = patch.dict(os.environ, {'XDG_STATE_HOME': temp.name}); patcher.start(); self.addCleanup(patcher.stop)

    def write(self, agent, *, start=OWN_START, days=7, generated=AS_OF, since='auto', jefe=(60, 5), otros=(0, 0), limited=False, block='ok'):
        data = {'schemaVersion': 1, 'id': agent, 'generatedAt': generated.isoformat(),
                'byKind': {'input': 1, 'output': 1, 'cacheRead': 1, 'cacheWrite': 1},
                'byProject': [], 'byTool': [], 'costUsd': None, 'unpricedModels': []}
        if since == 'auto':
            since = max(start, generated - timedelta(days=days))
        if block == 'ok':
            data['measurement'] = {'start': start.isoformat(), 'since': since.isoformat(), 'windowDays': days,
                                   'jefe': {'total_input': jefe[0], 'output': jefe[1]},
                                   'otros': {'total_input': otros[0], 'output': otros[1]}, 'classificationLimited': limited}
        elif block is not None:
            data['measurement'] = block
        (self.folder / (agent + '.json')).write_text(json.dumps(data))

    @staticmethod
    def agents(*ids, enabled=True):
        return [{'id': agent, 'enabled': enabled} for agent in ids]

    def measure(self, *ids, start=OWN_START, days=7):
        return usage_summary.measurement(self.agents(*ids), start=start, window_days=days)

    def test_measurement_sums_jefe_and_otros_of_every_selected_account(self):
        self.write('claude', jefe=(60, 5), otros=(10, 1))
        self.write('codex', jefe=(100, 5), otros=(20, 2), limited=True)
        self.assertEqual(self.measure('claude', 'codex'),
                         {'since': AS_OF - timedelta(days=7), 'asOf': AS_OF, 'jefe': 170, 'otros': 33, 'limited': True})

    def test_measurement_rejects_wrong_effective_cutoff(self):
        self.write('claude', since=OWN_START + timedelta(microseconds=1))
        self.assertIsNone(self.measure('claude'))
        self.write('claude', start=AS_OF - timedelta(days=40), since=AS_OF - timedelta(days=30))  # 30-day cutoff under a 7-day request
        self.assertIsNone(self.measure('claude', start=AS_OF - timedelta(days=40)))

    def test_measurement_accepts_equivalent_utc_offsets(self):
        offset = timezone(timedelta(hours=-3))
        self.write('claude', start=OWN_START.astimezone(offset), generated=AS_OF.astimezone(offset))
        result = self.measure('claude')
        self.assertEqual((result['since'], result['asOf']), (AS_OF - timedelta(days=7), AS_OF))

    def test_measurement_rejects_another_selected_window(self):
        self.write('claude', days=30)
        self.assertIsNone(self.measure('claude', days=7))
        self.assertEqual(self.measure('claude', days=30)['jefe'], 65)
        self.assertIsNone(self.measure('claude', days=14))

    def test_measurement_rejects_mixed_account_generations(self):
        self.write('claude')
        self.write('codex', generated=AS_OF + timedelta(minutes=1))
        self.assertIsNone(self.measure('claude', 'codex'))

    def test_measurement_rejects_another_start(self):
        self.write('claude')
        self.assertIsNone(self.measure('claude', start=AS_OF + timedelta(days=1)))
        self.assertIsNone(self.measure('claude', start=OWN_START - timedelta(seconds=1)))

    def test_old_since_start_block_does_not_satisfy_the_new_total(self):
        self.write('claude', block={'since': OWN_START.isoformat(), 'total_input': 1, 'output': 0})
        self.assertIsNone(self.measure('claude'))

    def test_invalid_counters_or_flag_are_unknown(self):
        good = {'start': OWN_START.isoformat(), 'since': OWN_START.isoformat(), 'windowDays': 7,
                'jefe': {'total_input': 1, 'output': 0}, 'otros': {'total_input': 0, 'output': 0}, 'classificationLimited': False}
        for change in ({'jefe': {'total_input': -1, 'output': 0}}, {'jefe': {'total_input': True, 'output': 0}},
                       {'otros': {'total_input': 1.5, 'output': 0}}, {'otros': {'total_input': 1}}, {'otros': None},
                       {'classificationLimited': 1}, {'classificationLimited': None}, {'windowDays': True}, {'windowDays': '7'},
                       {'start': 'yesterday'}, {'since': '2026-11-01T08:00:00'}):
            self.write('claude', block={**good, **change})
            self.assertIsNone(self.measure('claude'), repr(change))

    def test_missing_generation_or_missing_account_is_unknown_not_zero(self):
        self.write('claude')
        self.assertIsNone(self.measure('claude', 'codex'))
        data = json.loads((self.folder / 'claude.json').read_text()); data.pop('generatedAt')
        (self.folder / 'claude.json').write_text(json.dumps(data))
        self.assertIsNone(self.measure('claude'))
        self.assertIsNone(usage_summary.measurement([], start=OWN_START, window_days=7))

    def test_unselected_or_unsupported_accounts_are_ignored_and_known_zero_is_zero(self):
        self.write('claude', jefe=(0, 0))
        agents = self.agents('claude') + self.agents('codex', enabled=False) + self.agents('pi')
        result = usage_summary.measurement(agents, start=OWN_START, window_days=7)
        self.assertEqual((result['jefe'], result['otros']), (0, 0))

    def test_start_in_the_future_of_the_snapshot_is_unknown(self):
        self.write('claude', start=AS_OF + timedelta(days=1), since=AS_OF + timedelta(days=1))
        self.assertIsNone(self.measure('claude', start=AS_OF + timedelta(days=1)))

    def test_invalid_window_request_is_unknown(self):
        self.write('claude')
        self.assertIsNone(self.measure('claude', days=True))


if __name__ == '__main__':
    unittest.main()
