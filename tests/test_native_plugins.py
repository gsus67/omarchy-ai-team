import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import native_plugins as plugins


class NativePluginsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"HOME": str(self.root), "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
                                              "CODEX_HOME": str(self.root / "codex")})
        environment.start()
        self.addCleanup(environment.stop)

    def installation(self, agent="claude"):
        home = plugins.agent_home(agent)
        path = home / "plugins/cache/superpowers-marketplace/superpowers/7.0.1"
        manifest = path / (".claude-plugin" if agent == "claude" else ".codex-plugin") / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": "superpowers", "version": "7.0.1",
                                        "repository": "https://github.com/obra/superpowers"}))
        skill = path / "skills/using-superpowers/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("upstream skill\n")
        if agent == "claude":
            (home / "settings.json").write_text(json.dumps({"enabledPlugins": {plugins.PLUGIN: True}, "foreign": "keep"}))
            (home / "plugins/installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
                plugins.PLUGIN: [{"scope": "user", "installPath": str(path), "version": "7.0.1"}]}}))
            (home / "plugins/known_marketplaces.json").write_text(json.dumps({"superpowers-marketplace": {
                "source": {"source": "github", "repo": plugins.MARKETPLACE}}}))
        else:
            (home / "config.toml").write_text('[marketplaces.superpowers-marketplace]\nsource = "https://github.com/obra/superpowers-marketplace.git"\n'
                                             '[plugins."superpowers@superpowers-marketplace"]\nenabled = false\n')
        return path

    def test_missing_status_neither_executes_nor_creates_directories(self):
        with patch.object(plugins.subprocess, "run") as run, patch.object(plugins.subprocess, "Popen") as popen:
            for agent in ("claude", "codex"):
                self.assertFalse(plugins.inspect(agent)["installed"])
            run.assert_not_called()
            popen.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_foreign_marketplace_alias_cannot_trigger_install_or_update(self):
        self.installation()
        file = plugins.agent_home("claude") / "plugins/known_marketplaces.json"
        file.write_text(json.dumps({"superpowers-marketplace": {"source": {"source": "github", "repo": "someone/fork"}}}))
        with patch.object(plugins, "command") as command:
            with self.assertRaises(plugins.PluginError):
                plugins.apply("claude", "/installed/claude", "update")
            command.assert_not_called()

    def test_nonofficial_plugin_manifest_is_rejected(self):
        path = self.installation()
        file = path / ".claude-plugin/plugin.json"
        value = json.loads(file.read_text()); value["repository"] = "https://github.com/someone/fork"
        file.write_text(json.dumps(value))
        with self.assertRaises(plugins.PluginError):
            plugins.inspect("claude")

    def test_changed_skills_invalidate_previous_verification(self):
        path = self.installation()
        receipt = {"id": plugins.PLUGIN, "home": str(plugins.agent_home("claude")),
                   "digest": plugins.fingerprint(path, "claude"), "checkedAt": "test"}
        self.assertTrue(plugins.inspect("claude", receipt)["verified"])
        (path / "skills/using-superpowers/SKILL.md").write_text("changed upstream skill\n")
        self.assertFalse(plugins.inspect("claude", receipt)["verified"])
        self.assertEqual(json.loads((plugins.agent_home("claude") / "settings.json").read_text())["foreign"], "keep")

    def test_duplicate_installations_are_not_overwritten(self):
        path = self.installation()
        file = plugins.agent_home("claude") / "plugins/installed_plugins.json"
        data = json.loads(file.read_text())
        data["plugins"]["superpowers@other-marketplace"] = [{"scope": "user", "installPath": str(path)}]
        file.write_text(json.dumps(data)); before = file.read_bytes()
        with self.assertRaises(plugins.PluginError):
            plugins.inspect("claude")
        self.assertEqual(before, file.read_bytes())

    def test_disabled_codex_stays_disabled_when_update_fails(self):
        path = self.installation("codex")
        with patch.object(plugins, "command", side_effect=[{}, plugins.PluginError("download failed")]), \
             patch.object(plugins, "set_codex_enabled") as set_enabled:
            with self.assertRaises(plugins.PluginError):
                plugins.apply("codex", "/installed/codex", "update")
            set_enabled.assert_called_once_with("/installed/codex", plugins.PLUGIN, False)
        self.assertTrue(path.is_dir())

    def test_external_codex_update_requires_verification_and_can_be_refreshed(self):
        original = self.installation("codex")
        receipt = {"id": plugins.PLUGIN, "version": "7.0.1", "cacheVersions": ["7.0.1"],
                   "home": str(plugins.agent_home("codex")), "digest": plugins.fingerprint(original, "codex")}
        updated = original.parent / "7.0.2"
        shutil.copytree(original, updated)
        file = updated / ".codex-plugin/plugin.json"
        data = json.loads(file.read_text()); data["version"] = "7.0.2"
        file.write_text(json.dumps(data))
        with self.assertRaises(plugins.PluginError):
            plugins.inspect("codex", receipt)
        inventory = {"installed": [{"pluginId": plugins.PLUGIN, "version": "7.0.2", "enabled": False}]}
        with patch.object(plugins, "command", return_value=inventory) as command, \
             patch.object(plugins, "set_codex_connection") as connection:
            fresh = plugins.apply("codex", "/installed/codex", "verify", receipt)
        self.assertTrue(all(call.args[1][:2] == ["plugin", "list"] for call in command.call_args_list))
        connection.assert_called_once_with("/installed/codex", plugins.PLUGIN, False, updated)
        state = plugins.inspect("codex", fresh)
        self.assertEqual(state["version"], "7.0.2")
        self.assertTrue(state["verified"])
        self.assertFalse(state["enabled"])

    def test_bootstrap_replacement_and_removal_preserve_foreign_instructions_exactly(self):
        original = "Keep custom rules.\n\n"
        first = plugins.codex_instructions(original, self.root / "version 1")
        self.assertTrue(first.startswith(original))
        second = plugins.codex_instructions(first, self.root / "version 2")
        self.assertNotIn("version 1", second)
        self.assertIn("version 2", second)
        self.assertEqual(plugins.codex_instructions(second), original)
        self.assertEqual(plugins.codex_instructions(second, self.root / "version 2"), second)

    def test_altered_or_duplicate_bootstrap_markers_are_rejected(self):
        block = plugins.codex_instructions("", self.root)
        for value in (block + block, block.replace(plugins.BOOTSTRAP_END, ""),
                      plugins.BOOTSTRAP_END + plugins.BOOTSTRAP_START, None):
            with self.assertRaises(plugins.PluginError):
                plugins.codex_instructions(value, self.root)

    def test_codex_activation_and_bootstrap_are_one_version_checked_native_write(self):
        home = plugins.agent_home("codex")
        layer = {"name": {"type": "user", "file": str(home / "config.toml")},
                 "version": "sha256:fixture", "config": {"developer_instructions": "Keep this.\n"}}
        with patch.object(plugins, "codex_request", side_effect=[{"layers": [layer]}, {}]) as request:
            plugins.set_codex_connection("/installed/codex", plugins.PLUGIN, True, self.root)
        call = request.call_args_list[1]
        self.assertEqual(call.args[1], "config/batchWrite")
        self.assertEqual(call.args[2]["expectedVersion"], "sha256:fixture")
        self.assertFalse(call.args[2]["reloadUserConfig"])
        self.assertEqual(call.args[2]["edits"][0]["value"], True)
        self.assertTrue(call.args[2]["edits"][1]["value"].startswith("Keep this.\n"))

    def test_matching_codex_connection_does_not_write(self):
        home = plugins.agent_home("codex")
        path = self.root / "cache/superpowers"
        config = {"developer_instructions": plugins.codex_instructions("Keep.\n", path),
                  "plugins": {plugins.PLUGIN: {"enabled": True}}}
        layer = {"name": {"type": "user", "file": str(home / "config.toml")}, "version": "v1", "config": config}
        with patch.object(plugins, "codex_request", side_effect=[{"layers": [layer]}]) as request:
            plugins.set_codex_connection("/installed/codex", plugins.PLUGIN, True, path)
        self.assertEqual(request.call_count, 1)

    def test_codex_connection_writes_only_the_difference(self):
        home = plugins.agent_home("codex")
        config = {"developer_instructions": "Keep.\n", "plugins": {plugins.PLUGIN: {"enabled": True}}}
        layer = {"name": {"type": "user", "file": str(home / "config.toml")}, "version": "v1", "config": config}
        with patch.object(plugins, "codex_request", side_effect=[{"layers": [layer]}, {}]) as request:
            plugins.set_codex_connection("/installed/codex", plugins.PLUGIN, True, self.root)
        written = request.call_args_list[1].args[2]
        self.assertEqual([edit["keyPath"] for edit in written["edits"]], ["developer_instructions"])
        self.assertEqual(written["expectedVersion"], "v1")

    def test_missing_native_config_version_prevents_writing(self):
        with patch.object(plugins, "codex_request", return_value={"layers": []}) as request:
            with self.assertRaises(plugins.PluginError):
                plugins.set_codex_connection("/installed/codex", plugins.PLUGIN, True, self.root)
        self.assertEqual(request.call_count, 1)

    def test_enabled_codex_without_bootstrap_is_not_reported_verified(self):
        path = self.installation("codex")
        file = plugins.agent_home("codex") / "config.toml"
        file.write_text(file.read_text().replace("enabled = false", "enabled = true"))
        receipt = {"id": plugins.PLUGIN, "home": str(plugins.agent_home("codex")),
                   "digest": plugins.fingerprint(path, "codex"), "checkedAt": "test"}
        self.assertFalse(plugins.inspect("codex", receipt)["verified"])
        file.write_text('developer_instructions = ' + json.dumps(plugins.codex_instructions("", path)) + '\n' + file.read_text())
        self.assertTrue(plugins.inspect("codex", receipt)["verified"])

    def test_cli_timeout_is_an_error_and_never_leaks_output(self):
        with patch.object(plugins.subprocess, "run", side_effect=subprocess.TimeoutExpired("native", 90, output="private output")):
            with self.assertRaises(plugins.PluginError) as error:
                plugins.command("/installed/claude", ["plugin", "install"])
        self.assertNotIn("private output", str(error.exception))

    def test_plugin_arguments_are_literal_and_no_shell_is_used(self):
        with patch.object(plugins.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "{}", "")) as run:
            plugins.command("/path with spaces/claude", ["plugin", "enable", plugins.PLUGIN], True)
        self.assertEqual(run.call_args.args[0], ["/path with spaces/claude", "plugin", "enable", plugins.PLUGIN])
        self.assertNotIn("shell", run.call_args.kwargs)


class IndependentInstructionBlockTests(unittest.TestCase):
    def test_updating_one_component_preserves_the_position_and_bytes_of_other_blocks(self):
        import native_plugins
        first = native_plugins.instruction_block('Foreign rules.\n', 'fixture-component', 'old')
        combined = native_plugins.instruction_block(first, 'memory', 'keep memory')
        changed = native_plugins.instruction_block(combined, 'fixture-component', 'new')
        self.assertEqual(changed, combined.replace('\nold\n', '\nnew\n'))
        self.assertEqual(native_plugins.instruction_block(changed, 'fixture-component', 'new'), changed)
        self.assertEqual(native_plugins.instruction_block(changed, 'memory', 'keep memory'), changed)


if __name__ == "__main__":
    unittest.main()


class NeutralCwdTests(unittest.TestCase):
    def test_native_commands_never_run_from_home(self):
        # From ~, Codex reads ~/.codex/config.toml as a project layer and leaks the
        # main profile into a second CODEX_HOME.
        seen = []
        def run(args, cwd, **kwargs):
            seen.append(Path(cwd))
            return subprocess.CompletedProcess(args, 0, '{}', '')
        with patch.object(plugins.subprocess, 'run', side_effect=run):
            plugins.command('/native/codex', ['plugin', 'list', '--json'], True)
        home = Path.home().resolve()
        self.assertTrue(seen)
        for cwd in seen:
            self.assertNotEqual(cwd.resolve(), home)
            self.assertFalse(cwd.resolve().is_relative_to(home))

    def test_app_server_does_not_start_in_home(self):
        captured = {}
        class Stop(Exception):
            pass
        def popen(args, cwd, **kwargs):
            captured['cwd'] = Path(cwd)
            raise Stop()
        with patch.object(plugins.subprocess, 'Popen', side_effect=popen), self.assertRaises(Stop):
            plugins.codex_request('/native/codex', 'config/read', {})
        self.assertFalse(captured['cwd'].resolve().is_relative_to(Path.home().resolve()))

    def test_skill_listing_uses_a_neutral_cwd(self):
        captured = {}
        def request(binary, method, params):
            captured.update(method=method, cwds=params['cwds'])
            return {'data': []}
        with patch.object(plugins, 'codex_request', side_effect=request):
            plugins.codex_skills('/native/codex')
        self.assertEqual(captured['method'], 'skills/list')
        self.assertFalse(Path(captured['cwds'][0]).resolve().is_relative_to(Path.home().resolve()))
