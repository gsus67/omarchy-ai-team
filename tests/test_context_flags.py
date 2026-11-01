"""Settings and instructions of the long-session savings: grouping, exploration, handoff, idle TTL."""
import contextlib
from copy import deepcopy
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
import context_policy
import harness

BOOLS = ('group_related_subtasks', 'explore_with_simple_role', 'handoff_on_pause', 'suggest_resume_after_idle')
TTL = 'idle_cache_ttl_minutes'
ROLES = ('jefe', 'plan', 'code', 'review', 'simple')
OLD_FRESH = 'Abre un obrero nuevo por subtarea, con encargo autocontenido; no reanudes ni heredes su conversación. '


class PolicyTests(unittest.TestCase):
    def test_defaults_resolve_for_every_role_and_old_config_still_loads(self):
        old = {'context': {'compact_at_tokens': 180000}}
        original = deepcopy(old)
        for role in ROLES:
            resolved = context_policy.resolve(old, role)
            for key in BOOLS:
                self.assertIs(resolved[key], True, key)
            self.assertEqual(resolved[TTL], 55)
        self.assertEqual(old, original)
        self.assertEqual(context_policy.settings({})[TTL], 55)

    def test_types_and_ttl_range_are_strict_globally_per_role_and_per_invocation(self):
        invalid = [{key: value} for key in BOOLS for value in (1, 0, 'true', None)]
        invalid += [{TTL: value} for value in (0, 1441, -1, True, 1.5, '55', None)]
        invalid += [{'roles': {'code': {BOOLS[0]: 'no'}}}, {'roles': {'code': {TTL: 0}}}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(context_policy.ContextPolicyError):
                context_policy.settings({'context': value})
        for edge in (1, 1440):
            self.assertEqual(context_policy.settings({'context': {TTL: edge}})[TTL], edge)
        for bad in ({TTL: 1441}, {BOOLS[1]: 1}):
            with self.assertRaises(context_policy.ContextPolicyError):
                context_policy.resolve({}, 'code', override={**bad, 'reason': 'x'})

    def test_role_overrides_then_reasoned_invocation_win_without_mutating_the_config(self):
        config = {'context': {TTL: 30, 'handoff_on_pause': False, 'roles': {
            'code': {TTL: 10, 'group_related_subtasks': False}, 'review': {'handoff_on_pause': True}}}}
        original = deepcopy(config)
        self.assertEqual(context_policy.resolve(config, 'plan')[TTL], 30)
        self.assertIs(context_policy.resolve(config, 'plan')['handoff_on_pause'], False)
        code = context_policy.resolve(config, 'code')
        self.assertEqual((code[TTL], code['group_related_subtasks']), (10, False))
        self.assertIs(context_policy.resolve(config, 'review')['handoff_on_pause'], True)
        forced = context_policy.resolve(config, 'code', override={TTL: 5, 'suggest_resume_after_idle': False, 'reason': 'x'})
        self.assertEqual((forced[TTL], forced['suggest_resume_after_idle']), (5, False))
        self.assertEqual(config, original)

    def test_snapshot_shows_the_new_keys_as_native_types_and_with_value_validates(self):
        shot = context_policy.snapshot({})
        for scope in (shot['settings'], *shot['resolved'].values()):
            self.assertEqual([scope[key] for key in BOOLS], [True] * 4)
            self.assertIs(type(scope[TTL]), int)
        changed = context_policy.with_value({}, TTL, 90)
        self.assertEqual(context_policy.snapshot(changed)['settings'][TTL], 90)
        role = context_policy.with_value({}, 'handoff_on_pause', False, 'code')
        self.assertIs(context_policy.snapshot(role)['resolved']['code']['handoff_on_pause'], False)
        self.assertIs(context_policy.snapshot(role)['resolved']['plan']['handoff_on_pause'], True)
        with self.assertRaises(context_policy.ContextPolicyError):
            context_policy.with_value({}, TTL, 0)


class InstructionTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('context_state')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def text(self, role='code', **setting):
        policy = context_policy.resolve({'context': setting}, role)
        return self.module.instructions(self.module.instance('codex', role, self.temp.name, policy))

    def test_grouping_is_asked_of_chief_and_planner_and_reshapes_the_fresh_worker_text(self):
        for role in ('jefe', 'plan'):
            text = self.text(role)
            for fragment in ('mismos archivos', 'UN obrero', 'cambiar de fase'):
                self.assertIn(fragment, text)
        self.assertNotIn('UN obrero', self.text('code'))
        on = self.text('jefe')
        self.assertIn('obrero nuevo', on)  # fresh workers stay on
        self.assertNotIn(OLD_FRESH, on)
        self.assertIn('grupo', on)
        off = self.text('jefe', group_related_subtasks=False)
        self.assertIn(OLD_FRESH, off)
        self.assertNotIn('UN obrero', off)
        self.assertNotIn('obrero nuevo', self.text('jefe', fresh_worker_per_subtask=False))

    def test_exploration_goes_to_the_simple_role_returning_only_pointers(self):
        for role in ('jefe', 'plan', 'code', 'review'):
            text = self.text(role)
            for fragment in ('Tareas sencillas', 'archivo:línea', 'sin contenido'):
                self.assertIn(fragment, text)
        worker = self.text('simple')
        self.assertIn('solo punteros archivo:línea', worker)
        self.assertNotIn('Delega', worker)
        for role in ROLES:
            self.assertNotIn('Tareas sencillas', self.text(role, explore_with_simple_role=False))

    def test_handoff_on_pause_asks_for_a_verified_checkpoint_at_block_end_or_waiting(self):
        for role in ROLES:
            text = self.text(role)
            for fragment in ('esperando al usuario', 'relevo estructurado', 'status=saved'):
                self.assertIn(fragment, text)
            self.assertNotIn('esperando al usuario', self.text(role, handoff_on_pause=False))

    def test_every_flag_off_leaves_the_previous_text(self):
        off = {key: False for key in BOOLS}
        for role in ROLES:
            text = self.text(role, **off)
            for marker in ('UN obrero', 'Tareas sencillas', 'punteros archivo:línea, sin', 'esperando al usuario',
                           'relevo estructurado'):
                self.assertNotIn(marker, text, (role, marker))
        self.assertIn(OLD_FRESH, self.text('jefe', **off))


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = {'HOME': self.temp.name, 'XDG_CONFIG_HOME': self.temp.name + '/config', 'PATH': '',
               'LANG': 'en_US', 'LC_ALL': '', 'LC_MESSAGES': ''}
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = harness.main(list(argv))
        return code, json.loads(out.getvalue() or 'null')

    def test_context_set_and_reset_cover_the_new_keys_globally_and_per_role(self):
        self.assertEqual(self.run_cli('context-set', TTL, '30')[0], 0)
        self.assertEqual(self.run_cli('context-set', 'suggest_resume_after_idle', 'false')[0], 0)
        self.assertEqual(self.run_cli('context-set', 'handoff_on_pause', 'false', '--role', 'code')[0], 0)
        saved = json.loads(harness.config_path().read_text())['context']
        self.assertEqual((saved[TTL], saved['suggest_resume_after_idle']), (30, False))
        self.assertIs(saved['roles']['code']['handoff_on_pause'], False)
        for bad in ((TTL, '0'), (TTL, '"55"'), ('explore_with_simple_role', '1')):
            self.assertNotEqual(self.run_cli('context-set', *bad)[0], 0, bad)
        self.assertEqual(json.loads(harness.config_path().read_text())['context'], saved)
        self.assertEqual(self.run_cli('context-reset')[0], 0)
        self.assertNotIn('context', json.loads(harness.config_path().read_text()))


if __name__ == '__main__':
    unittest.main()
