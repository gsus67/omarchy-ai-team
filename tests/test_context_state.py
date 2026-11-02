"""ECC adapter contracts. Synthetic tests are not evidence of hook trust/execution."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import context_policy
import state_confinement


class Vault:
    def __init__(self):
        self.memories = {}
        self.calls = []
        self.project = 'synthetic'

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == 'memory_doctor':
            return {'activeProject': self.project, 'projects': ['synthetic', 'compartido']}
        if name == 'memory_save':
            key = 'mem_' + str(len(self.memories))
            memory = {**arguments, 'id': key}
            self.memories[key] = memory
            return {'memory': {k: v for k, v in memory.items() if k != 'body'}}
        if name == 'memory_read':
            return {'memory': self.memories[arguments['id']]}
        if name == 'memory_search':
            return {'results': [{'memory': m, 'excerpt': 'PRIVATE EXCERPT'}
                                for m in reversed(list(self.memories.values()))]}
        raise AssertionError(name)


class ContextStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        state_confinement.guard_writes(self, self.temp.name)
        state_confinement.isolate_env(self, self.temp.name)
        self.module = importlib.import_module('context_state')
        self.instance = self.module.instance('codex-2', 'code', Path(self.temp.name), context_policy.resolve({}, 'code'))
        self.vault = Vault()
        self.state = {'plan': ['implement'], 'decisions': [], 'files': ['backend/a.py:1'],
                      'errors': [], 'pending': ['review'], 'safe_boundary': True}

    def test_fixture_state_checkpoint_is_confined(self):
        self.module.checkpoint(self.vault, self.instance, self.state)
        folder = Path(self.temp.name)/'state/omarchy-ai-team/context-usage'/self.instance['id']
        self.assertTrue((folder/'handoff_checkpoint.json').is_file())
        with patch.dict(os.environ):
            os.environ.pop('XDG_STATE_HOME')
            self.module.checkpoint(self.vault, self.instance, self.state)
        fallback = Path(self.temp.name)/'home/.local/state/omarchy-ai-team/context-usage'/self.instance['id']
        self.assertTrue((fallback/'handoff_checkpoint.json').is_file())

    def test_checkpoint_is_saved_and_verified_through_ecc_not_local_files(self):
        result = self.module.checkpoint(self.vault, self.instance, self.state)
        self.assertEqual(result['status'], 'saved')
        self.assertEqual([c[0] for c in self.vault.calls], ['memory_doctor', 'memory_save', 'memory_read'])
        saved = self.vault.memories[result['id']]
        self.assertIn(self.instance['id'], saved['tags'])
        self.assertEqual(json.loads(saved['body'])['state'], self.state)
        self.assertEqual(saved['targetHarnesses'], ['all', 'project'])
        self.assertNotIn('project', self.vault.calls[1][1])  # cwd, never an ambiguous display name
        folder = Path(self.temp.name)/'state/omarchy-ai-team/context-usage'/self.instance['id']
        self.assertEqual([p.name for p in folder.iterdir()], ['handoff_checkpoint.json'])
        record = json.loads((folder/'handoff_checkpoint.json').read_text())
        self.assertEqual((record['schema'], record['id']), (self.module.IDLE_SCHEMA, result['id']))
        self.assertEqual(record['sha256'], hashlib.sha256(saved['body'].encode()).hexdigest())

    def test_checkpoint_rejects_incomplete_state_and_propagates_save_or_verification_errors(self):
        for invalid in ({}, {**self.state, 'plan': 'text'}, {**self.state, 'extra': 'log'}):
            with self.assertRaises(ValueError):
                self.module.checkpoint(self.vault, self.instance, invalid)
        with patch.object(self.vault, 'call', side_effect=ValueError('MEMORY_WRITE_REJECTED: suspected secret')):
            with self.assertRaisesRegex(ValueError, 'MEMORY_WRITE_REJECTED: suspected secret'):
                self.module.checkpoint(self.vault, self.instance, self.state)
        original = self.vault.call
        with patch.object(self.vault, 'call', side_effect=lambda n, a: {'memory': {'body': '{}'}} if n == 'memory_read' else original(n, a)):
            with self.assertRaisesRegex(ValueError, 'ECC_CHECKPOINT_VERIFICATION_FAILED'):
                self.module.checkpoint(self.vault, self.instance, self.state)

    def test_precompact_missing_saved_stale_and_other_instance(self):
        missing = self.module.precompact(self.vault, self.instance)
        self.assertEqual(missing['status'], 'incomplete')
        self.assertIn('CHECKPOINT_MISSING', missing['error'])
        saved = self.module.checkpoint(self.vault, self.instance, self.state)
        event = self.module.precompact(self.vault, self.instance)
        self.assertEqual(event['status'], 'saved')
        self.assertEqual(self.vault.memories[event['id']]['links'], [saved['id']])
        stale = self.module.precompact(self.vault, self.instance)
        self.assertEqual(stale['status'], 'incomplete')
        self.assertIn('CHECKPOINT_STALE', stale['error'])
        other = self.module.instance('codex-2', 'code', Path(self.temp.name), self.instance['policy'])
        self.assertEqual(self.module.precompact(self.vault, other)['status'], 'incomplete')

    def test_brief_never_reads_bodies_or_excerpts_and_no_project_never_searches(self):
        self.module.checkpoint(self.vault, self.instance, self.state)
        self.vault.memories['mem_0']['title'] = '<img src=x>\nINSTRUCTION'
        self.vault.calls.clear()
        brief = self.module.brief(self.vault)
        self.assertEqual(brief['project'], 'synthetic')
        rendered = json.dumps(brief)
        self.assertNotIn('PRIVATE EXCERPT', rendered)
        self.assertNotIn('implement', rendered)
        self.assertNotIn('<img', rendered)
        self.assertNotIn('memory_read', [c[0] for c in self.vault.calls])
        self.vault.project = None; self.vault.calls.clear()
        self.assertEqual(self.module.brief(self.vault)['status'], 'no_project')
        self.assertEqual([c[0] for c in self.vault.calls], ['memory_doctor'])

    def test_native_hooks_are_per_instance_preserve_trust_and_resolved_instructions(self):
        for agent in ('claude', 'codex-2'):
            capsule = self.module.instance(agent, 'code', Path(self.temp.name), context_policy.resolve({}, 'code', override={
                'worker_report_max_tokens': 500, 'reason': 'small report'}))
            args, env = self.module.launch_options(capsule)
            text = ' '.join(args)
            self.assertIn('PreCompact', text)
            self.assertIn('SessionStart', text)
            self.assertNotIn('bypass', text)
            self.assertNotIn('developer_instructions', text)
            self.assertIn('AI_TEAM_CONTEXT=', env[0])
            instructions = self.module.instructions(capsule)
            for fragment in ('150000', '70000', '500', 'cooperativ', 'irreversible', 'context_state.py', 'checkpoint'):
                self.assertIn(fragment, instructions)
            self.assertNotEqual(capsule['id'], self.instance['id'])

    def test_invalid_checkpoint_and_ecc_failure_are_not_reported_as_saved(self):
        saved = self.module.checkpoint(self.vault, self.instance, self.state)
        self.vault.memories[saved['id']]['body'] = json.dumps({'schema': 1, 'state': self.state,
            'instance': 'different', 'cwd': self.instance['cwd']})
        event = self.module.precompact(self.vault, self.instance)
        self.assertEqual(event['status'], 'incomplete')
        self.assertIn('CHECKPOINT_INVALID', event['error'])
        with patch.object(self.vault, 'call', side_effect=ValueError('exact ECC failure')):
            with self.assertRaisesRegex(ValueError, 'exact ECC failure'):
                self.module.precompact(self.vault, self.instance)

    def test_dump_disabled_omits_only_precompact_and_brief_errors_remain_explicit(self):
        capsule = {**self.instance, 'policy': {**self.instance['policy'], 'dump_state_before': False}}
        args, _ = self.module.launch_options(capsule)
        self.assertIn('hooks.SessionStart=', ' '.join(args))
        self.assertNotIn('hooks.PreCompact=', ' '.join(args))
        native_call = self.vault.call
        with patch.object(self.vault, 'call', side_effect=lambda n, a: native_call(n, a) if n == 'memory_doctor' else (_ for _ in ()).throw(ValueError('exact search failure'))):
            index = self.module.brief(self.vault)
        self.assertEqual(index['status'], 'pending')
        self.assertEqual(index['errors'], ['exact search failure', 'exact search failure'])

    def test_start_emits_instructions_even_when_ecc_is_unavailable(self):
        import contextlib
        import io
        output = io.StringIO()
        with patch.object(self.module, 'ECC', side_effect=ValueError('ECC unavailable')), contextlib.redirect_stdout(output):
            code = self.module.main(['start', '--instance', json.dumps(self.instance)])
        reply = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(set(reply), {'hookSpecificOutput'})
        context = reply['hookSpecificOutput']['additionalContext']
        self.assertEqual(reply['hookSpecificOutput']['hookEventName'], 'SessionStart')
        self.assertIn('cooperativ', context)
        self.assertIn('ECC unavailable', context)
        self.assertIn('pending', context)

    def test_precompact_failure_is_visible_on_native_hook_stderr(self):
        import contextlib
        import io
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(self.module, 'ECC', side_effect=ValueError('exact native ECC failure')), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(self.module.main(['precompact', '--instance', json.dumps(self.instance)]), 1)
        self.assertIn('exact native ECC failure', stderr.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'failed')

    def test_healthy_start_uses_native_hook_context_envelope_with_metadata_only(self):
        import contextlib
        import io
        self.module.checkpoint(self.vault, self.instance, self.state)
        capsule = {**self.instance, 'role': 'jefe'}
        output = io.StringIO()
        with patch.object(self.module, 'ECC', return_value=self.vault), contextlib.redirect_stdout(output):
            self.assertEqual(self.module.main(['start', '--instance', json.dumps(capsule)]), 0)
        reply = json.loads(output.getvalue())
        self.assertEqual(set(reply), {'hookSpecificOutput'})
        text = reply['hookSpecificOutput']['additionalContext']
        self.assertIn('mem_0', text)
        self.assertNotIn('PRIVATE EXCERPT', text)
        self.assertNotIn('backend/a.py:1', text)

    def test_launcher_injects_frozen_cwd_and_fresh_instance_and_rejects_native_disable(self):
        import native_launcher
        with patch.object(native_launcher.shutil, 'which', return_value='/omarchy'), patch.object(native_launcher.subprocess, 'Popen') as spawn:
            for _ in range(2):
                native_launcher.launch('/codex', 'codex-2', Path(self.temp.name), config={'memoryEnabled': True, 'agents': {'codex-2': True}}, role='review')
                args = spawn.call_args.args[0]
                capsule = json.loads(next(x.split('=', 1)[1] for x in args if x.startswith('AI_TEAM_CONTEXT=')))
                self.assertEqual(capsule['cwd'], str(Path.cwd()))
                self.assertEqual(capsule['role'], 'review')
                self.assertIn('hooks.PreCompact=', ' '.join(args))
            with patch.dict(os.environ, {'DISABLE_AUTO_COMPACT': '1'}), self.assertRaisesRegex(ValueError, 'DISABLE_AUTO_COMPACT'):
                native_launcher.launch('/claude', 'claude', Path(self.temp.name), config={})

    def test_default_cwd_follows_the_active_terminal_like_omarchy_does(self):
        import native_launcher
        active = Path(self.temp.name)/'active'; active.mkdir()
        done = type('Done', (), {'returncode': 0, 'stdout': str(active)+'\n'})()
        with patch.object(native_launcher.shutil, 'which', return_value='/omarchy'), \
                patch.object(native_launcher.subprocess, 'run', return_value=done) as ask, \
                patch.object(native_launcher.subprocess, 'Popen') as spawn:
            native_launcher.launch('/codex', 'codex-2', Path(self.temp.name), config={})
            args = spawn.call_args.args[0]
            capsule = json.loads(next(x.split('=', 1)[1] for x in args if x.startswith('AI_TEAM_CONTEXT=')))
            self.assertEqual(capsule['cwd'], str(active))
            self.assertEqual(ask.call_args.args[0], ['/omarchy'])
            done.stdout = '/does/not/exist\n'
            native_launcher.launch('/codex', 'codex-2', Path(self.temp.name), config={})
            args = spawn.call_args.args[0]
            capsule = json.loads(next(x.split('=', 1)[1] for x in args if x.startswith('AI_TEAM_CONTEXT=')))
            self.assertEqual(capsule['cwd'], str(Path.cwd()))

    def test_launcher_capsule_cwd_is_the_directory_the_terminal_opens_in(self):
        import native_launcher
        project = Path(self.temp.name)/'project'; project.mkdir()
        elsewhere = Path(self.temp.name)/'elsewhere'; elsewhere.mkdir()
        before = os.getcwd()
        os.chdir(elsewhere)
        try:
            with patch.object(native_launcher.shutil, 'which', return_value='/omarchy'), patch.object(native_launcher.subprocess, 'Popen') as spawn:
                native_launcher.launch('/codex', 'codex-2', Path(self.temp.name), config={}, cwd=project)
                args = spawn.call_args.args[0]
                capsule = json.loads(next(x.split('=', 1)[1] for x in args if x.startswith('AI_TEAM_CONTEXT=')))
                self.assertEqual(capsule['cwd'], str(project))
                self.assertEqual(args[args.index('/usr/bin/env') + 1:args.index('/usr/bin/env') + 3], ['-C', str(project)])
                native_launcher.launch('/codex', 'codex-2', Path(self.temp.name), config={})
                args = spawn.call_args.args[0]
                capsule = json.loads(next(x.split('=', 1)[1] for x in args if x.startswith('AI_TEAM_CONTEXT=')))
                self.assertEqual(capsule['cwd'], str(elsewhere))
                self.assertEqual(args[args.index('/usr/bin/env') + 1:args.index('/usr/bin/env') + 3], ['-C', str(elsewhere)])
                native_launcher.launch('/codex', 'codex-2', Path(self.temp.name), cwd=project)
                self.assertNotIn('-C', spawn.call_args.args[0])
        finally:
            os.chdir(before)

    def test_wayland_smoke_contract_accepts_extra_hooks_but_rejects_wrong_profile(self):
        import importlib.util
        import native_launcher
        spec = importlib.util.spec_from_file_location('context_preview', Path(__file__).resolve().parents[1]/'tools/preview.py')
        preview = importlib.util.module_from_spec(spec); spec.loader.exec_module(preview)
        home = Path(self.temp.name)
        with patch.object(native_launcher.shutil, 'which', return_value='/omarchy'), patch.object(native_launcher.subprocess, 'Popen') as spawn:
            native_launcher.launch(str(home/'bin/codex'), 'codex-2', home/'.codex-2', config={})
            request = spawn.call_args.args[0][1:]
        self.assertTrue(preview.valid_terminal_request(request, home, home/'bin'))
        invalid = [x.replace('CODEX_HOME='+str(home/'.codex-2'), 'CODEX_HOME=/wrong') for x in request]
        self.assertFalse(preview.valid_terminal_request(invalid, home, home/'bin'))

    def test_mcp_checks_jsonrpc_and_tool_errors_exactly(self):
        runtime = {'installed': True, 'node': '/node', 'path': '/ecc'}
        client = self.module.ECC(self.instance, runtime=runtime)
        for reply, message in [({'error': {'code': -1, 'message': 'native rpc error'}}, 'native rpc error'),
                               ({'result': {'isError': True, 'content': [{'type': 'text', 'text': 'MEMORY_WRITE_REJECTED'}]}}, 'MEMORY_WRITE_REJECTED')]:
            output = json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {}})+'\n'+json.dumps({'jsonrpc': '2.0', 'id': 2, **reply})+'\n'
            with patch.object(self.module.subprocess, 'run') as run:
                run.return_value.returncode = 0; run.return_value.stdout = output
                with self.assertRaisesRegex(ValueError, message): client.call('memory_save', {})
                self.assertEqual(run.call_args.kwargs['cwd'], self.instance['cwd'])
                self.assertIn('notifications/initialized', run.call_args.kwargs['input'])


if __name__ == '__main__': unittest.main()

class RealECCContextTests(unittest.TestCase):
    def test_installed_official_ecc_roundtrip_in_temporary_store(self):
        import shared_memory
        import context_state
        runtime = shared_memory.inspect_runtime()
        if not runtime['installed'] or not runtime['node']:
            self.skipTest('Official ECC runtime not installed; no installation permitted')
        with tempfile.TemporaryDirectory(prefix='ai-team-context-ecc-') as directory:
            root = Path(directory)
            state_confinement.guard_writes(self, root)  # before any mkdir/checkpoint/precompact
            state_confinement.isolate_env(self, root)   # HOME and every XDG base inside the temporary directory
            (root/'home').mkdir(); (root/'project').mkdir()
            command = shared_memory.mcp_command
            capsule = context_state.instance('codex-2', 'code', root/'project', context_policy.resolve({}, 'code'))
            with patch.object(shared_memory, 'mcp_command', side_effect=lambda r, a: command(r, a, root/'store')):
                client = context_state.ECC(capsule, runtime=runtime)
                saved = context_state.checkpoint(client, capsule, {
                    'plan': ['synthetic checkpoint'], 'decisions': [], 'files': [], 'errors': [], 'pending': []})
                event = context_state.precompact(client, capsule)
                self.assertEqual(saved['status'], 'saved')
                self.assertEqual(event['status'], 'saved')
                self.assertEqual(event['checkpoint'], saved['id'])
                self.assertEqual(context_state.precompact(client, capsule)['status'], 'incomplete')
                index = context_state.brief(client)
                self.assertNotIn('synthetic checkpoint', json.dumps(index))

class RealECCConfinementTests(unittest.TestCase):
    """Deterministic CI regression for RealECCContextTests: no Node, network or installed ECC is involved."""

    def test_roundtrip_confines_inherited_xdg_state(self):
        import atomic_files
        import shared_memory
        import context_state
        outer = tempfile.TemporaryDirectory(); self.addCleanup(outer.cleanup)
        outer_root = Path(outer.name)
        created, outside, written = [], [], []

        class Recorder(tempfile.TemporaryDirectory):  # remembers the fixture's own temporary directory
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                created.append(Path(self.name))

        def inside(path):
            return bool(created) and Path(os.path.abspath(path)).is_relative_to(created[-1])

        real_mkdir, real_write = Path.mkdir, atomic_files.atomic_write

        def mkdir(path, *args, **kwargs):  # independent of the fixture's own guard; checks before any I/O
            if not inside(path):
                outside.append(str(path))
                raise AssertionError(f'mkdir outside the fixture: {path}')
            return real_mkdir(path, *args, **kwargs)

        def write(destination, content):
            if not inside(destination):
                outside.append(str(destination))
                raise AssertionError(f'write outside the fixture: {destination}')
            written.append(Path(destination))
            return real_write(destination, content)

        inherited = {name: str(outer_root / ('inherited-' + name.lower())) for name in (
            'HOME', 'XDG_STATE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME')}
        vault = Vault()
        case = RealECCContextTests('test_installed_official_ecc_roundtrip_in_temporary_store')
        result = unittest.TestResult()
        with patch.dict(os.environ, inherited), patch.object(tempfile, 'tempdir', str(outer_root)), \
                patch.object(tempfile, 'TemporaryDirectory', Recorder), \
                patch.object(shared_memory, 'inspect_runtime', return_value={'installed': True, 'node': '/synthetic/node'}), \
                patch.object(context_state, 'ECC', lambda capsule, runtime=None: vault), \
                patch.object(Path, 'mkdir', mkdir), patch.object(atomic_files, 'atomic_write', write):
            environment = dict(os.environ)
            case.run(result)
            self.assertEqual(dict(os.environ), environment)  # the fixture's cleanups restored what they changed
        self.assertEqual(outside, [])
        self.assertEqual((result.errors, result.failures, result.skipped), ([], [], []))
        self.assertEqual(result.testsRun, 1)
        self.assertEqual({path.name for path in written}, {'handoff_checkpoint.json', 'handoff_precompact.json'})
        for path in written:
            self.assertEqual(path.relative_to(created[-1]).parts[:3], ('state', 'omarchy-ai-team', 'context-usage'))


class ContextActionTests(unittest.TestCase):
    def test_explicit_harness_actions_delegate_without_status_or_profile_writes(self):
        import harness
        import context_state
        for command, action in [('context-checkpoint', 'checkpoint'), ('context-brief', 'brief'), ('context-precompact', 'precompact')]:
            with patch.object(context_state, 'main', return_value=0) as entry, patch.object(harness, 'load_config', side_effect=AssertionError('unnecessary global config read')):
                self.assertEqual(harness._main([command, '--instance', '{}']), 0)
                entry.assert_called_once_with([action, '--instance', '{}'])
