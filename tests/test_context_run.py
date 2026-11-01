"""Exercise report transport with real synthetic child processes, no model inference."""
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import native_team

REPORT = {'status': 'pending review', 'references': 'backend/a.py:1', 'tests': '1 passed', 'error': '', 'pending': 'review'}


class ContextRunTests(unittest.TestCase):
    def setUp(self):
        self.run = importlib.import_module('context_run')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fake(self, text):
        path = self.root/'native'
        path.write_text('#!'+sys.executable+'\n'+text)
        path.chmod(0o700)
        return str(path)

    def test_standalone_bridges_translate_visible_errors_in_system_language(self):
        import subprocess
        import context_state
        import context_policy
        backend = Path(__file__).resolve().parents[1]/'backend'
        capsule = context_state.instance('codex', 'review', self.root, context_policy.resolve({}, 'review'))
        env = {**os.environ, 'HOME': str(self.root), 'LC_ALL': 'en_US.UTF-8', 'PATH': '',
               'XDG_DATA_HOME': str(self.root/'data'), 'XDG_CONFIG_HOME': str(self.root/'config')}
        for script, args, fragment in [
            ('context_run.py', ['--agent', 'codex', '--role', 'review', '--cwd', str(self.root)], 'native worker command'),
            ('context_state.py', ['checkpoint', '--instance', json.dumps(capsule)], 'ECC is unavailable')]:
            result = subprocess.run([sys.executable, str(backend/script), *args], env=env, input='{}', capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertIn(fragment, json.loads(result.stdout)['error'])

    def test_codex_transport_discards_tool_and_stderr_events_but_keeps_five_final_fields(self):
        fake = self.fake('import json,sys\nprint("PRIVATE STDERR",file=sys.stderr)\n'
            'print(json.dumps({"type":"item.completed","item":{"type":"command_execution","output":"PRIVATE TOOL"}}))\n'
            'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":'+repr(json.dumps(REPORT))+'}}))\n'
            'print(json.dumps({"type":"turn.completed"}))\n')
        output = io.StringIO()
        code = self.run.run([fake, 'exec', '-s', 'read-only', '-m', 'chosen', 'task'], 'codex-2', 'review', {}, self.root, output=output)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), REPORT)
        self.assertNotIn('PRIVATE', output.getvalue())

    def test_claude_native_structured_result_and_fatal_event_only(self):
        for agent, text, expected in [('claude', {'type': 'result', 'structured_output': REPORT, 'is_error': False}, 0),
                                      ('codex', {'type': 'error', 'message': 'literal native fatal'}, 7)]:
            fake = self.fake('import json,sys\nprint('+repr(json.dumps(text))+')\nsys.exit('+str(expected)+')\n')
            out = io.StringIO()
            result = self.run.run([fake, 'exec' if agent == 'codex' else '-p', 'task'], agent, 'review', {}, self.root, output=out)
            self.assertEqual(result, expected)
            reply = json.loads(out.getvalue())
            self.assertEqual(set(reply), set(REPORT))
            if expected: self.assertIn('literal native fatal', reply['error'])
            else: self.assertEqual(reply, REPORT)

    def test_oversized_final_report_is_referenced_without_forwarding_body(self):
        oversized = {**REPORT, 'references': 'PRIVATE BODY '*2000}
        fake = self.fake('import json\nprint('+repr(json.dumps({'type': 'result', 'structured_output': oversized}))+')\n')
        out = io.StringIO()
        with patch.object(self.run, 'save_report', return_value='mem_report_pointer') as save:
            code = self.run.run([fake, '-p', 'task'], 'claude', 'code', {}, self.root, output=out)
        self.assertEqual(code, 0)
        self.assertEqual(save.call_args.args[1], oversized)
        self.assertIn('mem_report_pointer', out.getvalue())
        self.assertNotIn('PRIVATE BODY', out.getvalue())

    def test_malformed_or_missing_final_does_not_relay_raw_outputs(self):
        fake = self.fake('print("PRIVATE RAW NON JSON")\n')
        out = io.StringIO()
        self.assertEqual(self.run.run([fake, 'exec', 'task'], 'codex', 'simple', {}, self.root, output=out), 1)
        self.assertNotIn('PRIVATE', out.getvalue())
        self.assertEqual(set(json.loads(out.getvalue())), set(REPORT))

    def test_later_invalid_final_and_failed_turn_cannot_reuse_an_earlier_report(self):
        valid = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(REPORT)}}
        later_events = [
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'INVALID FINAL'}},
            {'type': 'turn.failed', 'error': {'message': 'literal failed turn'}}]
        for later in later_events:
            fake = self.fake('print('+repr(json.dumps(valid))+')\nprint('+repr(json.dumps(later))+')\n')
            out = io.StringIO()
            self.assertEqual(self.run.run([fake, 'exec', 'task'], 'codex', 'review', {}, self.root, output=out), 1)
            self.assertNotEqual(json.loads(out.getvalue()), REPORT)

    def test_disabled_passes_exact_native_arguments_and_inherited_environment(self):
        fake = self.fake('raise AssertionError("must exec")\n')
        command = [fake, 'exec', '-m', 'chosen', '-s', 'read-only', 'task']
        with patch.object(self.run.os, 'execvpe') as execute:
            self.run.run(command, 'codex', 'review', {'context': {'enabled': False}}, self.root)
            args = execute.call_args.args
            self.assertEqual(args[1], command)
            self.assertEqual(args[2], dict(os.environ))

    def test_native_flags_permissions_profile_and_same_resolver(self):
        prepared, env, capsule = self.run.prepare(['codex', 'exec', '-m', 'chosen', '-s', 'read-only', 'task'],
            'codex-2', 'review', {'context': {'roles': {'review': {'compact_at_tokens': 190000}}}}, self.root,
            override={'worker_report_max_tokens': 300, 'reason': 'small task'})
        self.assertIn('model_auto_compact_token_limit=190000', prepared)
        self.assertIn('read-only', prepared)
        self.assertIn('chosen', prepared)
        self.assertEqual(env['CODEX_HOME'], str(Path.home()/'.codex-2'))
        self.assertEqual(capsule['policy']['worker_report_max_tokens'], 300)
        self.assertEqual(capsule['cwd'], str(self.root))
        self.assertNotIn('bypass', ' '.join(prepared))

    def test_recipes_call_wrapper_with_role_settings_and_original_controls(self):
        row = {'agent': 'codex', 'model': 'chosen', 'effort': 'high'}
        line = native_team.role_commands('review', row, ['codex-2'], settings_file='/plugin/settings.json')[0]
        for text in ('context_run.py', '--role review', '--agent codex-2', '--settings /plugin/settings.json', '-s read-only', '-m chosen'):
            self.assertIn(text, line)
        self.assertNotIn('bypass', line)

    def test_small_override_budget_also_bounds_ecc_failure_and_marks_error_excerpt(self):
        text = {'type': 'result', 'structured_output': {**REPORT, 'references': 'x'*10000}}
        fake = self.fake('print('+repr(json.dumps(text))+')\n')
        out = io.StringIO()
        with patch.object(self.run, 'save_report', side_effect=ValueError('é'*2000)):
            code = self.run.run([fake, '-p', 'task'], 'claude', 'code', {}, self.root, output=out,
                                override={'worker_report_max_tokens': 1, 'reason': 'minimal envelope'})
        self.assertEqual(code, 1)
        self.assertLessEqual(len(out.getvalue().strip().encode()), 256)
        self.assertIn('fragmento', json.loads(out.getvalue())['pending'])

    def test_failed_oversize_save_is_nonzero_and_preserves_bounded_exact_error(self):
        text = {'type': 'result', 'structured_output': {**REPORT, 'references': 'x'*10000}}
        fake = self.fake('print('+repr(json.dumps(text))+')\n')
        out = io.StringIO()
        with patch.object(self.run, 'save_report', side_effect=ValueError('MEMORY_WRITE_REJECTED: suspected secret')):
            code = self.run.run([fake, '-p', 'task'], 'claude', 'code', {}, self.root, output=out)
        self.assertEqual(code, 1)
        self.assertIn('MEMORY_WRITE_REJECTED: suspected secret', json.loads(out.getvalue())['error'])
        self.assertNotIn('x'*100, out.getvalue())
