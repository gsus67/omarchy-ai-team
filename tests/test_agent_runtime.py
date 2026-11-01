import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import agent_runtime
import harness


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        env = patch.dict(os.environ, {'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root / 'config')})
        env.start(); self.addCleanup(env.stop)
        self.inventory = {}; self.tool = {'installation': 'missing', 'binary': '', 'version': ''}
        self.args = []

    def install(self, version):
        directory = self.root / 'mise/installs/codex' / version
        (directory / 'bin').mkdir(parents=True)
        binary = directory / 'bin/codex'
        binary.write_text('#!' + sys.executable + '\nprint(' + repr('codex-cli ' + version) + ')\n'); binary.chmod(0o700)
        self.inventory = {'codex': [{'installed': True, 'active': True, 'install_path': str(directory), 'version': version}]}
        self.tool = {'installation': 'installed', 'binary': str(binary), 'version': version}
        return binary

    def operate(self, action):
        return agent_runtime.operate('codex', action, self.inventory, lambda *_: self.tool, lambda: (self.inventory, ''))

    def runner(self, argv, **kwargs):
        if argv[0] == '/native/mise':
            self.args.append((argv, kwargs)); self.install('1.2.4')
            return subprocess.CompletedProcess(argv, 0, '', '')
        return self.original_run(argv, **kwargs)

    def test_install_uses_official_global_package_and_checks_real_version(self):
        self.original_run = subprocess.run
        with patch.object(agent_runtime.shutil, 'which', return_value='/native/mise'), patch.object(agent_runtime.subprocess, 'run', side_effect=self.runner):
            result = self.operate('install')
        self.assertEqual(result['outcome'], 'installed'); self.assertEqual(result['version'], '1.2.4')
        self.assertEqual(self.args[0][0], ['/native/mise', 'use', '--global', '--quiet', 'codex'])
        self.assertEqual(self.args[0][1]['cwd'], self.root)
        self.assertFalse((self.root / '.codex').exists())

    def test_update_retains_old_executable_and_requests_no_prune(self):
        old = self.install('1.2.3'); original = old.read_bytes(); self.original_run = subprocess.run
        with patch.object(agent_runtime.shutil, 'which', return_value='/native/mise'), patch.object(agent_runtime.subprocess, 'run', side_effect=self.runner):
            result = self.operate('update')
        self.assertEqual(result['outcome'], 'updated'); self.assertEqual(old.read_bytes(), original)
        self.assertEqual(self.args[0][0], ['/native/mise', 'upgrade', '--global', '--no-prune', 'codex'])

    def test_external_installation_or_missing_update_never_runs_mise(self):
        with patch.object(agent_runtime.subprocess, 'run') as run:
            with self.assertRaises(agent_runtime.AgentError):self.operate('update')
            self.install('1.2.3'); self.inventory = {}
            with self.assertRaisesRegex(agent_runtime.AgentError, 'otro gestor'):self.operate('update')
            run.assert_not_called()

    def test_success_without_actual_install_is_not_success(self):
        with patch.object(agent_runtime.shutil, 'which', return_value='/native/mise'), \
             patch.object(agent_runtime.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            with self.assertRaisesRegex(agent_runtime.AgentError, 'ejecutable instalado'):self.operate('install')

    def test_native_noop_is_current_without_changing_configuration(self):
        self.install('1.2.3'); config = self.root / 'config.toml'; config.write_text('keep\n')
        before = config.read_bytes(); original_run = subprocess.run
        def same(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, '', '') if argv[0] == '/native/mise' else original_run(argv, **kwargs)
        with patch.object(agent_runtime.shutil, 'which', return_value='/native/mise'), patch.object(agent_runtime.subprocess, 'run', side_effect=same):
            result = self.operate('update')
        self.assertFalse(result['changed']); self.assertEqual(result['outcome'], 'current'); self.assertEqual(config.read_bytes(), before)

    def test_false_version_and_pruned_old_installation_are_not_verified(self):
        old = self.install('1.2.3'); self.original_run = subprocess.run
        def prune(argv, **kwargs):
            result = self.runner(argv, **kwargs)
            if argv[0] == '/native/mise':old.unlink()
            return result
        with patch.object(agent_runtime.shutil, 'which', return_value='/native/mise'), patch.object(agent_runtime.subprocess, 'run', side_effect=prune):
            with self.assertRaisesRegex(agent_runtime.AgentError, 'ejecutable anterior'):self.operate('update')
        self.tool['version'] = '9.9.9'
        with self.assertRaisesRegex(agent_runtime.AgentError, 'versión'):agent_runtime.check_version('codex', self.tool)

    def test_missing_profile_is_not_created_by_install_or_update(self):
        with patch.object(harness, 'mise_inventory', return_value=({}, '')), patch.object(agent_runtime, 'operate') as operation:
            with self.assertRaisesRegex(harness.HarnessError, 'perfil codex-2'):harness.manage_agent_runtime('codex-2', 'install')
            operation.assert_not_called()
        self.assertFalse((self.root / '.codex-2').exists())


if __name__ == '__main__':unittest.main()


class UninstallTests(unittest.TestCase):
    def test_uninstall_rejects_missing_and_external_cli(self):
        import agent_runtime
        missing = lambda name, inv: {'installation': 'missing'}
        with self.assertRaises(agent_runtime.AgentError):
            agent_runtime.operate('claude', 'uninstall', {}, missing, lambda: ({}, ''))
        external = lambda name, inv: {'installation': 'installed', 'binary': '/usr/bin/claude'}
        with self.assertRaises(agent_runtime.AgentError):
            agent_runtime.operate('claude', 'uninstall', {}, external, lambda: ({}, ''))

    def test_profile_two_cannot_uninstall_shared_cli(self):
        import harness
        with self.assertRaises(harness.HarnessError):
            harness.manage_agent_cli('claude-2', 'uninstall')
