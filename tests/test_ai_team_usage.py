import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/usage'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_team_usage
from test_usage_breakdown import codex_context, codex_count, denied, entry_type_denied

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
                                          'XDG_STATE_HOME': str(self.root / 'state'),
                                          'XDG_CONFIG_HOME': str(self.root / 'config')})
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

    def start_file(self):
        return self.root / 'state/omarchy-ai-team/usage-start.json'

    def persist_start(self, since='2020-01-01T00:00:00+00:00'):
        self.start_file().parent.mkdir(parents=True, mode=0o700)
        self.start_file().parent.chmod(0o700)
        self.start_file().write_text(json.dumps({'schemaVersion': 1, 'since': since}))
        self.start_file().chmod(0o600)

    def test_breakdowns_carry_the_persisted_start_and_its_totals(self):
        self.persist_start()
        self.transcript('.claude-2')
        ai_team_usage.write_breakdowns()
        written = json.loads((ai_team_usage.breakdown_dir() / 'claude-2.json').read_text())
        as_of = datetime.fromisoformat(written['generatedAt'])
        self.assertEqual(written['measurement'], {
            'start': '2020-01-01T00:00:00+00:00', 'since': (as_of - timedelta(days=7)).isoformat(), 'windowDays': 7,
            'jefe': {'total_input': 1_000_000, 'output': 0, 'cache': 0},
            'otros': {'total_input': 0, 'output': 0, 'cache': 0}, 'classificationLimited': True})
        self.assertEqual(written['windowDays'], 30)
        self.assertNotIn('sinceStart', written)

    def test_one_start_is_resolved_once_and_shared_by_the_four_scans(self):
        seen = []

        def scan(*args, since=None, **kwargs):
            seen.append(since)
        with patch.object(ai_team_usage.breakdown, 'scan_claude', scan), patch.object(ai_team_usage.breakdown, 'scan_codex', scan):
            ai_team_usage.write_breakdowns()
            first = self.start_file().read_text()
            ai_team_usage.write_breakdowns()
        self.assertEqual(len(seen), 8)
        self.assertEqual(len(set(seen)), 1)
        self.assertEqual(seen[0].isoformat(), json.loads(first)['since'])
        self.assertEqual(self.start_file().read_text(), first)

    def test_a_failed_start_keeps_every_breakdown_and_scans_nothing(self):
        self.transcript('.claude-2')
        ai_team_usage.write_breakdowns()
        folder = ai_team_usage.breakdown_dir()
        before = {path.name: path.read_text() for path in folder.glob('*.json')}
        self.start_file().write_text('{broken')
        with patch.object(ai_team_usage.breakdown, 'scan_claude') as claude, patch.object(ai_team_usage.breakdown, 'scan_codex') as codex:
            failed = ai_team_usage.write_breakdowns()
        self.assertEqual(sorted(failed), sorted(ai_team_usage.BREAKDOWN_ACCOUNTS))
        self.assertEqual((claude.called, codex.called), (False, False))
        self.assertEqual({path.name: path.read_text() for path in folder.glob('*.json')}, before)
        self.assertEqual(self.start_file().read_text(), '{broken')

    def test_missing_profile_removes_a_stale_breakdown_and_an_empty_readable_one_is_a_known_zero(self):
        stale = ai_team_usage.breakdown_dir()
        stale.mkdir(parents=True)
        (stale / 'claude.json').write_text('{}')
        (self.root / 'home/.codex-2/sessions').mkdir(parents=True)
        (stale / 'codex-2.json').write_text('{}')
        ai_team_usage.write_breakdowns()
        self.assertEqual([path.name for path in stale.glob('*.json')], ['codex-2.json'])
        written = json.loads((stale / 'codex-2.json').read_text())
        self.assertEqual((written['measurement']['jefe'], written['measurement']['otros']),
                         ({'total_input': 0, 'output': 0, 'cache': 0},) * 2)
        self.assertFalse(written['measurement']['classificationLimited'])
        self.assertEqual(written['byKind'], dict.fromkeys(('input', 'output', 'cacheRead', 'cacheWrite'), 0))

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

    def test_enumeration_failure_keeps_snapshot_bytes_and_generated_at(self):
        self.transcript('.claude-2')
        ai_team_usage.write_breakdowns(now=datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc))
        target = ai_team_usage.breakdown_dir() / 'claude-2.json'
        good = target.read_bytes()
        hidden = self.root / 'home/.claude-2/projects/secret'
        hidden.mkdir()
        with denied(hidden):
            failed = ai_team_usage.write_breakdowns(now=datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(failed, ['claude-2'])
        self.assertEqual(target.read_bytes(), good)
        self.assertEqual(json.loads(good)['generatedAt'], '2026-11-02T09:00:00+00:00')

    def test_entry_type_permission_error_preserves_snapshot(self):
        t0, t1 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc), datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
        for account in ('claude-2', 'codex-2'):
            for previous in (False, True):
                with self.subTest(account=account, previous=previous):
                    shutil.rmtree(self.root / 'home', ignore_errors=True)
                    shutil.rmtree(self.root / 'state', ignore_errors=True)
                    (self.root / 'home').mkdir()
                    if account == 'claude-2':
                        self.transcript('.claude-2')
                        tree = self.root / 'home/.claude-2/projects'
                    else:
                        folder = self.root / 'home/.codex-2/sessions/2026/11/02'
                        folder.mkdir(parents=True)
                        (folder / 'r.jsonl').write_text('\n'.join((codex_context(), codex_count(at=t0))) + '\n')
                        tree = self.root / 'home/.codex-2/sessions'
                    (tree / 'unreadable-subtree').mkdir()
                    target = ai_team_usage.breakdown_dir() / (account + '.json')
                    if previous:
                        self.assertEqual(ai_team_usage.write_breakdowns(now=t0), [])
                        good = target.read_bytes()
                    with entry_type_denied():
                        self.assertEqual(ai_team_usage.write_breakdowns(now=t1), [account])
                    if previous:
                        self.assertEqual(target.read_bytes(), good)
                        self.assertEqual(json.loads(good)['generatedAt'], t0.isoformat())
                    else:
                        self.assertFalse(target.exists())

    def test_inaccessible_source_does_not_delete_last_good_snapshot(self):
        (self.root / 'home/.codex-2/sessions').mkdir(parents=True)
        ai_team_usage.write_breakdowns()
        target = ai_team_usage.breakdown_dir() / 'codex-2.json'
        good = target.read_bytes()
        with denied(self.root / 'home/.codex-2/sessions', 'stat'):
            failed = ai_team_usage.write_breakdowns()
        self.assertEqual(failed, ['codex-2'])
        self.assertEqual(target.read_bytes(), good)

    def test_readable_empty_source_has_known_zero(self):
        (self.root / 'home/.claude-2/projects').mkdir(parents=True)
        self.assertEqual(ai_team_usage.write_breakdowns(), [])
        written = json.loads((ai_team_usage.breakdown_dir() / 'claude-2.json').read_text())
        self.assertEqual((written['measurement']['jefe'], written['measurement']['otros']),
                         ({'total_input': 0, 'output': 0, 'cache': 0},) * 2)
        self.assertFalse(written['measurement']['classificationLimited'])

    def test_source_disappearing_mid_scan_does_not_publish_partial_total(self):
        self.transcript('.claude-2')
        ai_team_usage.write_breakdowns()
        target = ai_team_usage.breakdown_dir() / 'claude-2.json'
        good = target.read_bytes()
        folder = self.root / 'home/.claude-2/projects/p'
        real = os.scandir

        def vanishing(path, *args, **kwargs):
            if os.fspath(path) == str(folder):
                raise FileNotFoundError(2, 'gone', str(folder))
            return real(path, *args, **kwargs)
        with patch.object(os, 'scandir', vanishing):
            self.assertEqual(ai_team_usage.write_breakdowns(), ['claude-2'])
        self.assertEqual(target.read_bytes(), good)

    NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def settings(self, content=None, **keys):
        path = self.root / 'config/omarchy-ai-team/settings.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if content is not None else json.dumps(keys))
        return path

    def test_window_days_setting_defaults_to_seven_and_is_strict(self):
        self.assertEqual(ai_team_usage.window_days_setting(), 7)  # no file: nothing is written either
        self.assertFalse((self.root / 'config').exists())
        self.settings(other=1)
        self.assertEqual(ai_team_usage.window_days_setting(), 7)
        self.settings(usageWindowDays=30)
        self.assertEqual(ai_team_usage.window_days_setting(), 30)
        for invalid in ('7', 7.0, True, 14, 0, None, [7]):
            self.settings(usageWindowDays=invalid)
            self.assertIsNone(ai_team_usage.window_days_setting(), invalid)
        self.settings('{broken')
        self.assertIsNone(ai_team_usage.window_days_setting())
        path = self.settings(usageWindowDays=30)
        path.unlink()
        path.symlink_to(self.root / 'elsewhere.json')
        self.assertIsNone(ai_team_usage.window_days_setting())

    def test_writer_shares_start_window_and_timestamp_across_four_scans(self):
        self.persist_start('2020-01-01T00:00:00+00:00')
        seen = []

        def scan(root, table, days, now, *args, **kwargs):
            seen.append((now, kwargs['since'], kwargs['total_days'], days))
        with patch.object(ai_team_usage.breakdown, 'scan_claude', scan), patch.object(ai_team_usage.breakdown, 'scan_codex', scan):
            ai_team_usage.write_breakdowns(self.NOW, window_days=30)
        self.assertEqual(len(seen), 4)
        self.assertEqual(set(seen), {(self.NOW, datetime(2020, 1, 1, tzinfo=timezone.utc), 30, 30)})

    def test_managed_runs_follow_the_ledger_engines_inside_the_window(self):
        self.persist_start('2020-01-01T00:00:00+00:00')
        flags = {}

        def scan(engine):
            def run(root, table, days, now, *args, managed_runs, **kwargs):
                flags.setdefault(engine, set()).add(managed_runs)
            return run
        ledger = self.root / 'state/omarchy-ai-team/role-usage.jsonl'

        def run(**kwargs):
            flags.clear()
            with patch.object(ai_team_usage.breakdown, 'scan_claude', scan('claude')), patch.object(ai_team_usage.breakdown, 'scan_codex', scan('codex')):
                ai_team_usage.write_breakdowns(self.NOW, **kwargs)
            return flags['claude'], flags['codex']

        def row(at, agent):
            return json.dumps({'at': at, 'role': 'code', 'agent': agent, 'tokens': 1}) + '\n'
        self.assertEqual(run(), ({False}, {False}))  # no ledger: nothing was managed
        ledger.write_text(row('2026-11-19T12:00:00+00:00', 'claude-2') + row('2026-11-01T12:00:00+00:00', 'codex'))
        self.assertEqual(run(), ({True}, {False}))  # the codex run is older than the seven days
        self.assertEqual(run(window_days=30), ({True}, {True}))
        ledger.unlink()
        ledger.mkdir()
        self.assertEqual(run(), ({True}, {True}))  # an unreadable ledger: every engine is managed

    def test_a_start_after_now_publishes_detail_without_measurement_and_keeps_the_start(self):
        self.persist_start('2030-01-01T00:00:00+00:00')
        self.transcript('.claude-2')
        saved = self.start_file().read_text()
        self.assertEqual(ai_team_usage.write_breakdowns(self.NOW), [])
        written = json.loads((ai_team_usage.breakdown_dir() / 'claude-2.json').read_text())
        self.assertNotIn('measurement', written)
        self.assertEqual(self.start_file().read_text(), saved)

    def test_main_reads_the_preference_once_and_an_invalid_one_publishes_no_measurement(self):
        self.persist_start()
        self.transcript('.claude-2')
        self.settings(usageWindowDays=30)
        ai_team_usage.main(['--breakdowns-only'])
        written = json.loads((ai_team_usage.breakdown_dir() / 'claude-2.json').read_text())
        self.assertEqual(written['measurement']['windowDays'], 30)
        self.settings(usageWindowDays=14)
        ai_team_usage.main(['--breakdowns-only'])
        written = json.loads((ai_team_usage.breakdown_dir() / 'claude-2.json').read_text())
        self.assertNotIn('measurement', written)
        self.assertEqual(written['windowDays'], 30)

    def test_breakdowns_only_does_not_call_official_collectors(self):
        self.persist_start()
        self.transcript('.claude-2')
        (self.root / 'home/.codex-2').mkdir()
        with patch.object(ai_team_usage, 'collect', side_effect=AssertionError('collector called')):
            self.assertEqual(ai_team_usage.main(['--breakdowns-only']), 0)
        self.assertTrue((ai_team_usage.breakdown_dir() / 'claude-2.json').exists())
        self.assertFalse(ai_team_usage.state_dir().exists())

    def test_changed_preference_or_start_before_publish_discards_the_attempt(self):
        self.persist_start()
        self.transcript('.claude-2')
        self.assertEqual(ai_team_usage.write_breakdowns(window_days=7), [])
        folder = ai_team_usage.breakdown_dir()
        before = {path.name: path.read_text() for path in folder.glob('*.json')}
        real = ai_team_usage.breakdown.scan_claude

        def changing(*args, **kwargs):
            self.settings(usageWindowDays=30)
            return real(*args, **kwargs)
        with patch.object(ai_team_usage.breakdown, 'scan_claude', changing):
            self.assertEqual(sorted(ai_team_usage.write_breakdowns(window_days=7)), sorted(ai_team_usage.BREAKDOWN_ACCOUNTS))
        self.assertEqual({path.name: path.read_text() for path in folder.glob('*.json')}, before)
        self.settings(usageWindowDays=7)

        def moving(*args, **kwargs):
            self.start_file().write_text(json.dumps({'schemaVersion': 1, 'since': '2021-01-01T00:00:00+00:00'}))
            return real(*args, **kwargs)
        with patch.object(ai_team_usage.breakdown, 'scan_claude', moving):
            self.assertEqual(sorted(ai_team_usage.write_breakdowns(window_days=7)), sorted(ai_team_usage.BREAKDOWN_ACCOUNTS))
        self.assertEqual({path.name: path.read_text() for path in folder.glob('*.json')}, before)

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
