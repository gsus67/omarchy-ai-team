import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import role_usage

AGENTS = [{'id': 'claude', 'enabled': True}, {'id': 'codex', 'enabled': False}]
BOTH = [{'id': 'claude', 'enabled': True}, {'id': 'codex', 'enabled': True}]
START = datetime(2026, 8, 1, 6, 30, tzinfo=timezone.utc)  # long before "now": the 30-day window must not matter


class RoleUsageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patcher = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root)}); patcher.start(); self.addCleanup(patcher.stop)
        self.start_file = self.root / 'omarchy-ai-team/usage-start.json'
        previous = os.umask(0o077); self.addCleanup(os.umask, previous)  # the state is private, as in a real session

    def start(self, moment=START):
        self.start_file.parent.mkdir(parents=True, exist_ok=True)
        self.start_file.write_text(json.dumps({'schemaVersion': 1, 'since': moment.isoformat()}))

    def breakdown(self, agent, tokens, output=0, since=START):
        folder = self.root / 'omarchy/agents/breakdown'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (agent + '.json')).write_text(json.dumps({
            'schemaVersion': 1, 'byKind': {'input': 7, 'output': 7, 'cacheRead': 7, 'cacheWrite': 7},  # the 30-day detail must not be used
            'byProject': [], 'byTool': [], 'costUsd': None, 'unpricedModels': [],
            'sinceStart': {'since': since.isoformat(), 'total_input': tokens, 'output': output}}))

    def counters(self, inputs=100, output=20):
        return {'total_input': inputs, 'cached_read': 0, 'new_input': inputs, 'cache_creation': 0, 'output': output}

    def rows(self, *rows):
        path = role_usage.ledger(); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join((row if isinstance(row, str) else json.dumps(row)) + '\n' for row in rows))

    @staticmethod
    def row(at, tokens, role='code', agent='claude'):
        return {'at': at.isoformat() if isinstance(at, datetime) else at, 'role': role, 'agent': agent, 'tokens': tokens}

    @staticmethod
    def tokens(result):
        return {row['role']: row['tokens'] for row in result['roles']}

    def test_without_selected_supported_accounts_there_is_nothing_to_measure(self):
        self.assertIsNone(role_usage.summary([{'id': 'codex', 'enabled': False}, {'id': 'pi', 'enabled': True}]))

    def test_a_missing_chief_breakdown_keeps_the_page_with_unknown_total_and_shares(self):
        self.start()
        role_usage.record('review', 'claude', self.counters(300, 100))
        result = role_usage.summary(AGENTS)
        self.assertIsNone(result['total'])
        self.assertEqual(self.tokens(result), {'jefe': None, 'plan': '0', 'code': '0', 'review': '400', 'simple': '0'})
        self.assertTrue(all(row['share'] is None for row in result['roles']))
        self.assertEqual(result['since'], START.isoformat())

    def test_the_total_is_the_chief_plus_the_workers_in_the_same_unit(self):
        self.start()
        self.breakdown('claude', 60, 5)
        self.breakdown('codex', 100, 5)
        role_usage.record('review', 'claude', self.counters(300, 100))
        role_usage.record('code', 'codex', self.counters(60, 5))
        result = role_usage.summary(BOTH)
        self.assertEqual(self.tokens(result), {'jefe': '170', 'plan': '0', 'code': '65', 'review': '400', 'simple': '0'})
        self.assertEqual(result['total'], '635')
        self.assertEqual({row['role']: row['share'] for row in result['roles']}['review'], round(400 / 635, 4))
        self.assertEqual(result['since'], START.isoformat())

    def test_workers_alone_give_the_total_when_the_chief_is_a_known_zero(self):
        self.start()
        self.breakdown('claude', 0, 0)
        result = role_usage.summary(AGENTS)
        self.assertEqual((result['total'], self.tokens(result)['jefe']), ('0', '0'))
        self.assertEqual({row['share'] for row in result['roles']}, {0})

    def test_a_partial_chief_is_unknown_not_a_partial_sum(self):
        self.start()
        self.breakdown('claude', 60, 5)
        result = role_usage.summary(BOTH)
        self.assertIsNone(result['total'])
        self.assertIsNone(self.tokens(result)['jefe'])

    def test_workers_include_entries_older_than_30_days_since_start(self):
        self.start(datetime.now(timezone.utc) - timedelta(days=90))
        self.breakdown('claude', 0, since=datetime.now(timezone.utc) - timedelta(days=90))
        self.rows(self.row(datetime.now(timezone.utc) - timedelta(days=60), 40), self.row(datetime.now(timezone.utc) - timedelta(days=100), 999))
        self.assertEqual(self.tokens(role_usage.summary(AGENTS))['code'], '40')

    def test_worker_entry_exactly_at_start_is_included(self):
        self.start()
        self.breakdown('claude', 0)
        self.rows(self.row(START - timedelta(microseconds=1), 1000), self.row(START, 10), self.row(START + timedelta(microseconds=1), 5),
                  self.row('2026-08-01T03:30:00-03:00', 100))  # the same instant as START in another offset
        self.assertEqual(self.tokens(role_usage.summary(AGENTS))['code'], '115')

    def test_ledger_larger_than_one_mib_keeps_oldest_totals(self):
        self.start()
        self.breakdown('claude', 0)
        filler = [self.row(START + timedelta(minutes=minute), 1) for minute in range(1, 14000)]
        self.rows(self.row(START, 700), *filler, self.row(START + timedelta(days=1), 1100))
        self.assertGreater(role_usage.ledger().stat().st_size, 1024 * 1024)
        self.assertEqual(self.tokens(role_usage.summary(AGENTS))['code'], '15.8K')

    def test_record_does_not_prune_existing_history(self):
        old = self.row(datetime.now(timezone.utc) - timedelta(days=200), 5)
        self.rows(old, 'not json')
        before = role_usage.ledger().read_bytes()
        role_usage.record('plan', 'claude', self.counters())
        self.assertTrue(role_usage.ledger().read_bytes().startswith(before))
        self.assertEqual(len(role_usage.ledger().read_text().splitlines()), 3)

    def test_record_creates_the_start_before_the_first_entry(self):
        before = datetime.now(timezone.utc)
        role_usage.record('plan', 'claude', self.counters())
        moment = datetime.fromisoformat(json.loads(self.start_file.read_text())['since'])
        self.assertGreaterEqual(moment, before)
        self.assertEqual(oct(self.start_file.stat().st_mode & 0o777), oct(0o600))

    def test_unknown_counters_and_the_chief_role_are_not_recorded(self):
        role_usage.record('review', 'claude', {'total_input': None, 'output': 5})
        role_usage.record('jefe', 'claude', self.counters())
        self.assertFalse(role_usage.ledger().exists())

    def test_malformed_rows_and_other_accounts_are_ignored(self):
        self.start()
        self.breakdown('claude', 50)
        now = datetime.now(timezone.utc)
        self.rows(self.row(now - timedelta(days=45), 20), 'not json', '[]', json.dumps({'at': now.isoformat()}),
                  self.row('x', 1), self.row(now, 700, agent='codex'), self.row(now, 5, role='jefe'), self.row(now, True),
                  self.row(now, -3), json.dumps({'at': now.isoformat(), 'agent': 'claude', 'tokens': 9}), self.row(now, 80))
        roles = self.tokens(role_usage.summary(AGENTS))
        self.assertEqual((roles['code'], roles['jefe']), ('100', '50'))

    def test_worker_tokens_use_total_input_plus_output_once(self):
        self.start()
        self.breakdown('claude', 0)
        role_usage.record('code', 'claude', {'total_input': 60, 'output': 5, 'cached_read': 50, 'cache_creation': 10, 'new_input': 0})
        self.assertEqual(self.tokens(role_usage.summary(AGENTS))['code'], '65')

    def test_a_fifo_in_place_of_the_ledger_never_blocks(self):
        self.start()
        self.breakdown('claude', 50)
        path = role_usage.ledger(); path.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(path)
        result = role_usage.summary(AGENTS)
        self.assertEqual(self.tokens(result), {'jefe': '50', 'plan': None, 'code': None, 'review': None, 'simple': None})
        self.assertIsNone(result['total'])
        with self.assertRaises(OSError):  # no reader: refused at once, not blocked
            role_usage.record('plan', 'claude', self.counters())

    def test_non_regular_ledger_keeps_workers_total_and_shares_unknown(self):
        self.start()
        self.breakdown('claude', 50)
        role_usage.ledger().mkdir(parents=True)
        result = role_usage.summary(AGENTS)
        self.assertEqual(self.tokens(result), {'jefe': '50', 'plan': None, 'code': None, 'review': None, 'simple': None})
        self.assertIsNone(result['total'])
        self.assertEqual({row['share'] for row in result['roles']}, {None})

    def test_record_does_not_follow_symlinked_state_directory(self):
        target = self.root / 'elsewhere'
        target.mkdir(mode=0o700)
        self.start_file.parent.symlink_to(target)
        for content in (None, '{corrupt'):  # a corrupt start must not let an unsafe directory pass
            with self.subTest(start=content):
                if content:
                    (target / 'usage-start.json').write_text(content)
                with self.assertRaises(OSError):
                    role_usage.record('plan', 'claude', self.counters())
                self.assertEqual(sorted(path.name for path in target.iterdir()), ['usage-start.json'] if content else [])

    def test_record_does_not_add_owner_permissions_to_existing_state(self):
        if os.geteuid() == 0:
            self.skipTest('running with privileges that ignore file modes')
        self.start()
        self.breakdown('claude', 50)
        self.rows(self.row(START, 5))
        folder, ledger = role_usage.ledger().parent, role_usage.ledger()
        self.start_file.chmod(0o600)
        ledger.chmod(0o600)
        folder.chmod(0o500)
        self.addCleanup(folder.chmod, 0o700)
        role_usage.record('plan', 'claude', self.counters())
        self.assertEqual((folder.stat().st_mode & 0o777, ledger.stat().st_mode & 0o777), (0o500, 0o600))
        ledger.chmod(0o400)
        with self.assertRaises(OSError):
            role_usage.record('plan', 'claude', self.counters())
        self.assertEqual((folder.stat().st_mode & 0o777, ledger.stat().st_mode & 0o777), (0o500, 0o400))

    def test_the_ledger_is_private(self):
        role_usage.record('plan', 'claude', self.counters())
        role_usage.ledger().chmod(0o666)
        role_usage.record('plan', 'claude', self.counters())
        self.assertEqual(role_usage.ledger().stat().st_mode & 0o777, 0o600)
        self.assertEqual(role_usage.ledger().parent.stat().st_mode & 0o777, 0o700)

    def test_entries_before_a_start_in_the_future_are_excluded_and_the_start_does_not_move(self):
        future = datetime.now(timezone.utc) + timedelta(hours=2)  # a clock that was ahead when the start was written
        self.start(future)
        self.breakdown('claude', 10, since=future)
        role_usage.record('code', 'claude', self.counters())
        result = role_usage.summary(AGENTS)
        self.assertEqual((self.tokens(result)['code'], self.tokens(result)['jefe']), ('0', '10'))
        self.assertEqual(json.loads(self.start_file.read_text())['since'], future.isoformat())

    def test_a_chief_block_from_another_start_is_unknown_without_a_thirty_day_fallback(self):
        self.start()
        self.breakdown('claude', 50, since=START + timedelta(days=1))
        result = role_usage.summary(AGENTS)
        self.assertIsNone(self.tokens(result)['jefe'])
        self.assertIsNone(result['total'])

    def test_a_corrupt_start_makes_the_measurement_unknown_without_resetting_it(self):
        self.start_file.parent.mkdir(parents=True)
        self.start_file.write_text('{broken')
        self.breakdown('claude', 50)
        result = role_usage.summary(AGENTS)
        self.assertEqual((result['since'], result['total']), (None, None))
        self.assertTrue(all(row['tokens'] is None and row['share'] is None for row in result['roles']))
        self.assertEqual(self.start_file.read_text(), '{broken')

    def test_status_survives_invalid_usage_start(self):
        self.breakdown('claude', 60, 5)
        self.rows(self.row(START + timedelta(days=1), 7))
        for stamp in ('0001-01-01T00:00:00+01:00', '9999-12-31T23:59:59-01:00'):
            self.start_file.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps({'schemaVersion': 1, 'since': stamp})
            self.start_file.write_text(content)
            result = role_usage.summary(AGENTS)
            self.assertEqual((result['since'], result['total']), (None, None), stamp)
            self.assertEqual(set(self.tokens(result).values()), {None})
            self.assertEqual(self.start_file.read_text(), content)

    def test_a_start_error_does_not_turn_a_valid_worker_run_into_zero(self):
        self.start_file.parent.mkdir(parents=True)
        self.start_file.write_text('{broken')
        role_usage.record('code', 'claude', self.counters(30, 5))  # must not raise, and the run is still recorded
        self.assertEqual(json.loads(role_usage.ledger().read_text())['tokens'], 35)
        self.assertEqual(self.start_file.read_text(), '{broken')

    def snapshot(self):
        """Every path under the state root with its mode and bytes, to prove that nothing was created or changed."""
        return {str(p.relative_to(self.root)): (p.stat().st_mode, p.read_bytes() if p.is_file() else None)
                for p in sorted(self.root.rglob('*'))}

    def test_status_read_does_not_bootstrap_start(self):
        state = self.root / 'omarchy-ai-team'
        prepare = {'absent directory': lambda: None,
                   'directory without start': lambda: state.mkdir(mode=0o700),
                   'ledger without start': lambda: self.rows(self.row(START, 5))}
        for name, setup in prepare.items():
            with self.subTest(name), patch.object(role_usage.usage_start, '_publish') as publish:
                shutil.rmtree(state, ignore_errors=True)
                setup()
                before = self.snapshot()
                result = role_usage.summary(AGENTS)
                self.assertIsNone(result['since'])
                self.assertIsNone(result['total'])
                self.assertTrue(all(row['tokens'] is None and row['share'] is None for row in result['roles']))
                publish.assert_not_called()
                self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.start_file.exists())

    def test_summary_keeps_validated_directory_for_ledger(self):
        for outside_ledger in (True, False):
            with self.subTest(outside_ledger=outside_ledger):
                shutil.rmtree(self.root, ignore_errors=True); self.root.mkdir()
                self.start()
                self.breakdown('claude', 0, 0)
                self.rows(self.row(START + timedelta(hours=1), 7))
                outside = self.root / 'outside'
                outside.mkdir(mode=0o700)
                if outside_ledger:
                    (outside / 'role-usage.jsonl').write_text(json.dumps(self.row(START + timedelta(hours=1), 999)) + '\n')
                before = self.snapshot_of(outside)
                state, real, swapped = self.start_file.parent, role_usage.usage_start._read_start, []

                def read_then_replace_parent(directory):
                    moment = real(directory)
                    if not swapped:
                        swapped.append(True)
                        state.rename(self.root / 'moved')
                        state.symlink_to(outside)
                    return moment
                with patch.object(role_usage.usage_start, '_read_start', read_then_replace_parent):
                    result = role_usage.summary(AGENTS)
                self.assertEqual(swapped, [True])
                self.assertEqual((result['since'], result['total']), (START.isoformat(), '7'))
                self.assertEqual(self.tokens(result)['code'], '7')
                self.assertEqual(self.snapshot_of(outside), before)

    def snapshot_of(self, folder):
        return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}

    def test_an_unreadable_ledger_makes_the_workers_unknown_not_zero(self):
        self.start()
        self.breakdown('claude', 50)
        self.rows(self.row(START, 5))
        role_usage.ledger().chmod(0)
        self.addCleanup(role_usage.ledger().chmod, 0o600)
        if os.access(role_usage.ledger(), os.R_OK):
            self.skipTest('running with privileges that ignore file modes')
        result = role_usage.summary(AGENTS)
        self.assertEqual(self.tokens(result), {'jefe': '50', 'plan': None, 'code': None, 'review': None, 'simple': None})
        self.assertIsNone(result['total'])


    @unittest.skipUnless(os.path.isdir('/proc/self/fd'), 'needs /proc/self/fd')
    def test_repeated_directory_ledger_summary_does_not_leak_fds(self):
        self.start()
        self.breakdown('claude', 50)
        role_usage.ledger().mkdir()
        before, fixture = len(os.listdir('/proc/self/fd')), self.snapshot()
        for _ in range(20):
            result = role_usage.summary(AGENTS)
            self.assertEqual(self.tokens(result), {'jefe': '50', 'plan': None, 'code': None, 'review': None, 'simple': None})
            self.assertIsNone(result['total'])
            self.assertTrue(all(row['share'] is None for row in result['roles']))
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)
        self.assertEqual(self.snapshot(), fixture)


if __name__ == '__main__':
    unittest.main()
