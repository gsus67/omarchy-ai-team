"""Muse Code (`muse`): only what its --help proves; the rest is explicitly omitted."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import agent_runtime
import context_policy
import harness
import native_launcher
import native_muse
import native_plugins
import native_team
import team
import usage_summary

MUSE_EXEC = ['muse', 'exec', '--model', 'chosen', '--reasoning-effort', 'high', 'task']


class MuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _catalog(self, rows, name='a.json', raw=None):
        folder = self.root / 'muse' / 'model-catalog'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(raw if raw is not None else json.dumps({'rows': rows}))

    def _models(self):
        with patch.dict(os.environ, {'XDG_DATA_HOME': str(self.root)}):
            return native_muse.muse_models()

    def test_catalog_visible_only_newest_first_and_safe_ids(self):
        row = lambda m, v, d: {'model_id': m, 'visibility': v, 'release_date': d}
        self._catalog([row('old-1', 'visible', '2026-01-01'), row('hid-1', 'hidden', '2026-12-01'),
                       row('new-1', 'visible', '2026-10-01'), row('x; rm -rf', 'visible', '2026-11-01'),
                       row('old-1', 'visible', '2026-01-01'), row(5, 'visible', '2026-11-01')])
        self.assertEqual(self._models(), ['new-1', 'old-1'])

    def test_deep_nesting_and_bad_dates_are_safe(self):
        self._catalog([], raw='[' * 900000)
        self.assertEqual(self._models(), native_muse.FALLBACK_MODELS)
        rows = [{'model_id': 'a-1', 'visibility': 'visible', 'release_date': 'zzz'},
                {'model_id': 'b-1', 'visibility': 'visible', 'release_date': '2026-01-01'}]
        self._catalog(rows, name='b.json')
        self.assertEqual(self._models()[0], 'b-1')

    def test_broken_or_missing_catalog_uses_fallback(self):
        self.assertEqual(self._models(), native_muse.FALLBACK_MODELS)
        self._catalog([], raw='{broken')
        self.assertEqual(self._models(), native_muse.FALLBACK_MODELS)

    def test_catalog_symlink_is_not_followed(self):
        self._catalog([{'model_id': 'real-1', 'visibility': 'visible'}], name='real.txt')
        folder = self.root / 'muse' / 'model-catalog'
        (folder / 'link.json').symlink_to(folder / 'real.txt')
        self.assertEqual(self._models(), native_muse.FALLBACK_MODELS)

    def test_selectable_agent_with_its_own_cli_name(self):
        self.assertEqual(harness.AGENTS['muse'], 'Muse Code')
        self.assertNotIn('muse', harness.CLI_NAME)
        with patch.dict(os.environ, {'CODEX_HOME': 'keep'}), harness.agent_profile('muse') as cli:
            self.assertEqual(cli, 'muse')
            self.assertEqual(os.environ['CODEX_HOME'], 'keep')  # Muse never inherits a Codex profile

    def test_version_is_read_from_the_muse_code_banner(self):
        tool = {'installation': 'installed', 'binary': '/native/muse', 'version': '1.4.14'}
        done = subprocess.CompletedProcess([], 0, 'Muse Code 1.4.14\n', '')
        with patch.object(agent_runtime.subprocess, 'run', return_value=done):
            self.assertEqual(agent_runtime.check_version('muse', tool), '1.4.14')
        bad = subprocess.CompletedProcess([], 0, '1.4.14\n', '')
        with patch.object(agent_runtime.subprocess, 'run', return_value=bad), \
                self.assertRaisesRegex(agent_runtime.AgentError, 'versión'):
            agent_runtime.check_version('muse', tool)

    def test_home_is_config_muse_and_honors_absolute_xdg(self):
        with patch.dict(os.environ, {'HOME': str(self.root)}, clear=False):
            os.environ.pop('XDG_CONFIG_HOME', None)
            self.assertEqual(native_plugins.agent_home('muse'), self.root / '.config' / 'muse')
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root / 'xdg')}):
            self.assertEqual(native_plugins.agent_home('muse'), self.root / 'xdg' / 'muse')
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': 'relative'}), self.assertRaises(native_plugins.PluginError):
            native_plugins.agent_home('muse')

    def test_terminal_launch_is_plain_and_clears_other_profiles(self):
        with patch.object(native_launcher.shutil, 'which', return_value='/usr/bin/omarchy'), \
                patch.object(native_launcher.subprocess, 'Popen') as popen:
            native_launcher.launch('/native/muse', 'muse', self.root)
        args = popen.call_args.args[0]
        self.assertEqual(args[-1], '/native/muse')
        self.assertIn('CODEX_HOME', args)  # only after -u
        self.assertFalse(any(item.startswith(('CODEX_HOME=', 'CLAUDE_CONFIG_DIR=')) for item in args))

    def test_authentication_opens_the_native_login(self):
        with patch.object(native_launcher.shutil, 'which', return_value='/usr/bin/omarchy'), \
                patch.object(native_launcher.subprocess, 'Popen') as popen:
            native_launcher.authenticate('/native/muse', 'muse', self.root)
        self.assertEqual(popen.call_args.args[0][-2:], ['/native/muse', 'login'])

    def test_context_policy_adds_no_foreign_flags_and_launch_stays_possible(self):
        policy = context_policy.resolve({}, 'jefe')
        self.assertEqual(context_policy.launch_options('muse', policy), ([], []))
        self.assertEqual(native_launcher.invocation('muse', config={}, role='jefe'), ([], []))

    def test_fresh_worker_refuses_resume_and_uses_no_session_log(self):
        run = importlib.import_module('context_run')
        prepared, _, capsule = run.prepare(MUSE_EXEC, 'muse', 'review', {}, self.root)
        self.assertIsNone(capsule)
        self.assertEqual(prepared[:3], ['muse', 'exec', '--no-session-log'])
        self.assertEqual(prepared.count('--no-session-log'), 1)
        again, _, _ = run.prepare(prepared, 'muse', 'review', {}, self.root)
        self.assertEqual(again.count('--no-session-log'), 1)
        for args in (['resume', '--last'], ['exec', '--session-id', 'x', 'task'], ['exec', '--session-id=x', 'task']):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, 'reanudar'):
                run.prepare(['muse', *args], 'muse', 'review', {}, self.root)

    def test_report_without_capsule_is_bounded_not_a_crash(self):
        run = importlib.import_module('context_run')
        small = dict(status='ok', references='a', tests='', error='', pending='')
        self.assertIs(run._bounded(small, None), small)
        big = dict(small, error='x' * 20000)
        out = run._bounded(big, None)
        self.assertEqual(out['status'], 'failed')
        self.assertEqual(out['error'], 'El informe del obrero excede el límite y no hay ECC donde guardarlo; pide uno más corto.')
        self.assertEqual(out['pending'], 'Repetir el encargo pidiendo un informe más corto.')
        self.assertLessEqual(len(json.dumps(out).encode()), 3200)

    def test_team_offers_muse_manually_but_never_recommends_it(self):
        self.assertIn('muse', team.families({'muse'}))
        rows = [row for row in team.options({'muse'}, {}, [], False) if row['agent'] == 'muse']
        self.assertTrue(rows)
        self.assertFalse(rows[0]['paid'])
        self.assertIn('high', rows[0]['efforts'])
        self.assertEqual(team.recommend({'muse'}, {}, [])[0], {})
        roles, _ = team.recommend({'claude', 'muse'}, {}, [])
        self.assertTrue(all(row['agent'] != 'muse' for row in roles.values()))
        config = {}
        team.set_role(config, 'review', 'muse', 'default', 'high', selected={'muse'}, catalog={}, free_pi=[], paid_ok=False)
        self.assertEqual(config['team']['roles']['review']['agent'], 'muse')

    def test_review_and_simple_use_muse_exec_and_writing_roles_stay_pending(self):
        row = dict(agent='muse', model='chosen', effort='high')
        for key in ('review', 'simple'):
            command = native_team.role_commands(key, row, ['claude'])[0]
            self.assertIn("muse exec --approval-mode untrusted --model chosen --reasoning-effort high '<encargo>'", command)
            self.assertIn('context_run.py', command)
            self.assertIn('--agent muse', command)
        default = native_team.role_commands('review', dict(agent='muse', model='default', effort=''), ['claude'])[0]
        self.assertIn("muse exec --approval-mode untrusted '<encargo>'", default)
        code = native_team.role_commands('code', row, ['claude'])[0]
        self.assertIn("muse exec --approval-mode on-request --workspace '<dir>' --model chosen --reasoning-effort high '<encargo>'", code)
        self.assertNotIn('--yolo', code)
        self.assertTrue(native_team.role_commands('plan', row, ['claude'])[0].startswith('# Muse'))

    def test_components_are_explicitly_unsupported(self):
        for key in ('memory', 'superpowers', 'local-tools', 'team'):
            state = native_muse.inspect_component(key, {})
            self.assertTrue(state['unsupported'])
            self.assertFalse(state['enabled'] or state['verified'])
            self.assertTrue(state['detail'])
        with self.assertRaises(native_plugins.PluginError):
            native_muse.apply_component('memory', '/native/muse', 'enable')

    def test_ai_check_skips_muse_without_running_it(self):
        import ai_check
        snapshot = {'agents': [dict(id='muse', name='Muse Code', enabled=True, installation='installed', binary='/native/muse')]}
        with patch.object(ai_check.subprocess, 'run') as run:
            result = ai_check.run({}, snapshot)['results'][0]
        run.assert_not_called()
        self.assertTrue(result['skipped'])

    def _usage(self, out, **agent):
        fake = self.root / 'muse'
        fake.write_text('#!/bin/sh\n')
        fake.chmod(0o755)
        row = dict(id='muse', enabled=True, installation='installed', binary=str(fake), **agent)
        with patch.dict(os.environ, {'XDG_CACHE_HOME': str(self.root / 'cache')}), \
                patch.object(usage_summary, '_muse_fetch', return_value=out) as fetch:
            first = usage_summary.rows([row])
            usage_summary.rows([row])
        return first, fetch

    def test_usage_comes_from_muse_usage_json(self):
        out = json.dumps({'allowance': {'tier': 'Everyday', 'weekly': {'usedPercent': 1, 'resetsAtMs': 4102444800000},
                                        'window': {'usedPercent': 5, 'resetsAtMs': 4102444800000}}})
        rows, fetch = self._usage(out)
        self.assertEqual(fetch.call_count, 1)  # second call served from the cache file
        self.assertEqual((rows[0]['name'], rows[0]['five'], rows[0]['weekly'], rows[0]['tier']), ('Muse', 5, 1, 'Everyday'))
        self.assertEqual(len(rows[0]['windows']), 2)

    def test_bad_muse_usage_keeps_an_empty_row_and_bad_values_are_dropped(self):
        for out in (None, 'broken', '[' * 5000, json.dumps({'allowance': {'window': {'usedPercent': 'x'}, 'weekly': {'usedPercent': float('nan')}}}),
                    json.dumps({'allowance': {'window': {'usedPercent': 101, 'resetsAtMs': 'z'}}})):
            with self.subTest(out=out[:10] if out else out):
                rows, _ = self._usage(out)
                self.assertEqual((rows[0]['five'], rows[0]['weekly'], rows[0]['windows']), (None, None, []))
                (self.root / 'cache' / 'omarchy-ai-team' / 'muse-usage.json').unlink(missing_ok=True)

    def test_muse_usage_never_runs_an_unverified_binary(self):
        with patch.object(usage_summary, '_muse_fetch') as fetch:
            for row in (dict(id='muse', enabled=True, installation='installed', binary='muse'),
                        dict(id='muse', enabled=True, installation='missing', binary='/bin/true'),
                        dict(id='muse', enabled=False, installation='installed', binary='/bin/true')):
                self.assertEqual(usage_summary.rows([row]), [])
        fetch.assert_not_called()

    def test_translations_cover_every_new_text(self):
        table = json.loads((Path(__file__).resolve().parents[1] / 'translations' / 'en.json').read_text())
        for source in native_muse.TEXTS:
            self.assertIn(getattr(source, 'source', source), table)


class MuseTotalTokensTests(unittest.TestCase):
    def setUp(self):
        cache = Path.home() / '.cache'
        cache.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=cache)  # outside /tmp, as required for these fixtures
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sessions = self.root / 'data' / 'muse' / 'sessions'
        env = {'XDG_DATA_HOME': str(self.root / 'data'), 'XDG_STATE_HOME': str(self.root / 'state')}
        patcher = patch.dict(os.environ, env); patcher.start(); self.addCleanup(patcher.stop)

    @staticmethod
    def _line(recorded, usage, kind='model_completed'):
        return json.dumps({'recorded_at': recorded, 'payload': {'event': {'kind': kind, 'usage': usage}}})

    def _session(self, relative, *lines):
        path = self.sessions / relative / 'session.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(lines) + '\n')
        return path

    def _usage(self, **overrides):
        usage = {'input_tokens': 10, 'output_tokens': 20, 'cache_read_tokens': 3, 'cache_write_tokens': 4, 'reasoning_tokens': 5}
        usage.update(overrides)
        return usage

    def test_muse_tokens_cover_last_30_days_including_subagents(self):
        now = int(time.time() * 1_000_000)
        day = 86_400 * 1_000_000
        self._session('2026/a', self._line(now - day, self._usage()),
                      self._line(now - day, self._usage(), kind='goal_usage_attribution'),
                      '{broken', self._line('soon', self._usage()),
                      self._line(now - day, self._usage(input_tokens=True, output_tokens=1.5, cache_read_tokens='3', cache_write_tokens=-1)))
        self._session('2026/a/subagent/b', self._line(now - 29 * day, self._usage()))
        self._session('2026/old', self._line(now - 31 * day, self._usage()))
        agents = [{'id': 'muse', 'enabled': True}]
        self.assertEqual(usage_summary.total_tokens(agents), 42 + 42 + 5)

    def test_muse_tokens_add_to_breakdown_and_need_enabled(self):
        now = int(time.time() * 1_000_000)
        self._session('2026/a', self._line(now, self._usage()))
        breakdown = self.root / 'state' / 'omarchy' / 'agents' / 'breakdown'
        breakdown.mkdir(parents=True)
        (breakdown / 'claude.json').write_text(json.dumps({'schemaVersion': 1, 'byKind': {'input': 1, 'output': 1, 'cacheRead': 1, 'cacheWrite': 1},
                                                            'byProject': [], 'byTool': [], 'costUsd': None, 'unpricedModels': []}))
        both = [{'id': 'claude', 'enabled': True}, {'id': 'muse', 'enabled': True}]
        self.assertEqual(usage_summary.total_tokens(both), 4 + 42)
        self.assertEqual(usage_summary.total_tokens([{'id': 'muse', 'enabled': False}]), None)
        self.assertEqual(usage_summary.total_tokens([{'id': 'claude', 'enabled': True}, {'id': 'muse', 'enabled': False}]), 4)

    def test_muse_relative_xdg_data_home_is_ignored(self):
        self._session('2026/a', self._line(int(time.time() * 1_000_000), self._usage()))
        with patch.dict(os.environ, {'XDG_DATA_HOME': 'data', 'HOME': str(self.root / 'nohome')}):
            self.assertIsNone(usage_summary.total_tokens([{'id': 'muse', 'enabled': True}]))

    def test_muse_symlinked_session_is_not_followed_and_missing_is_none(self):
        outside = self.root / 'outside.jsonl'
        outside.write_text(self._line(int(time.time() * 1_000_000), self._usage()) + '\n')
        folder = self.sessions / '2026' / 'linked'
        folder.mkdir(parents=True)
        (folder / 'session.jsonl').symlink_to(outside)
        self.assertIsNone(usage_summary.total_tokens([{'id': 'muse', 'enabled': True}]))


if __name__ == '__main__':
    unittest.main()
