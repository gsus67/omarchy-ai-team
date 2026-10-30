import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import native_tools
import shared_memory


class NativeToolTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        env=patch.dict(os.environ,{'HOME':str(self.root),'XDG_DATA_HOME':str(self.root/'data')});env.start();self.addCleanup(env.stop)

    def test_mise_tool_can_have_another_executable_name_without_running_it(self):
        binary=self.root/'install/bin/rg';binary.parent.mkdir(parents=True);binary.write_text('#!/bin/sh\nexit 99\n');binary.chmod(0o700)
        inventory={'ripgrep':[{'active':True,'installed':True,'version':'15.2.0','install_path':str(self.root/'install')}]}
        with patch.object(native_tools.shutil,'which',return_value=None),patch.object(native_tools.subprocess,'run') as run:
            result=native_tools.detect_tool('rg',inventory,'ripgrep');run.assert_not_called()
        self.assertEqual(result['binary'],str(binary))

    def test_success_without_installed_dependency_is_rejected(self):
        with patch.object(native_tools,'mise_inventory',return_value=({},'')),patch.object(native_tools.shutil,'which',side_effect=lambda n:'/native/mise' if n=='mise' else None),patch.object(native_tools.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')):
            with self.assertRaises(native_tools.native_plugins.PluginError):native_tools.ensure_mise('node')
        self.assertFalse((self.root/'config').exists())

    def test_nested_installed_binary_uses_official_resolution_and_rejects_escape(self):
        directory=self.root/'install';binary=directory/'nested/ruff'
        binary.parent.mkdir(parents=True);binary.write_text('#!/bin/sh\nexit 99\n');binary.chmod(0o700)
        inventory={'ruff':[{'active':True,'installed':True,'version':'0.17.3','install_path':str(directory)}]}
        with patch.object(native_tools.shutil,'which',side_effect=lambda n:'/native/mise' if n=='mise' else None), patch.object(native_tools.subprocess,'run',return_value=subprocess.CompletedProcess([],0,str(binary)+'\n','')) as run:
            self.assertEqual(native_tools.detect_tool('ruff',inventory)['binary'],str(binary))
            self.assertEqual(run.call_args.args[0],['/native/mise','which','ruff','--tool','ruff@0.17.3'])
            run.return_value=subprocess.CompletedProcess([],0,'/usr/bin/python3\n','')
            self.assertEqual(native_tools.detect_tool('ruff',inventory)['installation'],'missing')
            run.reset_mock();inventory['ruff'][0]['installed']=False
            native_tools.detect_tool('ruff',inventory);run.assert_not_called()

    def test_memory_reuses_existing_package_after_installing_only_missing_node(self):
        missing={'installed':True,'node':'','path':'/existing/ecc','version':'2.2.3'}
        ready={**missing,'node':'/managed/node'}
        with patch.object(shared_memory,'inspect_runtime',side_effect=[missing,ready]),patch.object(native_tools,'ensure_mise') as ensure,patch.object(shared_memory.subprocess,'run') as run:
            self.assertEqual(shared_memory.ensure_runtime(),ready)
            ensure.assert_called_once_with('node');run.assert_not_called()


if __name__=='__main__':unittest.main()
