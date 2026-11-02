import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import harness


class ComponentSelectionTests(unittest.TestCase):
    def setUp(self):
        # Existing fixtures cover the three core profiles. Pi has separate
        # native-adapter tests; never let expanding AGENTS invoke installers.
        agents = patch.object(harness, "AGENTS", {key:harness.AGENTS[key] for key in ("claude","codex","codex-2")})
        agents.start(); self.addCleanup(agents.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        binary_dir = self.root / "bin"; binary_dir.mkdir()
        for agent in harness.AGENTS:
            binary = binary_dir / agent
            binary.write_text("#!/bin/sh\nexit 0\n"); binary.chmod(0o755)
        environment = patch.dict(os.environ, {"LC_ALL": "es", "HOME": str(self.root), "PATH": str(binary_dir),
                    "XDG_CONFIG_HOME": str(self.root / "config"), "CODEX_HOME": str(self.root / "codex"),
                    "CLAUDE_CONFIG_DIR": str(self.root / "claude")}, clear=True)
        environment.start(); self.addCleanup(environment.stop)
        (self.root / ".codex-2").mkdir()
        self.native = {agent: {"installed": False, "enabled": False} for agent in harness.AGENTS}
        self.actions = []
        self.fail_agent = None
        for p in (patch.object(harness, "mise_inventory", return_value=({}, "")),
                  patch.object(harness.native_plugins, "inspect", side_effect=self.inspect),
                  patch.object(harness.native_plugins, "apply", side_effect=self.apply)):
            p.start(); self.addCleanup(p.stop)

    def target(self, agent):
        return "codex-2" if agent == "codex" and Path(os.environ["CODEX_HOME"]).name == ".codex-2" else agent

    def inspect(self, agent, receipt=None):
        agent = self.target(agent)
        return {"id": harness.native_plugins.PLUGIN, **self.native[agent], "version": "7.0.1",
                "verified": bool(receipt and receipt.get("digest")), "detail": "test state"}

    def apply(self, agent, binary, action, receipt=None):
        agent = self.target(agent)
        self.actions.append((agent, action))
        if agent == self.fail_agent:
            raise harness.native_plugins.PluginError("synthetic connection failure")
        if action == "enable":
            self.native[agent].update(installed=True, enabled=True)
        elif action == "disable":
            self.native[agent]["enabled"] = False
        elif not self.native[agent]["installed"]:
            raise harness.native_plugins.PluginError("not installed")
        return {"id": harness.native_plugins.PLUGIN, "version": "7.0.1", "digest": "fixture",
                "home": str(self.root / agent), "checkedAt": "fixture"}

    def command(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = harness.main(list(args))
        return code, json.loads(output.getvalue())

    def component(self, reply):
        return next(c for c in reply["data"]["components"] if c["id"] == "superpowers")

    def configured(self, selected, enabled=True):
        value = harness.load_config()
        value["agents"].update({agent: agent in selected for agent in harness.AGENTS})
        value["superpowersEnabled"] = enabled
        value["foreign"] = "preserve"
        harness.save_config(value)


    def test_default_launcher_requires_selection_and_preserves_it_after_deselection(self):
        self.configured([], enabled=False)
        code, _ = self.command("launch-default")
        self.assertEqual(code, 1)
        self.assertEqual(self.actions, [])
        code, _ = self.command("set-default-agent", "codex-2")
        self.assertEqual(code, 1)
        self.command("set-agent", "codex-2", "true")
        code, reply = self.command("set-default-agent", "codex-2")
        self.assertEqual(code, 0)
        self.assertEqual(reply["data"]["launcher"]["defaultAgent"], "codex-2")
        import native_launcher
        with patch.object(native_launcher, "launch") as launch:
            code, _ = self.command("launch-default")
            self.assertEqual(code, 0)
            self.assertEqual(launch.call_args.args[1:], ("codex-2", self.root/".codex-2"))
            self.command("set-agent", "codex-2", "false")
            code, reply = self.command("launch-default")
            self.assertEqual(code, 1)
            self.assertEqual(launch.call_count, 1)
        self.assertEqual(harness.load_config()["defaultAgent"], "codex-2")
        self.assertFalse(reply["data"]["launcher"]["canLaunch"])
        self.assertEqual(harness.load_config()["foreign"], "preserve")

    def test_launch_leaves_enabled_component_reconciliation_to_explicit_actions(self):
        self.configured(["codex"], enabled=True)
        self.fail_agent = "codex"
        import native_launcher
        with patch.object(native_launcher, "launch") as launch:
            code, reply = self.command("launch", "codex")
            self.assertEqual(code, 0)
            launch.assert_called_once()
            self.assertEqual(self.actions, [])
            self.assertTrue(self.component(reply)['enabled'])

    def test_menu_preference_without_selected_profiles_never_installs_or_launches(self):
        self.configured([], enabled=False)
        code, reply = self.command("launcher-menu", "true")
        self.assertEqual(code, 0)
        self.assertTrue(reply["data"]["launcher"]["menu"]["enabled"])
        self.assertEqual(self.actions, [])
        self.assertFalse((self.root/".local/share/applications").exists())

    def test_memory_backup_settings_can_be_prepared_without_agents_timers_or_models(self):
        self.configured([],enabled=False)
        destination=self.root/'backup folder';destination.mkdir()
        import memory_backups
        with patch.object(memory_backups.subprocess,'run') as external:
            code,reply=self.command('memory-configure',str(destination),'--hours','6','--keep','3')
            self.assertEqual(code,0)
            self.assertFalse(any('systemctl' in str(call) for call in external.call_args_list))
        settings=reply['data']['memory']['backupSettings']
        self.assertEqual((settings['hours'],settings['keep'],settings['enabled']),(6,3,False))
        self.assertTrue((destination/memory_backups.MARKER).is_file())
        self.assertFalse(any(a['enabled'] for a in reply['data']['agents']))
        self.assertEqual(self.actions,[])
        self.assertEqual(harness.load_config()['foreign'],'preserve')

    def test_global_component_can_be_prepared_before_selecting_agents(self):
        code, reply = self.command("superpowers-all", "enable")
        self.assertEqual(code, 0)
        self.assertTrue(self.component(reply)["enabled"])
        self.assertEqual(self.component(reply)["selectedCount"], 0)
        self.assertEqual(self.actions, [])
        code, reply = self.command("set-agent", "codex", "true")
        self.assertEqual(code, 0)
        self.assertTrue(self.native["codex"]["enabled"])
        self.assertFalse(self.native["claude"]["enabled"])
        self.assertEqual(self.component(reply)["connectedCount"], 1)

    def test_selecting_an_agent_with_component_off_does_not_install_it(self):
        self.configured([], enabled=False)
        code, _ = self.command("set-agent", "codex", "true")
        self.assertEqual(code, 0)
        self.assertEqual(self.actions, [])
        self.assertEqual(harness.load_config()["foreign"], "preserve")

    def test_deselecting_one_agent_leaves_other_connected_and_keeps_component_preference(self):
        self.configured(["claude", "codex"])
        self.command("superpowers-all", "enable")
        code, reply = self.command("set-agent", "claude", "false")
        self.assertEqual(code, 0)
        self.assertFalse(self.native["claude"]["enabled"])
        self.assertTrue(self.native["claude"]["installed"])
        self.assertTrue(self.native["codex"]["enabled"])
        self.assertTrue(self.component(reply)["enabled"])
        self.assertEqual(self.component(reply)["selectedCount"], 1)

    def test_single_component_disable_disconnects_only_managed_connections(self):
        self.configured(["codex"])
        for state in self.native.values():state.update(installed=True, enabled=True)
        code, reply = self.command("superpowers-all", "disable")
        self.assertEqual(code, 0)
        self.assertFalse(self.component(reply)["enabled"])
        self.assertTrue(self.native["codex"]["installed"])
        self.assertFalse(self.native["codex"]["enabled"])
        others = [state for agent, state in self.native.items() if agent != "codex"]
        self.assertTrue(all(state["enabled"] for state in others))
        self.assertEqual(self.component(reply)["activeCount"], 0)
        self.assertNotIn("pendiente", self.component(reply)["detail"])

    def test_update_uses_only_selected_agent_and_preserves_disabled_component(self):
        self.configured(["codex"], enabled=False)
        for state in self.native.values():state.update(installed=True, enabled=False)
        code, reply = self.command("superpowers-all", "update")
        self.assertEqual(code, 0)
        self.assertEqual(self.actions, [("codex", "update")])
        self.assertFalse(self.component(reply)["enabled"])
        self.assertEqual(harness.load_config()["foreign"], "preserve")

    def test_partial_failure_reports_actual_connections_and_can_retry(self):
        self.configured(["claude", "codex"], enabled=False)
        self.fail_agent = "codex"
        code, reply = self.command("superpowers-all", "enable")
        self.assertEqual(code, 1)
        self.assertFalse(reply["ok"])
        self.assertTrue(self.component(reply)["enabled"])
        self.assertEqual(self.component(reply)["connectedCount"], 1)
        self.assertEqual(self.component(reply)["selectedCount"], 2)
        self.fail_agent = None
        code, reply = self.command("superpowers-all", "enable")
        self.assertEqual(code, 0)
        self.assertEqual(self.component(reply)["connectedCount"], 2)

    def test_all_selected_profiles_receive_one_component_and_keep_distinct_homes(self):
        self.configured(list(harness.AGENTS), enabled=False)
        code, reply = self.command("superpowers-all", "enable")
        self.assertEqual(code, 0)
        self.assertEqual(self.component(reply)["connectedCount"], 3)
        self.assertEqual(self.actions, [(agent, "enable") for agent in harness.AGENTS])
        self.assertEqual({r["home"] for r in harness.load_config()["superpowers"].values()},
                         {str(self.root / agent) for agent in harness.AGENTS})

    def test_failure_on_one_profile_does_not_skip_remaining_selected_profiles(self):
        self.configured(list(harness.AGENTS), enabled=False)
        self.fail_agent = "codex"
        code, reply = self.command("superpowers-all", "enable")
        self.assertEqual(code, 1)
        self.assertIn("Codex:", reply["error"])
        self.assertTrue(self.native["codex-2"]["enabled"])
        self.assertEqual(self.component(reply)["connectedCount"], 2)

    def test_selecting_absent_second_profile_creates_empty_private_directory(self):
        (self.root / ".codex-2").rmdir()
        original = harness.manage_component
        # This fixture only fakes Superpowers; other components are covered in test_zero_touch.
        with patch.object(harness, "manage_component",
                               lambda config, key, action, **kw: original(config, key, action, **kw)
                               if key == "superpowers" else None):
            code, reply = self.command("set-agent", "codex-2", "true")
        self.assertEqual(code, 0)
        self.assertEqual((self.root / ".codex-2").stat().st_mode & 0o777, 0o700)
        self.assertEqual(list((self.root / ".codex-2").iterdir()), [])
        self.assertTrue(next(a for a in reply["data"]["agents"] if a["id"] == "codex-2")["enabled"])

    def test_named_profiles_ignore_active_codex_home_and_restore_it_after_errors(self):
        before = os.environ["CODEX_HOME"]
        for agent, directory in (("codex", ".codex"), ("codex-2", ".codex-2")):
            with self.assertRaises(RuntimeError):
                with harness.agent_profile(agent) as cli:
                    self.assertEqual(cli, "codex")
                    self.assertEqual(harness.native_plugins.agent_home(cli), self.root / directory)
                    raise RuntimeError("synthetic")
            self.assertEqual(os.environ["CODEX_HOME"], before)

    def test_failed_disable_keeps_actual_active_count_visible_until_retry(self):
        self.configured(list(harness.AGENTS))
        self.command("superpowers-all", "enable")
        self.fail_agent = "codex"
        code, reply = self.command("superpowers-all", "disable")
        self.assertEqual(code, 1)
        self.assertFalse(self.component(reply)["enabled"])
        self.assertEqual(self.component(reply)["activeCount"], 1)
        self.assertIn("Desactivación pendiente", self.component(reply)["detail"])
        self.assertFalse(self.native["codex-2"]["enabled"])
        self.fail_agent = None
        code, reply = self.command("superpowers-all", "disable")
        self.assertEqual(code, 0)
        self.assertEqual(self.component(reply)["activeCount"], 0)


    def test_invalid_global_preference_is_preserved_without_native_operations(self):
        value = harness.load_config(); value["superpowersEnabled"] = "false"
        harness.save_config(value); before = harness.config_path().read_bytes()
        code, reply = self.command("set-agent", "codex", "true")
        self.assertEqual(code, 1)
        self.assertFalse(reply["ok"])
        self.assertEqual(harness.config_path().read_bytes(), before)
        self.assertEqual(self.actions, [])

    @contextlib.contextmanager
    def memory_adapter(self):
        states = {key: {"installed": False, "enabled": False} for key in harness.AGENTS}
        actions, failure = [], {"agent": None}
        def inspect(agent, receipt=None):
            return {**states[self.target(agent)], "verified": bool(receipt and receipt.get("digest")), "version": "2.2.3", "detail": "synthetic memory"}
        def apply(agent, binary, action, receipt=None):
            key = self.target(agent); actions.append((key, action))
            if key == failure["agent"]:
                raise harness.native_plugins.PluginError("synthetic memory failure")
            if action in ("enable", "disable"):
                states[key].update(installed=True, enabled=action == "enable")
            return {"id": "ai-team-memory", "version": "2.2.3", "digest": "test", "home": str(self.root / key), "checkedAt": "test"}
        with patch.object(harness.shared_memory, "inspect", side_effect=inspect), \
             patch.object(harness.shared_memory, "apply", side_effect=apply):
            yield states, actions, failure

    def test_memory_control_uses_only_selected_profiles_and_preserves_choice_and_data(self):
        self.configured([], enabled=False)
        with self.memory_adapter() as (states, actions, _):
            code, reply = self.command("memory-all", "enable")
            self.assertEqual(code, 0)
            self.assertEqual(actions, [])
            for key in harness.AGENTS:
                code, reply = self.command("set-agent", key, "true")
                self.assertEqual(code, 0)
            component = next(c for c in reply["data"]["components"] if c["id"] == "memory")
            self.assertEqual(component["connectedCount"], 3)
            code, reply = self.command("set-agent", "claude", "false")
            self.assertEqual(code, 0)
            self.assertTrue(states["claude"]["installed"])
            self.assertFalse(states["claude"]["enabled"])
            self.assertTrue(states["codex-2"]["enabled"])
            self.assertTrue(harness.load_config()["memoryEnabled"])
        self.assertEqual(harness.load_config()["foreign"], "preserve")

    def test_memory_partial_failure_reports_real_counts_and_retry_connects_remaining(self):
        self.configured(list(harness.AGENTS), enabled=False)
        with self.memory_adapter() as (states, _, failure):
            failure["agent"] = "codex"
            code, reply = self.command("memory-all", "enable")
            self.assertEqual(code, 1)
            component = next(c for c in reply["data"]["components"] if c["id"] == "memory")
            self.assertEqual(component["connectedCount"], 2)
            self.assertTrue(states["codex-2"]["enabled"])
            self.assertTrue(component["enabled"])
            failure["agent"] = None
            code, reply = self.command("memory-all", "enable")
            self.assertEqual(code, 0)
            self.assertTrue(all(row["enabled"] for row in states.values()))

    def test_invalid_memory_preference_cannot_enable_connections(self):
        value = harness.load_config(); value["memoryEnabled"] = "false"
        harness.save_config(value); before = harness.config_path().read_bytes()
        with self.memory_adapter() as (_, actions, _):
            code, reply = self.command("set-agent", "codex", "true")
            self.assertEqual(code, 1)
            self.assertFalse(reply["ok"])
            self.assertEqual(actions, [])
        self.assertEqual(harness.config_path().read_bytes(), before)

    def test_failed_memory_recheck_revokes_old_verification_until_retry(self):
        self.configured(list(harness.AGENTS), enabled=False)
        with self.memory_adapter() as (_, _, failure):
            self.assertEqual(self.command("memory-all", "enable")[0], 0)
            failure["agent"] = "codex"
            code, reply = self.command("memory-all", "verify")
            self.assertEqual(code, 1)
            component = next(c for c in reply["data"]["components"] if c["id"] == "memory")
            self.assertEqual(component["connectedCount"], 2)
            self.assertIn("synthetic memory failure", next(t for t in component["targets"] if t["agent"] == "codex")["detail"])
            failure["agent"] = None
            self.assertEqual(self.command("memory-all", "enable")[0], 0)


if __name__ == "__main__":
    unittest.main()
