import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import safe_updates as updates
import runtime_guard
import shared_memory
import harness


class SafeUpdatesTests(unittest.TestCase):
    def setUp(self):
        real_which = updates.shutil.which
        which = patch.object(updates.shutil, 'which', side_effect=lambda name: '/fixture/npm' if name == 'npm' else real_which(name))
        which.start(); self.addCleanup(which.stop)
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, {'HOME': str(self.root), 'XDG_DATA_HOME': str(self.root / 'data'),
                                     'XDG_CONFIG_HOME': str(self.root / 'config')}, clear=False)
        env.start(); self.addCleanup(env.stop)
        self.prefix = shared_memory.data_root() / 'tools/ecc-memory'
        self.package(self.prefix, '2.2.3')
        self.metadata = {'name': 'ecc-universal', 'version': '2.2.4', 'license': 'MIT',
                         'repository': {'url': 'git+https://github.com/affaan-m/ECC.git'}}
        for mocked in (patch.object(updates, 'metadata', return_value=self.metadata),
                       patch.object(runtime_guard, 'assert_idle'), patch.object(shared_memory, 'probe'),
                       patch.object(shared_memory.system_tools, 'node_binary', return_value='/fixture/node')):
            mocked.start(); self.addCleanup(mocked.stop)

    def package(self, prefix, version):
        path = prefix / 'node_modules/ecc-universal'; (path / 'scripts/lib').mkdir(parents=True)
        (path / 'package.json').write_text(json.dumps({'name': 'ecc-universal', 'version': version, 'license': 'MIT',
            'repository': {'url': 'git+https://github.com/affaan-m/ECC.git'}, 'bin': {'ecc-memory-mcp': 'scripts/memory-mcp.mjs'}}))
        for name in ('memory-mcp.mjs', 'lib/memory-vault.js', 'lib/memory-vault-format.js', 'lib/path-safety.js', 'lib/missing-dependency.js'):
            (path / 'scripts' / name).write_text('// fixture ' + version)
        return path

    def npm(self, argv, **kwargs):
        self.package(Path(argv[argv.index('--prefix') + 1]), '2.2.4')
        return subprocess.CompletedProcess(argv, 0, '', '')

    def test_shared_clients_and_exclusive_updates_exclude_each_other_and_release_after_failure(self):
        lock = self.root / 'state/lease'
        with runtime_guard.lease(lock):
            with runtime_guard.lease(lock):
                with self.assertRaises(runtime_guard.BusyError):
                    with runtime_guard.lease(lock, exclusive=True):pass
        with runtime_guard.lease(lock, exclusive=True):
            with self.assertRaises(runtime_guard.BusyError):
                with runtime_guard.lease(lock):pass
        with runtime_guard.lease(lock):pass

    def test_mcp_inherits_shared_lease_through_exec_and_update_waits_for_exit(self):
        lock = self.root / 'state/lifetime'
        script = ('import sys;sys.path.insert(0,' + repr(str(Path(runtime_guard.__file__).parent)) + ');'
                  'import runtime_guard,os;'
                  'ctx=runtime_guard.lease(' + repr(str(lock)) + ',inherit=True);ctx.__enter__();'
                  'os.execl(' + repr(sys.executable) + ',"python","-c","import sys;print(\'ready\',flush=True);sys.stdin.read()")')
        child = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            with self.assertRaises(runtime_guard.BusyError):
                with runtime_guard.lease(lock, exclusive=True):pass
            child.communicate('', timeout=5)
            with runtime_guard.lease(lock, exclusive=True):pass
        finally:
            if child.poll() is None:child.kill();child.wait()

    def test_current_version_is_verified_noop_without_install_or_recovery(self):
        self.metadata['version'] = '2.2.3'
        before = shared_memory.inspect_runtime()['digest']
        with patch.object(updates.subprocess, 'run') as run:
            result = updates.memory_update()
            run.assert_not_called()
        self.assertEqual(result['outcome'], 'current'); self.assertFalse(result['changed'])
        self.assertEqual(shared_memory.inspect_runtime()['digest'], before)
        self.assertFalse((shared_memory.data_root() / 'recovery').exists())

    def test_memory_replacement_preserves_previous_runtime_and_preferences(self):
        settings = harness.load_config(); settings.update(agents={'codex': False}, memoryEnabled=False, foreignPreference=False)
        harness.save_config(settings); before = harness.config_path().read_bytes()
        with patch.object(updates.subprocess, 'run', side_effect=self.npm):
            result = updates.memory_update()
        self.assertTrue(result['changed']); self.assertEqual(shared_memory.inspect_runtime()['version'], '2.2.4')
        previous = Path(result['recovery']) / 'previous-runtime/node_modules/ecc-universal'
        self.assertEqual(shared_memory.runtime_at(previous, sys.executable)['version'], '2.2.3')
        self.assertEqual(harness.config_path().read_bytes(), before)

    def test_final_probe_failure_restores_original_without_touching_data(self):
        before = shared_memory.inspect_runtime()['digest']
        shared_memory.probe.side_effect = [None, shared_memory.MemoryError('synthetic final probe failure')]
        with patch.object(updates.subprocess, 'run', side_effect=self.npm):
            with self.assertRaises(shared_memory.MemoryError):updates.memory_update()
        self.assertEqual(shared_memory.inspect_runtime()['digest'], before)
        self.assertFalse((shared_memory.data_root() / 'memory').exists())

    def test_foreign_path_extra_files_bad_origin_and_active_clients_never_invoke_npm(self):
        for change in ('origin', 'extra', 'active', 'foreign'):
            with self.subTest(change=change), patch.object(updates.subprocess, 'run') as run:
                extra = self.prefix / 'foreign.txt'
                if change == 'origin':self.metadata['license'] = 'proprietary'
                if change == 'extra':extra.write_text('keep')
                if change == 'active':runtime_guard.assert_idle.side_effect = runtime_guard.BusyError('active')
                current = shared_memory.inspect_runtime()
                if change == 'foreign':current['path'] = str(self.root / 'external')
                with patch.object(shared_memory, 'inspect_runtime', return_value=current):
                    with self.assertRaises(updates.native_plugins.PluginError):updates.memory_update()
                run.assert_not_called()
                self.metadata['license'] = 'MIT'; extra.unlink(missing_ok=True); runtime_guard.assert_idle.side_effect = None


    def test_joint_update_skips_missing_components_and_preserves_preferences(self):
        config = {'schemaVersion': 1, 'agents': {}, 'memoryEnabled': False, 'foreignPreference': False}
        empty = {'installed': False}
        with patch.object(shared_memory, 'inspect_runtime', return_value=empty), \
             patch.object(harness, 'status', return_value={'components': [{'id': 'superpowers', 'targets': []}]}), \
             patch.object(harness, 'mise_inventory', return_value=({}, '')), \
             patch.object(updates, 'memory_update') as memory:
            result = harness.update_runtimes(config, 'core'); memory.assert_not_called()
        self.assertEqual(result, {'updates': [], 'updateErrors': []})
        self.assertFalse(config['memoryEnabled']); self.assertFalse(config['foreignPreference'])


if __name__ == '__main__':unittest.main()
