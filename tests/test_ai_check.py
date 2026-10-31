import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import harness
import ai_check
import memory_mcp
import shared_memory


class AiCheckTests(unittest.TestCase):
    def test_team_check_can_read_only_its_own_skill(self):
        wanted = dict(skills=[], tools=False, memory=False, codeModel='opus')
        args = ai_check.command(dict(binary='claude', id='claude'), 'haiku', wanted)
        extra = [item for item in args if item.startswith(('Skill(', 'Read('))]
        self.assertEqual(len(extra), 2)
        self.assertEqual(extra[0], 'Skill(ai-team-roles)')
        self.assertTrue(extra[1].endswith('/SKILL.md)'))
        self.assertNotIn('Skill', [item for item in args if item not in extra])

    def test_code_model_accepts_descriptive_answer_but_not_another_model(self):
        wanted = dict(skills=[], tools=False, memory=False, codeModel='opus')
        self.assertEqual(ai_check.compare(wanted, {'codeModel': 'claude opus high'}, 'x'), [])
        self.assertEqual(ai_check.compare(wanted, {'codeModel': 'claude · opus · high'}, 'x'), [])
        failures = ai_check.compare(wanted, {'codeModel': 'claude sonnet high'}, 'x')
        self.assertIn('esperado opus, visto claude sonnet high', failures[0])
        self.assertTrue(ai_check.compare(wanted, {}, 'x'))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {
            'HOME': str(self.root), 'PATH': '', 'XDG_CONFIG_HOME': str(self.root / 'config'),
            'XDG_DATA_HOME': str(self.root / 'data'), 'XDG_STATE_HOME': str(self.root / 'state'),
            'CODEX_HOME': str(self.root / '.codex'), 'CLAUDE_CONFIG_DIR': str(self.root / '.claude'),
            'PI_CODING_AGENT_DIR': str(self.root / '.pi/agent')}, clear=True)
        environment.start(); self.addCleanup(environment.stop)
        self.config = {'schemaVersion': 1, 'agents': {'claude': True, 'codex-2': True},
                       'team': {'enabled': True, 'roles': {}}, 'local-toolsEnabled': True,
                       'teamModels': {'codex': [{'id': 'gpt-6-luna'}, {'id': 'gpt-6.2-luna'}]}}
        self.snapshot = {
            'agents': [dict(id=agent, name=name, enabled=True, installation='installed', binary='/fake/' + agent)
                       for agent, name in (('claude', 'Claude Code'), ('codex-2', 'Codex-2'))],
            'components': [dict(id=key, enabled=True, targets=[
                dict(agent=agent, enabled=True, verified=True) for agent in ('claude', 'codex-2')])
                for key in ('local-tools', 'memory', 'team')],
            'team': {'enabled': True, 'roles': {'code': {'model': 'opus'}}}}
        self.calls = []; self.projects = []; self.stores = []
        self.report = None; self.error = None; self.envelope = None
        runner = patch.object(ai_check.subprocess, 'run', side_effect=self.fake_run)
        self.runner = runner.start(); self.addCleanup(runner.stop)

    def fake_run(self, args, **kwargs):
        cwd = Path(kwargs['cwd'])
        if args[0] == 'git':
            if args[1] == 'init':
                (cwd / '.git').mkdir()
                return subprocess.CompletedProcess(args, 0, '', '')
            return subprocess.CompletedProcess(args, 0, str(cwd / '.git') + '\n', '')
        self.calls.append((args, kwargs)); self.projects.append(cwd)
        store = memory_mcp.project_store(cwd, shared_memory.data_root() / 'memory')
        store.mkdir(parents=True); (store / 'marker.md').write_text('synthetic')
        self.stores.append(store)
        if self.error: raise self.error
        prompt = kwargs.get('input') or args[-1]
        # The random marker is sent as a structured final line in the fixed prompt.
        request = json.loads(prompt.splitlines()[-1])
        report = self.report if self.report is not None else dict(
            skills=['ai-team-local-tools', 'ai-team-roles'], rgVersion='ripgrep 15.2.0',
            memoryMarker=request['marker'], codeModel='opus')
        if self.envelope == 'claude':
            output = json.dumps({'type': 'result', 'result': json.dumps(report),
                                 'usage': {'input_tokens': 11, 'output_tokens': 7}, 'total_cost_usd': 0.001})
        elif self.envelope == 'codex':
            output = '\n'.join(map(json.dumps, [
                {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(report)}},
                {'type': 'turn.completed', 'usage': {'input_tokens': 20, 'output_tokens': 9}}]))
        elif self.envelope == 'pi':
            output = json.dumps({'type': 'message_end', 'message': {'role': 'assistant',
                'content': [{'type': 'text', 'text': json.dumps(report)}],
                'usage': {'input': 12, 'output': 8, 'cost': {'total': 0}}}})
        elif self.envelope == 'invalid': output = 'not JSON'
        elif self.envelope == 'failed-turn':
            output = json.dumps(report) + '\n' + json.dumps({'type': 'turn.failed', 'error': {'message': 'private'}})
        elif self.envelope == 'claude-error':
            output = json.dumps({'type': 'result', 'is_error': True, 'result': json.dumps(report)})
        elif self.envelope == 'pi-error':
            output = json.dumps({'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': 'error',
                'content': [{'type': 'text', 'text': json.dumps(report)}]}})
        elif self.envelope == 'malformed-message':
            output = json.dumps({'type': 'message_end', 'message': None})
        elif self.envelope == 'malformed-item':
            output = json.dumps({'type': 'item.completed', 'item': None})
        elif self.envelope == 'malformed-content':
            output = json.dumps({'type': 'message_end', 'message': {'role': 'assistant', 'content': [None]}})
        elif self.envelope == 'failure-usage':
            output = json.dumps({'type': 'result', 'is_error': True, 'usage': {'input_tokens': 3, 'output_tokens': 2},
                                 'total_cost_usd': 0.0001})
            return subprocess.CompletedProcess(args, 1, output, 'private error')
        elif self.envelope == 'exit': return subprocess.CompletedProcess(args, 1, '', 'private error')
        else: output = 'progress\n' + json.dumps(report) + '\n'
        return subprocess.CompletedProcess(args, 0, output, '')

    def run_check(self):
        return ai_check.run(self.config, self.snapshot)

    def assert_cleaned(self):
        self.assertTrue(self.projects)
        self.assertTrue(all(not path.exists() for path in self.projects + self.stores))

    def test_native_commands_permissions_profiles_and_timeout(self):
        result = self.run_check()
        self.assertTrue(all(row['ok'] for row in result['results']), result)
        claude, codex = self.calls
        args, options = claude
        self.assertEqual(args[args.index('--model') + 1], 'haiku')
        self.assertEqual(args[args.index('--effort') + 1], 'low')
        allowed = [item for item in args[args.index('--allowedTools') + 1:] if not item.startswith(('Skill(', 'Read('))]
        self.assertEqual(allowed, ['Bash(rg --version)', 'mcp__ai-team-memory__memory_save',
                                   'mcp__ai-team-memory__memory_search'])
        args, options = codex
        self.assertEqual(args[1], 'exec')
        self.assertEqual(args[args.index('-s') + 1], 'read-only')
        self.assertEqual(args[args.index('-m') + 1], 'gpt-6.2-luna')
        self.assertIn('model_reasoning_effort="low"', args)
        self.assertIn('mcp_servers.ai-team-memory.default_tools_approval_mode="approve"', args)
        self.assertEqual(options['env']['CODEX_HOME'], str(self.root / '.codex-2'))
        for args, options in self.calls:
            self.assertEqual(options['timeout'], 180)
            self.assertFalse(options.get('shell', False))
            self.assertTrue((options['input']).strip())
            self.assertNotIn('danger-full-access', ' '.join(args))
        self.assert_cleaned()

    def test_expected_vs_reported_lists_all_failures_in_spanish(self):
        self.report = dict(skills=[], rgVersion='', memoryMarker='wrong', codeModel='wrong')
        result = self.run_check()
        for row in result['results']:
            self.assertFalse(row['ok'])
            detail = ' '.join(row['failures'])
            for fragment in ('ai-team-local-tools', 'ai-team-roles', 'rg --version', 'marcador', 'Programar'):
                self.assertIn(fragment, detail)
        self.assert_cleaned()

    def test_disconnected_tools_and_memory_are_not_requested(self):
        self.snapshot['components'] = []
        self.snapshot['team'] = {'enabled': False}
        self.config['team']['enabled'] = False
        self.config['local-toolsEnabled'] = False
        self.report = dict(skills=[])
        self.assertTrue(all(row['ok'] for row in self.run_check()['results']))
        claude, codex = self.calls
        self.assertNotIn('mcp_servers.ai-team-memory.default_tools_approval_mode="approve"', codex[0])
        self.assertNotIn('mcp__ai-team-memory__memory_save', claude[0])

    def test_cleanup_on_timeout_failure_and_malformed_json(self):
        for error, envelope in ((subprocess.TimeoutExpired('/fake', 180), None),
                                (OSError('synthetic'), None), (None, 'invalid'), (None, 'exit')):
            with self.subTest(error=error, envelope=envelope):
                self.error, self.envelope = error, envelope
                result = self.run_check()
                self.assertTrue(all(not row['ok'] and row['failures'] for row in result['results']))
                self.assertNotIn('private error', json.dumps(result))
                self.assert_cleaned()

    def test_native_json_envelopes_record_reported_usage_only(self):
        for envelope, agent, tokens, cost in (('claude', 'claude', 18, 0.001),
                                             ('codex', 'codex-2', 29, None), ('pi', 'pi', 20, 0)):
            with self.subTest(envelope=envelope):
                self.envelope = envelope
                self.snapshot['agents'] = [dict(id=agent, name=agent, enabled=True,
                    installation='installed', binary='/fake/' + agent)]
                self.config['piNousCatalog'] = {'models': [{'id': 'test:free', 'cost': {'input': 0, 'output': 0}}]}
                row = self.run_check()['results'][0]
                self.assertTrue(row['ok'], row)
                self.assertEqual(row['tokens'], tokens)
                self.assertEqual(row.get('cost'), cost)

    def test_failure_still_records_usage_reported_by_cli(self):
        self.envelope = 'failure-usage'
        for row in self.run_check()['results']:
            self.assertFalse(row['ok'])
            self.assertEqual(row.get('tokens'), 5)
            self.assertEqual(row.get('cost'), 0.0001)

    def test_pi_check_prefers_the_most_reliable_free_model_when_available(self):
        free = lambda name: {'id': name, 'cost': {'input': 0}}
        models = [free('upstage/solar-mini4:free'), free('reflection/beam-1.0-501b-a23b:free')]
        self.assertEqual(ai_check.pi_check_model({'piNousCatalog': {'models': models}}), 'nous/reflection/beam-1.0-501b-a23b:free')
        self.assertEqual(ai_check.pi_check_model({'piNousCatalog': {'models': models[:1]}}), 'nous/upstage/solar-mini4:free')
        self.assertIsNone(ai_check.pi_check_model({'piNousCatalog': {'models': [{'id': 'pago', 'cost': {'input': 1}}]}}))

    def test_prompt_keeps_the_marker_out_of_real_projects(self):
        prompt = ai_check.prompt_for({'tools': True, 'memory': True, 'codeModel': 'opus'}, 'x')
        self.assertIn('sin indicar project', prompt)

    def test_prompt_says_which_field_is_the_model(self):
        prompt = ai_check.prompt_for({'tools': True, 'memory': True, 'codeModel': 'opus'}, 'x')
        self.assertIn('agente · modelo · esfuerzo', prompt)
        self.assertIn('solo el campo modelo', prompt)

    def test_pi_skipped_without_verified_free_model(self):
        self.snapshot['agents'] = [dict(id='pi', name='Pi', enabled=True, installation='installed', binary='/fake/pi')]
        self.config['piPaidApproved'] = True
        self.config['agentCatalogs'] = {'pi': {'models': [{'id': 'xai/paid'}]}}
        row = self.run_check()['results'][0]
        self.assertTrue(row['skipped'])
        self.assertIn('gratuito', ' '.join(row['warnings']))
        self.assertEqual(self.calls, [])

    def test_native_error_envelopes_cannot_report_success(self):
        for envelope in ('failed-turn', 'claude-error', 'pi-error'):
            with self.subTest(envelope=envelope):
                self.envelope = envelope
                result = self.run_check()
                self.assertTrue(all(not row['ok'] and row['failures'] for row in result['results']), result)
                self.assert_cleaned()

    def test_malformed_native_envelope_is_an_agent_failure(self):
        for envelope in ('malformed-message', 'malformed-item', 'malformed-content'):
            with self.subTest(envelope=envelope):
                self.envelope = envelope
                rows = self.run_check()['results']
                self.assertEqual(len(rows), 2)
                self.assertTrue(all(not row['ok'] and row['failures'] for row in rows))
                self.assert_cleaned()

    def test_codex_accounts_use_separate_selected_profiles(self):
        self.snapshot['agents'] = [dict(id=agent, name=agent, enabled=True, installation='installed', binary='/fake/codex')
                                   for agent in ('codex-2', 'codex')]
        self.run_check()
        self.assertEqual([options['env']['CODEX_HOME'] for _, options in self.calls],
                         [str(self.root / '.codex-2'), str(self.root / '.codex')])

    def test_unselected_or_uninstalled_agents_never_run(self):
        for row in self.snapshot['agents']:
            row['enabled'] = False
        self.snapshot['agents'].append(dict(id='pi', name='Pi', enabled=True, installation='missing'))
        self.assertEqual(self.run_check()['results'], [])
        self.assertEqual(self.calls, [])

    def test_cli_command_persists_dated_results_and_status_does_no_inference(self):
        harness.save_config(self.config)
        output = io.StringIO()
        with patch.object(harness, 'status', return_value=self.snapshot), contextlib.redirect_stdout(output):
            self.assertEqual(harness.main(['ai-check']), 0)
        stored = harness.load_config()['aiCheck']
        self.assertTrue(stored['checkedAt'])
        self.assertTrue(all(row['ok'] for row in stored['results']))
        self.calls.clear()
        with patch.object(harness, 'mise_inventory', return_value=({}, '')), \
                patch.object(harness, 'agent_tool', return_value={'installation': 'missing', 'binary': '', 'version': ''}), \
                patch.object(harness, 'memory_status', return_value={'projects': []}):
            snapshot = harness.status(harness.load_config())
        self.assertEqual(snapshot['aiCheck'], stored)
        self.assertEqual(self.calls, [])
