"""Savings contract of the launch instructions; every capsule and vault here is synthetic."""
import contextlib
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import context_policy


class SavingsInstructionTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('context_state')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        other = Path(self.temp.name) / 'other'
        other.mkdir()
        self.other = other

    def capsule(self, role='code', agent='codex', config=None, cwd=None, override=None):
        policy = context_policy.resolve(config or {}, role, override=override)
        return self.module.instance(agent, role, cwd or self.temp.name, policy)

    def text(self, role='code', **setting):
        return self.module.instructions(self.capsule(role, config={'context': setting}))

    def test_pointer_contract_has_goal_references_acceptance_and_do_not_reread_with_exceptions(self):
        text = self.text('plan')
        for fragment in ('objetivo', 'archivo:línea', 'aceptación', 'no releer',
                         'cambian los archivos', 'fallo', 'evidencia insuficiente'):
            self.assertIn(fragment, text)
        worker = self.text('code')
        self.assertIn('no releer', worker)
        self.assertIn('evidencia insuficiente', worker)
        disabled = self.text('plan', task_plan_with_pointers=False)
        self.assertNotIn('no releer', disabled)
        self.assertNotIn('objetivo', disabled)

    def test_resolved_window_floor_and_budget_are_interpolated(self):
        capsule = self.capsule('code', override={'read_window_lines': 77, 'compact_floor_tokens': 65000,
                                                 'worker_report_max_tokens': 333, 'reason': 'test'})
        text = self.module.instructions(capsule)
        self.assertIn('ventanas de 77 líneas', text)
        self.assertIn('65000', text)
        self.assertIn('333', text)
        self.assertNotIn('ventanas de', self.text('code', bounded_reads=False))

    def test_fresh_worker_and_clear_preferences_follow_their_flags(self):
        jefe = self.text('jefe')
        self.assertIn('obrero nuevo', jefe)
        self.assertNotIn('obrero nuevo', self.text('jefe', fresh_worker_per_subtask=False))
        on = self.text('code')
        self.assertIn('/clear', on)
        self.assertIn('no lo envíes', on)
        self.assertIn('desconocid', on)
        self.assertNotIn('/clear', self.text('code', clear_between_tasks_below_floor=False))

    def test_stable_prefix_is_shared_across_instances_and_variable_data_comes_last(self):
        first = self.capsule('code')
        second = self.capsule('code', cwd=self.other)
        self.assertNotEqual((first['id'], first['cwd']), (second['id'], second['cwd']))
        stable = self.module.stable_instructions(first)
        self.assertEqual(stable, self.module.stable_instructions(second))
        full_a, full_b = self.module.instructions(first), self.module.instructions(second)
        self.assertTrue(full_a.startswith(stable) and full_b.startswith(stable))
        self.assertNotEqual(full_a, full_b)
        for variable in (first['id'], first['cwd'], 'context_state.py'):
            self.assertNotIn(variable, stable)
            self.assertIn(variable, full_a)
        self.assertNotEqual(stable, self.module.stable_instructions(self.capsule('review')))

    def test_disabled_stable_prefix_keeps_instance_first_and_changed_settings_do_not_touch_a_launched_capsule(self):
        legacy = self.capsule('code', config={'context': {'stable_prompt_prefix': False}})
        self.assertIn(legacy['id'], self.module.instructions(legacy)[:120])
        capsule = self.capsule('code')
        before = self.module.instructions(capsule)
        self.capsule('code', config={'context': {'read_window_lines': 5, 'bounded_reads': False}})
        self.assertEqual(self.module.instructions(capsule), before)

    def test_claude_records_the_native_snapshot_only_with_stable_prefix(self):
        stable = self.capsule('code', agent='claude')
        args, _ = self.module.launch_options(stable)
        self.assertEqual(args[args.index('--system-prompt-snapshot') + 1], 'on')
        legacy = self.capsule('code', agent='claude', config={'context': {'stable_prompt_prefix': False}})
        self.assertNotIn('--system-prompt-snapshot', self.module.launch_options(legacy)[0])
        self.assertNotIn('--system-prompt-snapshot', self.module.launch_options(self.capsule('code'))[0])

    def start_context(self, capsule):
        output = io.StringIO()
        with patch.object(self.module, 'ECC', side_effect=ValueError('ECC unavailable')), contextlib.redirect_stdout(output):
            self.assertEqual(self.module.main(['start', '--instance', json.dumps(capsule)]), 0)
        return json.loads(output.getvalue())['hookSpecificOutput']['additionalContext']

    def test_claude_session_start_does_not_repeat_the_system_prompt_but_codex_still_needs_it(self):
        claude = self.start_context(self.capsule('code', agent='claude'))
        self.assertNotIn('Umbral recomendado', claude)
        self.assertIn('ECC unavailable', claude)
        legacy = self.start_context(self.capsule('code', agent='claude', config={'context': {'stable_prompt_prefix': False}}))
        self.assertIn('Umbral recomendado', legacy)
        self.assertIn('Umbral recomendado', self.start_context(self.capsule('code', agent='codex')))


if __name__ == '__main__':
    unittest.main()
