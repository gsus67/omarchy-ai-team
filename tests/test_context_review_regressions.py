import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import context_policy
import context_run
import context_state
import native_launcher


class ReviewRegressions(unittest.TestCase):
    def test_memory_disabled_or_profile_unselected_omits_memory_hooks(self):
        for agent in ('claude', 'codex', 'codex-2', 'pi'):
            for config in ({'memoryEnabled': False, 'agents': {agent: True}},
                           {'memoryEnabled': True, 'agents': {agent: False}}, {'agents': {agent: True}}):
                args, env = native_launcher.invocation(agent, config=config, cwd=Path.cwd())
                self.assertNotIn('SessionStart', ' '.join(args))
                self.assertNotIn('PreCompact', ' '.join(args))
                capsule = json.loads(next(x.split('=', 1)[1] for x in env if x.startswith('AI_TEAM_CONTEXT=')))
                self.assertFalse(capsule['memory_enabled'])

    def test_codex_cwd_conflict_rejected_and_matching_path_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); other = root / 'other'; other.mkdir()
            marker = root / 'executed'
            fake = root / 'codex'; fake.write_text('#!/usr/bin/python3\nimport json, pathlib\npathlib.Path('+repr(str(marker))+').touch()\nprint(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps({"status":"ok","references":"","tests":"","error":"","pending":""})}}))\n'); fake.chmod(0o700)
            with patch.dict(os.environ, {'XDG_STATE_HOME': str(root / 'state')}), self.assertRaisesRegex(ValueError, '(cwd|directorio|directory)'):
                context_run.run([str(fake), 'exec', '-C', str(other), 'task'], 'codex', 'code', {}, root)
            self.assertFalse(marker.exists())
            prepared, _, capsule = context_run.prepare([str(fake), 'exec', '--cd='+str(root), 'task'], 'codex', 'code', {}, root)
            self.assertIn('--cd='+str(root), prepared)
            self.assertEqual(capsule['cwd'], str(root))
            with patch.dict(os.environ, {'XDG_STATE_HOME': str(root / 'state')}):
                self.assertEqual(context_run.run([str(fake), 'exec', '-C', str(root), 'task'], 'codex', 'code', {}, root, output=io.StringIO()), 0)
            self.assertTrue(marker.exists())

    def test_codex_cwd_options_after_prompt_and_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            other = root / 'other'
            other.mkdir()
            for args in (['task', '-C', str(other)],
                         ['-i', 'uno.png', 'dos.png', '-C', str(other)],
                         ['task', '--thread-source', 'cli', '-C', str(other)],
                         ['task', '--cyber-access-program', 'standard', '-C', str(other)],
                         ['-C', str(root), 'task', '-C', str(other)],
                         ['task', '-C='+str(other)]):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    context_run.prepare(['codex', 'exec', *args], 'codex', 'code', {}, root)
            for args in (['-C=.', 'task'], ['-C='+str(root), 'task'],
                         ['task', '--', '-C', str(other)]):
                with self.subTest(args=args):
                    context_run.prepare(['codex', 'exec', *args], 'codex', 'code', {}, root)

    def test_codex_worktree_rejected_with_active_policy(self):
        root = Path.cwd()
        for command in (['codex', '--worktree', 'exec', 'task'],
                        ['codex', 'exec', '--worktree', 'task'],
                        ['codex', 'exec', 'task', '--worktree']):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, '--worktree'):
                context_run.prepare(command, 'codex', 'code', {}, root)
        _, _, capsule = context_run.prepare(['codex', 'exec', 'task'], 'codex', 'code', {}, root)
        self.assertEqual(capsule['cwd'], str(root))

    def test_disabled_policy_passes_conflicting_cwd_to_native_executor(self):
        command = ['codex', 'exec', '-C', '/other', 'task']
        config = {'context': {'enabled': False}}
        with patch.object(context_run.os, 'execvpe') as execute:
            context_run.run(command, 'codex', 'code', config, Path.cwd())
        execute.assert_called_once_with(command[0], command, dict(os.environ))
        prepared, _, _ = context_run.prepare(command, 'codex', 'code', config, Path.cwd())
        self.assertEqual(prepared, command)

    def test_pi_memory_disabled_runs_no_start_or_prompt_hooks(self):
        extension = (Path(__file__).resolve().parents[1] / 'extensions/pi-context-policy.ts').as_uri()
        script = '''const {default: extension}=await import(process.argv[1]); const h={};
extension({on:(e,f)=>h[e]=f}); const ctx={hasUI:false};
if (h.session_start) await h.session_start({},ctx);
if (h.before_agent_start) await h.before_agent_start({systemPrompt:"base"},ctx);'''
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'hooks'
            child = Path(tmp) / 'python3'
            child.write_text('#!/usr/bin/python3\nfrom pathlib import Path\nPath('+repr(str(marker))+').touch()\n')
            child.chmod(0o700)
            env = {**os.environ, 'PATH': tmp, 'AI_TEAM_CONTEXT': json.dumps({'memory_enabled': False, 'policy': {'suggest_resume_after_idle': True}})}
            result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script, extension], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())

    def test_emoji_path_is_valid_toml_for_both_codex_profiles(self):
        for agent in ('codex', 'codex-2'):
            capsule = context_state.instance(agent, 'code', Path.cwd(), context_policy.resolve({}, 'code'))
            capsule['cwd'] = '/work/proyecto-😀'
            args, _ = context_state.launch_options(capsule)
            for index, arg in enumerate(args):
                if arg == '-c':
                    value = tomllib.loads(args[index + 1])
                    if 'compact_prompt' in value:
                        self.assertIn('/work/proyecto-😀', value['compact_prompt'])

    def test_pi_overflow_never_cancelled_under_floor(self):
        extension = (Path(__file__).resolve().parents[1] / 'extensions/pi-context-policy.ts').as_uri()
        script = '''const {default: extension}=await import(process.argv[1]); const h={};
extension({on:(e,f)=>h[e]=f}); const r=await h.session_before_compact({reason:"overflow",preparation:{tokensBefore:32000}},{hasUI:false}); console.log(JSON.stringify(r ?? null));'''
        env = {**os.environ, 'AI_TEAM_CONTEXT': json.dumps({'policy': {'compact_floor_tokens': 70000, 'dump_state_before': False}})}
        result = subprocess.run(['node', '--input-type=module', '-e', script, extension], env=env, capture_output=True, text=True, check=True)
        self.assertNotEqual(json.loads(result.stdout), {'cancel': True})

    def test_pi_start_and_idle_context_reach_first_and_next_turn(self):
        extension = (Path(__file__).resolve().parents[1] / 'extensions/pi-context-policy.ts').as_uri()
        script = '''const {default: extension}=await import(process.argv[1]); const h={};
extension({on:(e,f)=>h[e]=f}); const ctx={hasUI:false};
await h.session_start({},ctx); const first=await h.before_agent_start({systemPrompt:"base"},ctx);
const second=await h.before_agent_start({systemPrompt:"base"},ctx);
console.log(JSON.stringify({first,second}));'''
        with tempfile.TemporaryDirectory() as tmp:
            child = Path(tmp) / 'python3'
            child.write_text('#!/usr/bin/python3\nimport json,sys\na=sys.argv[2]\nprint(json.dumps({"hookSpecificOutput":{"additionalContext":"INDEX" if a == "start" else "IDLE"}}))\n')
            child.chmod(0o700)
            env = {**os.environ, 'PATH': tmp, 'AI_TEAM_CONTEXT': json.dumps({'memory_enabled': True, 'policy': {'suggest_resume_after_idle': True}})}
            result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script, extension], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertIn('INDEX', value['first']['message']['content'])
        self.assertNotIn('systemPrompt', value['first'])
        self.assertIn('IDLE', value['second']['message']['content'])
