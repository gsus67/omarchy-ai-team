import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
import native_launcher


class NativeLauncherTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {'XDG_DATA_HOME':str(self.root/'data')})
        env.start(); self.addCleanup(env.stop)
        self.names = {'claude':'Claude Code','codex':'Codex','codex-2':'Codex-2'}
        self.config = {'launcherMenuEnabled':True, 'agents':{'claude':True,'codex-2':True}}
        self.script = self.root/'Plugin $ with "quotes" % and `backtick`'/'harness.py'

    def sync(self):
        native_launcher.sync_menu(self.config, self.names, self.script)

    def test_menu_tracks_only_selected_profiles_and_removal_keeps_unrelated_entries(self):
        self.sync(); folder = native_launcher.applications_dir()
        other = folder/'unrelated.desktop'; other.write_text('Keep me.')
        self.assertEqual({p.name for p in folder.glob('ai-team-*')}, {'ai-team-claude.desktop','ai-team-codex-2.desktop'})
        self.assertTrue(native_launcher.inspect_menu(self.config,self.names,self.script)['verified'])
        self.config['agents']['claude'] = False; self.sync()
        self.assertFalse((folder/'ai-team-claude.desktop').exists())
        self.config['launcherMenuEnabled'] = False; self.sync()
        self.assertEqual([p.name for p in folder.iterdir()], ['unrelated.desktop'])
        self.assertEqual(other.read_text(), 'Keep me.')

    def test_foreign_edits_block_before_any_other_entry_is_changed(self):
        self.sync(); folder = native_launcher.applications_dir()
        changed = folder/'ai-team-codex-2.desktop'; changed.write_text(changed.read_text()+'# My edit\n')
        baseline = {p.name:p.read_bytes() for p in folder.iterdir()}
        self.config['launcherMenuEnabled'] = False
        with self.assertRaises(native_launcher.LauncherError):self.sync()
        self.assertEqual({p.name:p.read_bytes() for p in folder.iterdir()},baseline)
        self.assertFalse(native_launcher.inspect_menu(self.config,self.names,self.script)['verified'])

    def test_symlink_is_preserved_and_target_cannot_be_overwritten(self):
        folder = native_launcher.applications_dir(); folder.mkdir(parents=True)
        other = self.root/'foreign'; other.write_text('Keep my file.')
        target = folder/'ai-team-claude.desktop'; target.symlink_to(other)
        with self.assertRaises(native_launcher.LauncherError):self.sync()
        self.assertTrue(target.is_symlink())
        self.assertEqual(other.read_text(), 'Keep my file.')
        self.assertFalse((folder/'ai-team-codex-2.desktop').exists())

    @unittest.skipUnless(shutil.which("desktop-file-validate"), "Native desktop validator not installed")
    def test_generated_desktop_passes_native_validator_with_literal_metacharacters(self):
        self.sync()
        for entry in native_launcher.applications_dir().glob('*.desktop'):
            result = subprocess.run(['/usr/bin/desktop-file-validate',str(entry)], capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        with self.assertRaises(native_launcher.LauncherError):
            native_launcher.desktop_quote('invalid\npath')

    def test_terminal_command_passes_profile_as_data_without_permission_or_model_overrides(self):
        home = self.root/'account $(literal)'
        with patch.object(native_launcher.shutil,'which',return_value='/usr/bin/omarchy'), \
             patch.object(native_launcher.subprocess,'Popen') as popen:
            native_launcher.launch('/absolute/codex', 'codex-2', home)
        args = popen.call_args.args[0]
        self.assertEqual(args[:4], ['/usr/bin/omarchy','launch','terminal','/usr/bin/env'])
        self.assertEqual(args[-2:], ['CODEX_HOME='+str(home),'/absolute/codex'])
        self.assertFalse(popen.call_args.kwargs.get('shell',False))
        self.assertFalse(any(x in args for x in ('--approve-for-me','--permission-mode','--model','--dangerously-bypass-approvals-and-sandbox')))

    def test_authentication_uses_selected_profile_and_subscription_flow_without_flags_or_tokens(self):
        with patch.object(native_launcher.shutil,'which',return_value='/usr/bin/omarchy'), \
             patch.object(native_launcher.subprocess,'Popen') as popen:
            native_launcher.authenticate('/native/codex','codex-2',self.root/'.codex-2')
            args=popen.call_args.args[0]
            self.assertEqual(args[-3:],['CODEX_HOME='+str(self.root/'.codex-2'),'/native/codex','login'])
            native_launcher.authenticate('/native/claude','claude',self.root/'.claude')
            args=popen.call_args.args[0]
            self.assertEqual(args[-4:],['/native/claude','auth','login','--claudeai'])
            self.assertNotIn('--console',args)


if __name__ == '__main__':unittest.main()
