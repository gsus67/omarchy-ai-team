import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
spec = importlib.util.spec_from_file_location("harness", ROOT / "backend/harness.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {**os.environ, "LC_ALL": "es", "HOME": str(self.root), "XDG_CONFIG_HOME": str(self.root / "config"),
                    "CLAUDE_CONFIG_DIR": str(self.root / "claude"), "CODEX_HOME": str(self.root / "codex"),
                    "PATH": str(self.bin), "OMARCHY_PATH": str(self.root / "omarchy")}
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def executable(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
        return path

    def run_cli(self, *args):
        result = subprocess.run([sys.executable, str(ROOT / "backend/harness.py"), *args],
                                capture_output=True, text=True, env=self.env)
        return result, json.loads(result.stdout)

    def run_patched_cli(self, *args, agent_tool=None, install=None):
        output = io.StringIO()
        installed = agent_tool or (lambda agent, inventory: {"installation": "installed"})
        install_runtime = install or (lambda agent, action: {"changed": True})
        with patch.object(harness, "agent_tool", side_effect=installed), \
             patch.object(harness, "manage_agent_runtime", side_effect=install_runtime), \
             patch.object(harness, "manage_component") as manage_component, \
             patch.object(harness, "sync_launcher_menu") as sync_launcher_menu, \
             contextlib.redirect_stdout(output):
            result = harness.main(args)
        return result, json.loads(output.getvalue()), manage_component, sync_launcher_menu

    def test_set_agents_adds_and_removes_in_one_call(self):
        config = harness.load_config()
        config["agents"] = {"claude": True}
        harness.save_config(config)
        result, reply, manage_component, _ = self.run_patched_cli("set-agents", "codex,pi")
        self.assertEqual(result, 0)
        self.assertTrue(reply["ok"])
        self.assertEqual(harness.load_config()["agents"], {
            "claude": False, "codex": True, "codex-2": False, "pi": True,
        })
        self.assertEqual(manage_component.call_count, len(harness.MANAGED))
        self.assertEqual({call.args[1:] for call in manage_component.call_args_list},
                         {(key, "sync") for key in harness.MANAGED})

    def test_set_agents_no_change_is_noop(self):
        config = harness.load_config()
        config["agents"] = {"codex": True}
        harness.save_config(config)
        result, reply, manage_component, sync_launcher_menu = self.run_patched_cli("set-agents", "codex")
        self.assertEqual(result, 0)
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["message"], "Agentes guardados. Abre sesiones nuevas.")
        manage_component.assert_not_called()
        sync_launcher_menu.assert_not_called()

    def test_set_agents_partial_install_error_keeps_other_changes(self):
        config = harness.load_config()
        harness.save_config(config)
        def installation(agent, inventory):
            return {"installation": "launcher-only" if agent == "pi" else "installed"}
        def install(agent, action):
            if agent == "pi":
                raise harness.HarnessError("instalación fallida")
            return {"changed": True}
        result, reply, manage_component, _ = self.run_patched_cli(
            "set-agents", "codex,pi", agent_tool=installation, install=install)
        self.assertEqual(result, 1)
        self.assertFalse(reply["ok"])
        self.assertIn("Pi:", reply["error"])
        self.assertTrue(harness.load_config()["agents"]["codex"])
        self.assertFalse(harness.load_config()["agents"]["pi"])
        self.assertEqual(manage_component.call_count, len(harness.MANAGED))

    def test_set_agents_none_clears_selection(self):
        config = harness.load_config()
        config["agents"] = {agent: True for agent in harness.AGENTS}
        harness.save_config(config)
        result, reply, _, _ = self.run_patched_cli("set-agents", "none")
        self.assertEqual(result, 0)
        self.assertTrue(reply["ok"])
        self.assertEqual(harness.load_config()["agents"], {agent: False for agent in harness.AGENTS})

    def test_set_agents_rejects_missing_profile(self):
        config = harness.load_config()
        original = harness.config_path()
        harness.save_config(config)
        before = original.read_bytes()
        def installation(agent, inventory):
            if agent == "codex-2":
                return {"installation": "missing-profile"}
            return {"installation": "installed"}
        result, reply, manage_component, _ = self.run_patched_cli(
            "set-agents", "codex-2", agent_tool=installation)
        self.assertEqual(result, 1)
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"], "Perfil codex-2 no disponible.")
        self.assertEqual(original.read_bytes(), before)
        manage_component.assert_not_called()

    def test_set_agent_still_toggles_one_profile(self):
        result, reply, manage_component, _ = self.run_patched_cli("set-agent", "codex", "true")
        self.assertEqual(result, 0)
        self.assertTrue(reply["ok"])
        self.assertEqual(harness.load_config()["agents"], {
            "claude": False, "codex": True, "codex-2": False, "pi": False,
        })
        self.assertEqual(manage_component.call_count, len(harness.MANAGED))

    def test_set_agent_reconciles_already_selected_profile(self):
        config = harness.load_config()
        config["agents"] = {agent: agent == "codex" for agent in harness.AGENTS}
        harness.save_config(config)
        installs = []

        def installation(agent, inventory):
            return {"installation": "missing" if agent == "codex" else "installed"}

        def install(agent, action):
            installs.append((agent, action))
            return {"changed": True}

        result, reply, manage_component, sync_launcher_menu = self.run_patched_cli(
            "set-agent", "codex", "true", agent_tool=installation, install=install)
        self.assertEqual(result, 0)
        self.assertTrue(reply["ok"])
        self.assertEqual(installs, [("codex", "install")])
        self.assertEqual(manage_component.call_count, len(harness.MANAGED))
        self.assertEqual({call.args[1:] for call in manage_component.call_args_list},
                         {(key, "sync") for key in harness.MANAGED})
        sync_launcher_menu.assert_called_once()

    def test_status_does_not_run_agents_or_create_configuration(self):
        marker = self.root / "agent-was-executed"
        self.executable("codex", f'touch "{marker}"')
        result, reply = self.run_cli("status")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(reply["ok"])
        codex = next(row for row in reply["data"]["agents"] if row["id"] == "codex")
        self.assertEqual(codex["installation"], "installed")
        self.assertFalse(codex["enabled"])
        self.assertFalse(marker.exists())
        self.assertFalse(harness.config_path().parent.exists())

    def test_status_reports_goals_without_running_agents(self):
        marker = self.root / "agent-was-executed"
        self.executable("codex", f'/usr/bin/touch "{marker}"')
        result, reply = self.run_cli("status")
        self.assertEqual(result.returncode, 0)
        data = reply["data"]
        self.assertEqual([row["id"] for row in data["goals"]], ["quality", "memory", "tokens", "security"])
        self.assertEqual(data["summary"], {"text": "Selecciona un agente", "page": "", "state": "pending"})
        self.assertNotIn("securityAudit", data)
        self.assertFalse(marker.exists())
        self.assertFalse(harness.config_path().parent.exists())
        config = harness.load_config()
        config.update(tokensEnabled=True, securityEnabled=True,
                      lastSecurityAudit={"findingCount": 2, "severities": {"high": 2},
                                         "checkedAt": "2026-10-29T12:00:00+00:00"})
        harness.save_config(config)
        original = harness.config_path().read_bytes()
        result, reply = self.run_cli("status")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("securityAudit", reply["data"])
        self.assertEqual(reply["data"]["summary"]["text"], "Selecciona un agente")
        self.assertTrue(all(row["enabled"] for row in reply["data"]["goals"][2:]))
        self.assertEqual(harness.config_path().read_bytes(), original)
        self.assertFalse(marker.exists())

    def test_load_config_ignores_old_agentshield_data_without_rewriting(self):
        import project_scope
        scope = project_scope.project_key(self.root)
        for audit in (None, [], {"findingCount": 2}, "obsolete"):
            with self.subTest(audit=audit):
                config = {"schemaVersion": 1, "agents": {}, "lastSecurityAudit": audit,
                          "localToolsSettings": {
                              "defaults": {"agentshield": "obsolete", "rg": "on"},
                              "projects": {scope: {"agentshield": None, "jq": "off"}},
                              "probes": {"agentshield": [], "rg": {"stamp": "keep"}}},
                          "foreign": "keep"}
                harness.save_config(config)
                original = harness.config_path().read_bytes()
                loaded = harness.load_config()
                self.assertNotIn("lastSecurityAudit", loaded)
                self.assertEqual(loaded["localToolsSettings"], {
                    "defaults": {"rg": "on"}, "projects": {scope: {"jq": "off"}},
                    "probes": {"rg": {"stamp": "keep"}}})
                self.assertEqual(loaded["foreign"], "keep")
                self.assertEqual(harness.config_path().read_bytes(), original)

    def test_load_config_drops_legacy_tool_without_adding_settings_sections(self):
        config = {"schemaVersion": 1, "localToolsSettings": {"defaults": {"agentshield": "on", "rg": "auto"}}}
        harness.save_config(config)
        original = harness.config_path().read_bytes()
        self.assertEqual(harness.load_config(), {
            "schemaVersion": 1, "localToolsSettings": {"defaults": {"rg": "auto"}}})
        self.assertEqual(harness.config_path().read_bytes(), original)

    def test_obsolete_component_settings_are_ignored_without_rewriting(self):
        config = harness.load_config()
        config.update(retiredEnabled="obsolete", retired=["unused"], workflowSettings="obsolete")
        harness.save_config(config)
        original = harness.config_path().read_bytes()
        result, reply = self.run_cli("status")
        self.assertEqual(result.returncode, 0)
        self.assertEqual({row["id"] for row in reply["data"]["components"]},
                         {"superpowers", "memory", "local-tools", "team"})
        self.assertNotIn("workflow", reply["data"])
        self.assertEqual(harness.config_path().read_bytes(), original)
        self.assertEqual(harness.load_config(), config)

    def test_lazy_wrapper_is_not_an_installed_agent(self):
        wrapper = self.executable("claude", 'mise use -g --quiet "claude"\nexit 99')
        self.assertTrue(harness.is_launcher(wrapper))
        self.assertEqual(harness.detect_tool("claude", {})["installation"], "launcher-only")
        result, reply = self.run_cli("set-agent", "claude", "true")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(reply["ok"])
        self.assertFalse(harness.config_path().exists())

    def test_mise_shim_without_install_is_not_executable_agent(self):
        mise = self.executable("mise", "exit 0")
        (self.bin / "codex").symlink_to(mise)
        self.assertEqual(harness.detect_tool("codex", {})["installation"], "launcher-only")

    def test_mise_inventory_resolves_installed_binary_without_running_it(self):
        self.executable("codex", 'mise use -g "codex"')
        installation = self.root / "installed"
        (installation / "bin").mkdir(parents=True)
        actual = installation / "bin/codex"
        actual.write_text("#!/bin/sh\nexit 99\n")
        actual.chmod(0o755)
        inventory = {"codex": [{"installed": True, "active": True, "version": "1.2.3",
                                  "install_path": str(installation)}]}
        result = harness.detect_tool("codex", inventory)
        self.assertEqual(result["binary"], str(actual))
        self.assertEqual(result["version"], "1.2.3")

    def test_custom_path_install_wins_over_other_mise_version(self):
        actual = self.executable("codex", "exit 0")
        inventory = {"codex": [{"installed": True, "active": True, "version": "9.0",
                                  "install_path": str(self.root / "other-install")}]}
        self.assertEqual(harness.detect_tool("codex", inventory)["binary"], str(actual))
        self.assertEqual(harness.detect_tool("codex", inventory)["version"], "")

    def test_preferences_persist_and_disable_keeps_other_settings(self):
        self.executable("codex", "exit 0")
        config = harness.load_config()
        config["future-setting"] = "keep"
        harness.save_config(config)
        for enabled in ("true", "false"):
            result, reply = self.run_cli("set-agent", "codex", enabled)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(next(row for row in reply["data"]["agents"] if row["id"] == "codex")["enabled"], enabled == "true")
        self.assertEqual(harness.load_config()["future-setting"], "keep")
        self.assertEqual(stat.S_IMODE(harness.config_path().stat().st_mode), 0o600)
        self.assertEqual(list(harness.config_path().parent.glob(".settings-*")), [])

    def test_invalid_config_is_preserved(self):
        path = harness.config_path()
        path.parent.mkdir(parents=True)
        values = ['{"schemaVersion":1,"agents":{"codex":"false"}}', "{broken", '{"schemaVersion":2}', '{"schemaVersion":true}']
        for value in values:
            with self.subTest(value=value):
                path.write_text(value)
                result, reply = self.run_cli("set-agent", "codex", "false")
                self.assertEqual(result.returncode, 1)
                self.assertFalse(reply["ok"])
                self.assertEqual(path.read_text(), value)

    def test_symlinked_config_is_not_modified(self):
        original = self.root / "other.json"
        original.write_text('{"schemaVersion":1}')
        path = harness.config_path()
        path.parent.mkdir(parents=True)
        path.symlink_to(original)
        result, reply = self.run_cli("set-agent", "codex", "false")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(reply["ok"])
        self.assertTrue(path.is_symlink())
        self.assertEqual(original.read_text(), '{"schemaVersion":1}')

    def test_agent_update_invalidates_plugin_verification(self):
        tool = {"installation": "installed", "binary": "/installed/codex-2", "version": "2"}
        plugin = {"installed": True, "enabled": True, "verified": True, "version": "7.0.1", "detail": "checked"}
        config = harness.load_config()
        config["superpowers"] = {"codex": {"agentBinary": "/installed/codex-1", "agentVersion": "1"}}
        with patch.object(harness, "detect_tool", return_value=tool), \
             patch.object(harness.native_plugins, "inspect", side_effect=lambda *args: dict(plugin)):
            before = harness.status(config)
            row = next(c for c in before["components"] if c["id"] == "superpowers")
            self.assertFalse(next(t for t in row["targets"] if t["agent"] == "codex")["verified"])
            config["superpowers"]["codex"].update(agentBinary=tool["binary"], agentVersion=tool["version"])
            after = harness.status(config)
            row = next(c for c in after["components"] if c["id"] == "superpowers")
            self.assertTrue(next(t for t in row["targets"] if t["agent"] == "codex")["verified"])

    def test_deferred_providers_are_not_probed(self):
        probed = []
        real_which = harness.shutil.which
        def which(name):
            probed.append(name)
            return real_which(name)
        with patch.object(harness.shutil, "which", side_effect=which):
            result = harness.status()
        self.assertEqual(result["deferred"], ["Grok", "Nous"])
        self.assertFalse({"grok", "nous"}.intersection(probed))


if __name__ == "__main__":
    unittest.main()
