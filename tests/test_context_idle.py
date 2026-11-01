"""Idle cold-cache hook: context-only advice, private per-instance state, safe about /clear."""
import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import context_policy

T0 = 1_800_000_000  # synthetic epoch seconds
MINUTE = 60


class Vault:
    def __init__(self):
        self.memories = {}

    def call(self, name, arguments):
        if name == 'memory_doctor':
            return {'activeProject': 'synthetic'}
        if name == 'memory_save':
            key = 'mem_' + str(len(self.memories))
            # Real ECC always stamps createdAt; the far future stands for 'saved after the last turn'.
            self.memories[key] = {**arguments, 'id': key, 'createdAt': '2099-01-01T00:00:00Z'}
            return {'memory': {k: v for k, v in self.memories[key].items() if k != 'body'}}
        if name == 'memory_read':
            return {'memory': self.memories[arguments['id']]}
        if name == 'memory_search':
            return {'results': [{'memory': m} for m in reversed(list(self.memories.values()))]}
        raise AssertionError(name)


class Broken:
    def __init__(self, error):
        self.error = error

    def call(self, name, arguments):
        raise self.error


STATE = {'plan': ['p'], 'decisions': [], 'files': ['backend/a.py:1'], 'errors': [], 'pending': ['x']}


class IdleHookTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('context_state')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cwd = self.root / 'project'
        self.cwd.mkdir()
        patcher = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'state')})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.vault = Vault()

    def capsule(self, config=None, role='jefe', agent='claude'):
        return self.module.instance(agent, role, self.cwd, context_policy.resolve(config or {}, role))

    def state_dir(self, capsule):
        return self.root / 'state' / 'omarchy-ai-team' / 'context-usage' / capsule['id']

    def turn(self, capsule, at, **kwargs):
        kwargs.setdefault('client', self.vault)
        return self.module.idle(capsule, now=at, **kwargs)

    def idle_after(self, capsule, minutes=60, **kwargs):
        self.assertIsNone(self.turn(capsule, T0))
        return self.turn(capsule, T0 + minutes * MINUTE, **kwargs)

    # --- wiring -----------------------------------------------------------------------------
    def test_user_prompt_submit_hook_is_per_instance_native_and_opt_out(self):
        for agent in ('claude', 'codex', 'codex-2'):
            capsule = self.capsule(agent=agent)
            args, _ = self.module.launch_options(capsule)
            text = ' '.join(args)
            self.assertIn('UserPromptSubmit', text, agent)
            self.assertIn(' idle ', text)
            self.assertIn(capsule['id'], text)
            self.assertNotIn('bypass', text)
            off = self.capsule({'context': {'suggest_resume_after_idle': False}}, agent=agent)
            self.assertNotIn('UserPromptSubmit', ' '.join(self.module.launch_options(off)[0]))
            self.assertIn('SessionStart', ' '.join(self.module.launch_options(off)[0]))
        no_dump = self.capsule({'context': {'dump_state_before': False}})
        text = ' '.join(self.module.launch_options(no_dump)[0])
        self.assertIn('UserPromptSubmit', text)
        self.assertNotIn('PreCompact', text)

    # --- private state ----------------------------------------------------------------------
    def test_first_turn_records_only_a_timestamp_privately_and_says_nothing(self):
        capsule = self.capsule()
        self.assertIsNone(self.turn(capsule, T0))
        folder = self.state_dir(capsule)
        files = list(folder.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(folder.parent.stat().st_mode), 0o700)
        data = json.loads(files[0].read_text())
        self.assertEqual(set(data), {'schema', 'last_turn'})
        self.assertEqual(data['last_turn'], T0)

    def test_hook_main_ignores_the_prompt_and_stores_no_text(self):
        capsule = self.capsule()
        stdin = io.StringIO(json.dumps({'prompt': 'SECRET PROMPT', 'transcript_path': '/secret/t.jsonl'}))
        with patch.object(sys, 'stdin', stdin), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.module.main(['idle', '--instance', json.dumps(capsule)]), 0)
        stored = ''.join(p.read_text() for p in self.state_dir(capsule).iterdir())
        self.assertNotIn('SECRET', stored)
        self.assertNotIn('transcript', stored)

    def test_within_the_ttl_stays_silent_and_slides_the_clock(self):
        capsule = self.capsule({'context': {'idle_cache_ttl_minutes': 30}})
        self.assertIsNone(self.turn(capsule, T0))
        self.assertIsNone(self.turn(capsule, T0 + 29 * MINUTE))
        self.assertIsNone(self.turn(capsule, T0 + 58 * MINUTE))  # 29 min since the last one: still warm
        self.assertIsNotNone(self.turn(capsule, T0 + 100 * MINUTE))

    def test_notice_is_not_repeated_on_the_next_turn(self):
        capsule = self.capsule()
        self.assertIsNotNone(self.idle_after(capsule))
        self.assertIsNone(self.turn(capsule, T0 + 61 * MINUTE))

    # --- the safety rule ----------------------------------------------------------------------
    def test_verified_handoff_recommends_clear_and_brief_without_acting(self):
        capsule = self.capsule()
        saved = self.module.checkpoint(self.vault, capsule, STATE)
        notice = self.idle_after(capsule, 60)
        for fragment in ('caché fría', '60 min', saved['id'], '/clear', 'brief', 'tokens desconocidos', 'no se ha limpiado'):
            self.assertIn(fragment, notice)
        self.assertNotIn('NO conviene', notice)
        self.assertNotIn('backend/a.py:1', notice)  # pointers and ids only, never the body

    def test_without_a_verified_handoff_it_says_so_and_advises_against_clearing(self):
        capsule = self.capsule()
        notice = self.idle_after(capsule)
        self.assertIn('NO hay relevo verificado', notice)
        self.assertIn('NO conviene limpiar', notice)
        self.assertNotIn('Conviene /clear', notice)
        self.assertIn('caché fría', notice)

    def test_ecc_failures_never_claim_a_handoff(self):
        for error in (ValueError('exact ecc failure'), OSError('no node')):
            capsule = self.capsule()
            self.module.checkpoint(self.vault, capsule, STATE)  # a recorded handoff, so the failing call is reached
            notice = self.idle_after(capsule, client=Broken(error))
            with self.subTest(error=error):
                self.assertIn('NO hay relevo verificado', notice)
                self.assertIn('NO conviene limpiar', notice)
                self.assertNotIn('mem_', notice)

    def test_a_handoff_without_a_valid_date_cannot_prove_it_is_fresh(self):
        for date in (None, 'not-a-date'):
            capsule = self.capsule()
            key = self.module.checkpoint(self.vault, capsule, STATE)['id']
            memory = self.vault.memories[key]
            memory.pop('createdAt', None)
            if date:
                memory['createdAt'] = date
            with self.subTest(date=date):
                notice = self.idle_after(capsule)
                self.assertIn('NO hay relevo verificado', notice)
                self.assertIn('NO conviene limpiar', notice)

    def test_another_instances_handoff_does_not_count(self):
        capsule = self.capsule()
        other = self.module.instance('claude', 'jefe', self.cwd, capsule['policy'])
        self.module.checkpoint(self.vault, other, STATE)
        self.assertIn('NO hay relevo verificado', self.idle_after(capsule))

    def test_a_handoff_whose_reread_body_is_not_the_canonical_saved_one_does_not_count(self):
        capsule = self.capsule()
        key = self.module.checkpoint(self.vault, capsule, STATE)['id']
        memory = self.vault.memories[key]
        memory['body'] = json.dumps(json.loads(memory['body']), indent=2)
        self.assertIn('NO hay relevo verificado', self.idle_after(capsule))

    def test_a_valid_but_different_state_returned_by_ecc_is_not_the_saved_handoff(self):
        capsule = self.capsule()
        key = self.module.checkpoint(self.vault, capsule, STATE)['id']
        memory = self.vault.memories[key]
        other = {**json.loads(memory['body']), 'state': {**STATE, 'plan': ['another plan']}}
        memory['body'] = json.dumps(other, ensure_ascii=False, sort_keys=True)  # canonical, valid, same instance
        notice = self.idle_after(capsule)
        self.assertIn('NO hay relevo verificado', notice)
        self.assertIn('NO conviene limpiar', notice)

    def test_saving_a_handoff_records_only_its_id_and_digest_privately(self):
        capsule = self.capsule()
        key = self.module.checkpoint(self.vault, capsule, STATE)['id']
        files = list(self.state_dir(capsule).iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        text = files[0].read_text()
        self.assertIn(key, text)
        self.assertIn(hashlib.sha256(self.vault.memories[key]['body'].encode()).hexdigest(), text)
        self.assertNotIn('backend/a.py', text)
        self.assertNotIn('another', text)

    def test_without_a_recorded_digest_the_handoff_is_not_verified(self):
        capsule = self.capsule()
        self.module.checkpoint(self.vault, capsule, STATE)
        for path in self.state_dir(capsule).iterdir():
            path.unlink()
        self.assertIn('NO hay relevo verificado', self.idle_after(capsule))

    # --- special files never block the hook -----------------------------------------------------
    def within_a_second(self, function):
        outcome = []
        worker = threading.Thread(target=lambda: outcome.append(function()), daemon=True)
        started = time.monotonic()
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive(), 'the hook blocked on a special state file')
        self.assertLess(time.monotonic() - started, 1)
        return outcome[0]

    def test_a_fifo_as_last_turn_state_is_treated_as_no_state_without_blocking(self):
        capsule = self.capsule()
        folder = self.state_dir(capsule)
        folder.mkdir(parents=True, mode=0o700)
        os.mkfifo(folder / 'last_turn.json')
        self.assertIsNone(self.within_a_second(lambda: self.module._last_turn(capsule['id'])))
        os.unlink(folder / 'last_turn.json')
        os.mkfifo(folder / 'last_turn.json')
        self.assertIsNone(self.within_a_second(lambda: self.turn(capsule, T0)))

    def test_a_fifo_as_handoff_record_is_not_verified_without_blocking(self):
        capsule = self.capsule()
        self.module.checkpoint(self.vault, capsule, STATE)
        folder = self.state_dir(capsule)
        for path in folder.iterdir():
            path.unlink()
        os.mkfifo(folder / 'handoff_checkpoint.json')
        self.assertIsNone(self.within_a_second(lambda: self.turn(capsule, T0)))
        notice = self.within_a_second(lambda: self.turn(capsule, T0 + 60 * MINUTE))
        self.assertIn('NO hay relevo verificado', notice)

    def test_an_oversized_state_file_is_treated_as_no_state(self):
        capsule = self.capsule()
        folder = self.state_dir(capsule)
        folder.mkdir(parents=True, mode=0o700)
        (folder / 'last_turn.json').write_text(json.dumps({'schema': 1, 'last_turn': T0, 'pad': 'x' * 5000}))
        self.assertIsNone(self.module._last_turn(capsule['id']))

    def test_a_handoff_for_another_folder_does_not_count(self):
        capsule = self.capsule()
        self.module.checkpoint(self.vault, capsule, STATE)
        moved = {**capsule, 'cwd': str(self.root)}
        # The stored checkpoint says the original folder: it is not this instance's handoff.
        self.assertIn('NO hay relevo verificado', self.idle_after(moved))

    def test_a_handoff_older_than_the_last_turn_is_not_a_current_handoff(self):
        capsule = self.capsule()
        key = self.module.checkpoint(self.vault, capsule, STATE)['id']
        self.vault.memories[key]['createdAt'] = '2026-01-01T00:00:00Z'  # long before T0
        self.assertIn('NO hay relevo verificado', self.idle_after(capsule))
        fresh = self.capsule()
        key = self.module.checkpoint(self.vault, fresh, STATE)['id']
        self.vault.memories[key]['createdAt'] = '2099-01-01T00:00:00Z'
        self.assertIn('/clear', self.idle_after(fresh))

    # --- size of the context -----------------------------------------------------------------
    def test_known_tokens_below_the_floor_stay_silent_and_above_are_reported(self):
        capsule = self.capsule()
        self.assertIsNone(self.idle_after(capsule, tokens=69999))
        big = self.capsule()
        notice = self.idle_after(big, tokens=123456)
        self.assertIn('123456', notice)
        self.assertNotIn('desconocidos', notice)
        limit = self.capsule({'context': {'compact_floor_tokens': 20000}})
        self.assertIsNotNone(self.idle_after(limit, tokens=20000))

    # --- never breaks the turn ------------------------------------------------------------------
    def test_hook_output_only_adds_context_and_never_blocks(self):
        capsule = self.capsule()
        self.turn(capsule, T0)
        out = io.StringIO()
        with patch.object(self.module, '_now', return_value=T0 + 90 * MINUTE), \
                patch.object(self.module, 'ECC', return_value=self.vault), contextlib.redirect_stdout(out):
            self.assertEqual(self.module.main(['idle', '--instance', json.dumps(capsule)]), 0)
        reply = json.loads(out.getvalue())
        self.assertEqual(set(reply), {'hookSpecificOutput'})
        self.assertEqual(set(reply['hookSpecificOutput']), {'hookEventName', 'additionalContext'})
        self.assertEqual(reply['hookSpecificOutput']['hookEventName'], 'UserPromptSubmit')
        self.assertIn('NO hay relevo verificado', reply['hookSpecificOutput']['additionalContext'])

    def test_unwritable_state_or_garbage_never_surfaces(self):
        capsule = self.capsule()
        (self.root / 'blocker').write_text('file, not a directory')
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'blocker')}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(self.module.main(['idle', '--instance', json.dumps(capsule)]), 0)
        self.assertEqual((out.getvalue(), err.getvalue()), ('', ''))
        for garbage in ('not json', '{"schema": 1, "last_turn": "x"}', '[]'):
            folder = self.state_dir(capsule)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'last_turn.json').write_text(garbage)
            self.assertIsNone(self.turn(capsule, T0), garbage)
        for bad in ('{"id": "nope"}', 'not json'):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(self.module.main(['idle', '--instance', bad]), 0)

    def test_disabled_setting_writes_nothing_and_says_nothing(self):
        capsule = self.capsule({'context': {'suggest_resume_after_idle': False}})
        self.assertIsNone(self.idle_after(capsule))
        self.assertFalse(self.state_dir(capsule).exists())


if __name__ == '__main__':
    unittest.main()
