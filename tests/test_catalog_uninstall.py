import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import harness
import local_catalog as catalog
import project_scope


class CatalogUninstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.bin = self.root / 'bin'; self.bin.mkdir()
        self.state = self.root / 'mise.json'; self.state.write_text('{}')
        self.log = self.root / 'commands.jsonl'
        mise = self.bin / 'mise'
        mise.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
state = Path({str(self.state)!r})
args = sys.argv[1:]
with open({str(self.log)!r}, 'a') as output:
    output.write(json.dumps(args) + '\\n')
value = json.loads(state.read_text())
if args == ['ls', '--installed', '--json']:
    print(json.dumps(value))
elif args[:3] == ['use', '--global', '--quiet']:
    name, _, requested = args[3].partition('@')
    # mise with no explicit version can reuse an inactive older install.
    if not requested and value.get(name):
        value[name][-1]['active'] = True
        state.write_text(json.dumps(value))
        sys.exit(0)
    if os.environ.get('TEST_MISE_FAIL') == name:
        sys.exit(1)
    folder = Path({str(self.root)!r}) / 'installs' / name
    (folder / 'bin').mkdir(parents=True, exist_ok=True)
    binary = folder / 'bin' / ('rg' if name == 'ripgrep' else name)
    version = 'ripgrep 1.2.3' if name == 'ripgrep' else name + ' 1.2.3'
    binary.write_text('#!/bin/sh\\nprintf "%s\\\\n" "' + version + '"\\n')
    binary.chmod(0o755)
    rows = value.setdefault(name, [])
    for row in rows:
        row['active'] = False
    rows.append(dict(active=True, installed=True, version='1.2.3', install_path=str(folder)))
    state.write_text(json.dumps(value))
elif args[:2] == ['unuse', '--global']:
    if not os.environ.get('TEST_MISE_UNUSE_NOOP'):
        name = args[-1]
        if '--no-prune' in args:
            for row in value.get(name, []):
                row['active'] = False
        else:
            value.pop(name, None)
        state.write_text(json.dumps(value))
elif args[0] == 'uninstall':
    name, version = args[1].rsplit('@', 1)
    if os.environ.get('TEST_MISE_UNINSTALL_NOOP') != name:
        value[name] = [row for row in value.get(name, []) if row['version'] != version]
        state.write_text(json.dumps(value))
else:
    sys.exit(99)
''')
        mise.chmod(0o755)
        env = patch.dict(os.environ, {"LC_ALL": "es", 'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root / 'config'),
                                     'XDG_DATA_HOME': str(self.root / 'data'), 'PATH': str(self.bin)}, clear=True)
        env.start(); self.addCleanup(env.stop)
        self.config = {'schemaVersion': 1, 'agents': {}, 'foreign': 'keep'}

    def run_main(self, *arguments):
        output = io.StringIO()
        with patch.object(harness, 'status', return_value={}), contextlib.redirect_stdout(output):
            result = harness.main(arguments)
        return result, json.loads(output.getvalue())

    def unuse_commands(self):
        return [args for args in map(json.loads, self.log.read_text().splitlines()) if args[0] == 'unuse'] if self.log.exists() else []

    def test_activate_records_only_new_installs_and_keeps_ownership_on_retry(self):
        harness.activate_tool(self.config, 'rg')
        self.assertEqual(harness.load_config()['catalogInstalls'], {'rg': {'deps': [], 'versions': {'ripgrep': ['1.2.3']}}})
        harness.activate_tool(self.config, 'rg')
        self.assertEqual(harness.load_config()['catalogInstalls'], {'rg': {'deps': [], 'versions': {'ripgrep': ['1.2.3']}}})
        external = {'schemaVersion': 1, 'agents': {}}
        harness.activate_tool(external, 'rg')
        self.assertEqual(harness.load_config().get('catalogInstalls', {}), {})

    def test_dependency_recorded_only_when_newly_installed(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.state.write_text('{}')
                self.config.pop('catalogInstalls', None)
                if existing:
                    catalog.native_tools.ensure_mise('uv', 'uv')
                harness.activate_tool(self.config, 'semgrep')
                self.assertEqual(harness.load_config()['catalogInstalls'],
                                 {'semgrep': {'deps': [] if existing else ['uv'], 'versions': {'semgrep': ['1.2.3'], **({} if existing else {'uv': ['1.2.3']})}}})

    def test_failed_install_is_not_recorded(self):
        with patch.dict(os.environ, {'TEST_MISE_FAIL': 'ripgrep'}):
            with self.assertRaises(catalog.native_plugins.PluginError):
                harness.activate_tool(self.config, 'rg')
        self.assertNotIn('catalogInstalls', self.config)

    def test_failed_probe_keeps_real_install_record_for_uninstall(self):
        with patch.object(catalog, 'probe', side_effect=catalog.CatalogError('versión no válida')):
            with self.assertRaises(catalog.CatalogError):
                harness.activate_tool(self.config, 'semgrep')
        self.assertEqual(harness.load_config()['catalogInstalls'], {'semgrep': {'deps': ['uv'], 'versions': {'semgrep': ['1.2.3'], 'uv': ['1.2.3']}}})

    def test_uninstall_refuses_unrecorded_tools(self):
        catalog.install('rg')
        harness.save_config(self.config)
        before = harness.config_path().read_bytes()
        result, reply = self.run_main('catalog-uninstall', 'rg')
        self.assertEqual(result, 1)
        self.assertEqual(reply['error'], 'AI Team no instaló esta herramienta; se conserva.')
        self.assertEqual(self.unuse_commands(), [])
        self.assertEqual(harness.config_path().read_bytes(), before)

    def test_uninstall_removes_owned_dependency_resets_default_and_preserves_projects(self):
        harness.activate_tool(self.config, 'semgrep')
        scope = project_scope.project_key(self.root)
        catalog.set_preference(self.config, 'semgrep', 'default', 'on')
        catalog.set_preference(self.config, 'semgrep', scope, 'off')
        harness.save_config(self.config)
        result, reply = self.run_main('catalog-uninstall', 'semgrep')
        self.assertEqual(result, 0, reply)
        self.assertEqual(reply['message'], 'Desinstalado. Vuelve a Automático.')
        self.assertEqual(self.unuse_commands(), [['unuse', '--global', '--no-prune', 'semgrep'], ['unuse', '--global', '--no-prune', 'uv']])
        saved = harness.load_config()
        self.assertEqual(saved['catalogInstalls'], {})
        self.assertNotIn('semgrep', saved['localToolsSettings']['probes'])
        self.assertEqual(catalog.preference(saved, 'semgrep'), 'auto')
        self.assertEqual(catalog.preference(saved, 'semgrep', scope), 'off')
        self.assertEqual(saved['foreign'], 'keep')
        self.assertEqual(catalog.inspect('semgrep', catalog.native_tools.mise_inventory()[0])['installation'], 'missing')

    def test_shared_dependency_is_removed_only_with_last_record(self):
        harness.activate_tool(self.config, 'semgrep')
        harness.activate_tool(self.config, 'rg')
        self.config['catalogInstalls']['rg']['deps'] = ['uv']
        harness.save_config(self.config)
        result, reply = self.run_main('catalog-uninstall', 'semgrep')
        self.assertEqual(result, 0, reply)
        self.assertEqual(self.unuse_commands(), [['unuse', '--global', '--no-prune', 'semgrep']])
        self.assertEqual(harness.load_config()['catalogInstalls']['rg']['versions']['uv'], ['1.2.3'])
        result, reply = self.run_main('catalog-uninstall', 'rg')
        self.assertEqual(result, 0, reply)
        self.assertEqual(self.unuse_commands()[-2:], [['unuse', '--global', '--no-prune', 'ripgrep'], ['unuse', '--global', '--no-prune', 'uv']])
        self.assertEqual(json.loads(self.state.read_text())['uv'], [])

    def test_uninstall_verification_failure_names_tool(self):
        harness.activate_tool(self.config, 'rg')
        with patch.dict(os.environ, {'TEST_MISE_UNINSTALL_NOOP': 'ripgrep'}):
            result, reply = self.run_main('catalog-uninstall', 'rg')
        self.assertEqual(result, 1)
        self.assertIn('ripgrep', reply['error'])
        saved = harness.load_config()
        self.assertEqual(saved, self.config)
        rows = {row['id']: row for row in catalog.snapshot(saved)}
        self.assertEqual(rows['rg']['uninstallItems'], ['ripgrep'])
        result, reply = self.run_main('catalog-uninstall', 'rg')
        self.assertEqual(result, 0, reply)

    def test_failed_tool_install_keeps_dependency_ownership_across_retry(self):
        with patch.dict(os.environ, {'TEST_MISE_FAIL': 'semgrep'}):
            for _ in range(2):
                with self.assertRaises(catalog.native_plugins.PluginError):
                    harness.activate_tool(self.config, 'semgrep')
                self.config = harness.load_config()
                self.assertEqual(self.config['catalogInstalls'],
                                 {'semgrep': {'deps': ['uv'], 'versions': {'uv': ['1.2.3']}}})
        harness.activate_tool(self.config, 'semgrep')
        self.assertEqual(harness.load_config()['catalogInstalls'],
                         {'semgrep': {'deps': ['uv'], 'versions': {'uv': ['1.2.3'], 'semgrep': ['1.2.3']}}})

    def test_uninstall_preserves_other_versions_and_uses_exact_owned_versions(self):
        foreign = self.root / 'foreign/bin/rg'
        foreign.parent.mkdir(parents=True)
        foreign.write_text('#!/bin/sh\nprintf \"ripgrep 0.9.0\\n\"\n')
        foreign.chmod(0o755)
        self.state.write_text(json.dumps({'ripgrep': [dict(active=False, installed=True,
                                     version='0.9.0', install_path=str(self.root / 'foreign'))]}))
        harness.activate_tool(self.config, 'rg')
        self.assertEqual(self.config['catalogInstalls']['rg']['versions'], {'ripgrep': ['1.2.3']})
        result, reply = self.run_main('catalog-uninstall', 'rg')
        self.assertEqual(result, 0, reply)
        rows = json.loads(self.state.read_text())['ripgrep']
        self.assertEqual([row['version'] for row in rows], ['0.9.0'])
        commands = list(map(json.loads, self.log.read_text().splitlines()))
        self.assertIn(['uninstall', 'ripgrep@1.2.3'], commands)
        self.assertEqual(self.unuse_commands(), [['unuse', '--global', '--no-prune', 'ripgrep']])

    def test_failed_dependency_removal_keeps_record_and_probe_for_retry(self):
        harness.activate_tool(self.config, 'semgrep')
        with patch.dict(os.environ, {'TEST_MISE_UNINSTALL_NOOP': 'uv'}):
            result, reply = self.run_main('catalog-uninstall', 'semgrep')
        self.assertEqual(result, 1, reply)
        self.assertIn('Semgrep', reply['error'])
        self.assertEqual(harness.load_config(), self.config)
        result, reply = self.run_main('catalog-uninstall', 'semgrep')
        self.assertEqual(result, 0, reply)

    def test_inventory_verification_failure_keeps_record_and_names_tool(self):
        harness.activate_tool(self.config, 'rg')
        with patch.object(harness, 'mise_inventory', return_value=({}, 'inventario falló')):
            with self.assertRaisesRegex(harness.HarnessError, 'ripgrep'):
                harness.uninstall_tool(self.config, 'rg')
        self.assertEqual(harness.load_config(), self.config)

    def test_legacy_record_never_prunes_unrecorded_versions(self):
        harness.activate_tool(self.config, 'rg')
        self.config['catalogInstalls']['rg'].pop('versions')
        harness.save_config(self.config)
        result, reply = self.run_main('catalog-uninstall', 'rg')
        self.assertEqual(result, 0, reply)
        self.assertEqual([row['version'] for row in json.loads(self.state.read_text())['ripgrep']], ['1.2.3'])

    def test_load_config_accepts_versions_and_rejects_unsafe_shapes(self):
        record = {'deps': ['uv'], 'versions': {'semgrep': ['1.2.3'], 'uv': ['0.9.1']}}
        harness.save_config({**self.config, 'catalogInstalls': {'semgrep': record}})
        self.assertEqual(harness.load_config()['catalogInstalls']['semgrep'], record)
        for versions in (None, [], {'semgrep': '1.2.3'}, {'semgrep': [1]},
                         {'other': ['1.2.3']}, {'uv': ['--all']}, {'uv': ['1@2']}, {'uv': ['']}):
            with self.subTest(versions=versions):
                harness.save_config({**self.config, 'catalogInstalls': {'semgrep': {'deps': ['uv'], 'versions': versions}}})
                before = harness.config_path().read_bytes()
                with self.assertRaises(harness.HarnessError):
                    harness.load_config()
                self.assertEqual(harness.config_path().read_bytes(), before)

    def test_snapshot_lists_only_removable_items(self):
        harness.activate_tool(self.config, 'semgrep')
        catalog.install('rg')
        rows = {row['id']: row for row in catalog.snapshot(self.config)}
        self.assertEqual(rows['semgrep'].get('uninstallItems'), ['Semgrep', 'uv'])
        self.assertEqual(rows['rg'].get('uninstallItems'), [])
        self.config['catalogInstalls']['rg'] = {'deps': ['uv']}
        rows = {row['id']: row for row in catalog.snapshot(self.config)}
        self.assertEqual(rows['semgrep']['uninstallItems'], ['Semgrep'])
        self.assertEqual(rows['rg']['uninstallItems'], ['ripgrep'])

    def test_load_config_validates_install_records_without_rewriting(self):
        for records in (None, [], {'unknown': {'deps': []}}, {'rg': []}, {'rg': {}},
                        {'rg': {'deps': 'uv'}}, {'rg': {'deps': [1]}}, {'rg': {'deps': [], 'other': True}}):
            with self.subTest(records=records):
                harness.save_config({**self.config, 'catalogInstalls': records})
                before = harness.config_path().read_bytes()
                with self.assertRaises(harness.HarnessError):
                    harness.load_config()
                self.assertEqual(harness.config_path().read_bytes(), before)
        self.config['catalogInstalls'] = {'semgrep': {'deps': ['uv']}, 'rg': {'deps': []}}
        harness.save_config(self.config)
        self.assertEqual(harness.load_config(), self.config)


if __name__ == '__main__':
    unittest.main()
