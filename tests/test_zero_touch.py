import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_snapshot import snapshot
import harness
import shared_memory


class ZeroTouchCase(unittest.TestCase):
    AGENT_BINARIES = ("claude", "codex", "pi", "muse")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"; self.bin.mkdir()
        for name in self.AGENT_BINARIES:
            binary = self.bin / name
            binary.write_text("#!/bin/sh\nexit 0\n"); binary.chmod(0o755)
        environment = patch.dict(os.environ, {"LC_ALL": "es", "HOME": str(self.root), "PATH": str(self.bin),
                                              "XDG_CONFIG_HOME": str(self.root / "config"),
                                              "XDG_DATA_HOME": str(self.root / "data")}, clear=True)
        environment.start(); self.addCleanup(environment.stop)
        inventory = patch.object(harness, "mise_inventory", return_value=({}, ""))
        inventory.start(); self.addCleanup(inventory.stop)

    def runtime(self):
        package = self.root / "package"
        (package / "scripts/lib").mkdir(parents=True, exist_ok=True)
        (package / "package.json").write_text(json.dumps({
            "name": "ecc-universal", "version": "2.2.3", "license": "MIT",
            "repository": {"url": "git+https://github.com/affaan-m/ECC.git"},
            "bin": {"ecc-memory-mcp": "scripts/memory-mcp.mjs"}}))
        for name in ("memory-mcp.mjs", "lib/memory-vault.js", "lib/memory-vault-format.js",
                     "lib/path-safety.js", "lib/missing-dependency.js"):
            (package / "scripts" / name).write_text("// upstream fixture\n")
        return shared_memory.runtime_at(package, "/usr/bin/node")

    def codex_config(self, runtime, *, enabled, profile=".codex", instructions=""):
        command = shared_memory.mcp_command(runtime, "codex")
        home = self.root / profile; home.mkdir(exist_ok=True)
        text = f"developer_instructions = {json.dumps(instructions)}\n" if instructions else ""
        text += (f"[mcp_servers.{shared_memory.SERVER}]\ncommand = {json.dumps(command[0])}\n"
                 f"args = {json.dumps(command[1:])}\nenabled = {str(enabled).lower()}\n")
        (home / "config.toml").write_text(text)

    def settings(self, **values):
        config = {"schemaVersion": 1, "agents": {key: False for key in harness.AGENTS}, **values}
        harness.save_config(config)
        return harness.load_config()

    def agent(self, reply, key):
        return next(row for row in reply["agents"] if row["id"] == key)

    def command(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = harness.main(list(args))
        return code, json.loads(output.getvalue())


class FakeNative:
    """Native state per profile and component; keeps the real harness logic under test."""

    def __init__(self):
        self.enabled, self.actions, self.fail, self.installed = {}, [], set(), set()

    def profile(self, cli):
        if cli == "claude":
            return Path(os.environ.get("CLAUDE_CONFIG_DIR", ".claude")).name.removeprefix(".")
        if cli != "codex":
            return cli
        return Path(os.environ["CODEX_HOME"]).name.removeprefix(".")

    def inspect(self, key, cli, receipt):
        row = (key, self.profile(cli))
        on = row in self.enabled
        current = bool(receipt and receipt.get("digest") == "ok" and on)
        return {"id": key, "installed": on or row in self.installed, "enabled": on, "version": "1", "verified": current,
                "detail": "sintético"}

    def apply(self, key, cli, action, receipt):
        profile = self.profile(cli)
        self.actions.append((key, profile, action))
        if (key, profile) in self.fail:
            raise harness.native_plugins.PluginError("fallo sintético")
        if action == "disable":
            self.enabled.pop((key, profile), None)
            return {"id": key, "digest": "off", "checkedAt": "t"}
        self.enabled[(key, profile)] = True
        return {"id": key, "digest": "ok", "checkedAt": "t"}

    def adapter(self, key):
        fake = self
        class Adapter:
            @staticmethod
            def inspect(cli, receipt=None, **options):
                return fake.inspect(key, cli, receipt)
            @staticmethod
            def apply(cli, binary, action, receipt=None, **options):
                return fake.apply(key, cli, action, receipt)
        return Adapter

    def install(self, case):
        managed = {key: (name, self.adapter(key)) for key, (name, _) in harness.MANAGED.items()}
        for patcher in (patch.object(harness, "MANAGED", managed),
                        patch.object(harness.native_pi, "inspect_component", side_effect=lambda key, receipt=None: self.inspect(key, "pi", receipt)),
                        patch.object(harness.native_pi, "apply_component", side_effect=lambda key, binary, action, receipt=None: self.apply(key, "pi", action, receipt)),
                        patch.object(harness, "refresh_team_models", return_value="")):
            patcher.start(); case.addCleanup(patcher.stop)


class PrepareAgentTests(ZeroTouchCase):
    def setUp(self):
        super().setUp()
        self.native = FakeNative(); self.native.install(self)
        for profile in (".codex-2", ".claude-2"): (self.root / profile).mkdir()

    def test_first_selection_prepares_all_components(self):
        for agent in harness.AGENTS:
            with self.subTest(agent=agent):
                harness.config_path().unlink(missing_ok=True)
                self.native.actions.clear(); self.native.enabled.clear()
                config = harness.load_config()
                harness.prepare_agent(config, agent)
                saved = harness.load_config()
                self.assertTrue(saved["agents"][agent])
                self.assertTrue(all(saved[key + "Enabled"] for key in ("superpowers", "memory", "local-tools")))
                self.assertTrue(saved["team"]["enabled"])
                if agent == "muse":
                    # Muse Code has no managed installer or components (native_muse): selecting it is only a preference.
                    self.assertEqual(self.native.actions, [])
                    continue
                row = self.agent(harness.status(saved), agent)
                # Every component connected; Pi alone still waits for the user to turn on Nous.
                self.assertEqual(row["missingComponents"], ["models"] if agent == "pi" else [])
                self.assertEqual(row["setupState"], "pending" if agent == "pi" else "ready")
                self.assertEqual({row[0] for row in self.native.actions}, set(harness.MANAGED))
                self.assertEqual({row[1] for row in self.native.actions}, {agent})

    def test_muse_is_never_installed_even_when_missing(self):
        (self.bin / "muse").unlink()
        config = harness.load_config()
        with patch.object(harness, "manage_agent_runtime") as install:
            harness.prepare_agent(config, "muse")
            install.assert_not_called()
            self.assertTrue(harness.load_config()["agents"]["muse"])
            (self.bin / "claude").unlink()
            with self.assertRaises(harness.HarnessError):  # the fake installer installs nothing
                harness.prepare_agent(harness.load_config(), "claude")
            install.assert_called_once_with("claude", "install")

    def test_existing_disabled_preferences_are_preserved(self):
        config = self.settings(memoryEnabled=False, superpowersEnabled=True)
        harness.prepare_agent(config, "claude")
        saved = harness.load_config()
        self.assertIs(saved["memoryEnabled"], False)
        self.assertNotIn("local-toolsEnabled", saved)
        self.assertEqual({row[0] for row in self.native.actions}, {"superpowers"})

    def test_a_second_profile_inherits_preferences_without_new_defaults(self):
        config = self.settings(superpowersEnabled=True, agents={"claude": True})
        harness.prepare_agent(config, "pi")
        self.assertNotIn("memoryEnabled", harness.load_config())

    def test_partial_failure_keeps_selection_pending_and_names_profile_and_component(self):
        self.native.fail.add(("memory", "codex"))
        config = harness.load_config()
        with self.assertRaises(harness.HarnessError) as caught:
            harness.prepare_agent(config, "codex")
        self.assertIn("Codex", str(caught.exception))
        self.assertIn("Memoria", str(caught.exception))
        saved = harness.load_config()
        self.assertTrue(saved["agents"]["codex"])
        row = self.agent(harness.status(saved), "codex")
        self.assertEqual((row["setupState"], row["missingComponents"]), ("error", ["memory"]))
        self.native.fail.clear()
        harness.prepare_agent(saved, "codex")
        self.assertEqual(self.agent(harness.status(harness.load_config()), "codex")["setupState"], "ready")

    def test_missing_codex_2_profile_is_not_created(self):
        (self.root / ".codex-2").rmdir()
        with self.assertRaises(harness.HarnessError):
            harness.prepare_agent(harness.load_config(), "codex-2")
        self.assertFalse((self.root / ".codex-2").exists())
        self.assertEqual(self.native.actions, [])
        self.assertFalse(harness.load_config()["agents"].get("codex-2", False))

    def test_other_connected_profiles_are_not_touched(self):
        self.native.enabled[("memory", "codex")] = True
        config = self.settings(memoryEnabled=True)
        harness.prepare_agent(config, "claude")
        self.assertFalse([row for row in self.native.actions if row[1] != "claude"])
        self.assertIn(("memory", "codex"), self.native.enabled)

    def test_refreshed_team_models_are_saved_even_if_nothing_else_changes(self):
        harness.prepare_agent(harness.load_config(), "codex")
        def refresh(config):
            config["teamModels"] = {"codex": [{"id": "sintético"}], "checkedAt": "t"}
            config["team"]["notices"] = ["Aviso sintético"]
            return ""
        with patch.object(harness, "refresh_team_models", side_effect=refresh):
            harness.prepare_agent(harness.load_config(), "codex")
        saved = harness.load_config()
        self.assertEqual(saved["teamModels"]["codex"], [{"id": "sintético"}])
        self.assertEqual(saved["team"]["notices"], ["Aviso sintético"])

    def test_make_default_and_nothing_about_authentication_or_payment_changes(self):
        harness.prepare_agent(harness.load_config(), "pi", make_default=True)
        saved = harness.load_config()
        self.assertEqual(saved["defaultAgent"], "pi")
        self.assertNotIn("piPaidApproved", saved)
        self.assertNotIn("piManagedModel", saved)
        self.assertNotIn("piProviders", saved)

    def test_default_is_untouched_unless_requested(self):
        config = self.settings(defaultAgent="claude", agents={"claude": True}, superpowersEnabled=True)
        harness.prepare_agent(config, "pi")
        self.assertEqual(harness.load_config()["defaultAgent"], "claude")

    def test_command_reports_failure_with_status_for_retry(self):
        self.native.fail.add(("team", "pi"))
        code, reply = self.command("prepare-agent", "pi", "--default")
        self.assertEqual(code, 1)
        self.assertIn("Pi", reply["error"])
        self.assertEqual(self.agent(reply["data"], "pi")["setupState"], "error")
        self.native.fail.clear()
        code, reply = self.command("prepare-agent", "pi", "--default")
        self.assertEqual(code, 0)
        self.assertEqual(self.agent(reply["data"], "pi")["missingComponents"], ["models"])  # The failed component is back.
        self.assertEqual(reply["data"]["launcher"]["defaultAgent"], "pi")


class UnmanagedProfilesTests(ZeroTouchCase):
    """A connection the plugin never made belongs to the user, even if the goal is on."""

    def setUp(self):
        super().setUp()
        self.native = FakeNative(); self.native.install(self)

    def connect_codex_by_hand(self):
        for key in harness.MANAGED:
            self.native.enabled[(key, "codex")] = True

    def assert_codex_untouched(self):
        self.assertEqual([row for row in self.native.actions if row[1] == "codex"], [])
        self.assertTrue(all((key, "codex") in self.native.enabled for key in harness.MANAGED))

    def test_selecting_another_profile_leaves_a_connected_unmarked_codex_alone(self):
        self.connect_codex_by_hand()
        self.settings(memoryEnabled=True, superpowersEnabled=True)
        code, _ = self.command("set-agents", "claude")
        self.assertEqual(code, 0)
        self.assertIn(("memory", "claude", "enable"), self.native.actions)
        self.assert_codex_untouched()

    def test_goal_commands_leave_a_connected_unmarked_codex_alone(self):
        self.connect_codex_by_hand()
        for command in ("memory-all", "superpowers-all"):
            for action in ("enable", "disable"):
                with self.subTest(command=command, action=action):
                    self.settings(agents={"claude": True}, memoryEnabled=True, superpowersEnabled=True)
                    code, _ = self.command(command, action)
                    self.assertEqual(code, 0)
                    self.assert_codex_untouched()

    def test_unmarking_a_prepared_profile_disconnects_it(self):
        harness.prepare_agent(harness.load_config(), "codex")
        self.native.actions.clear()
        code, _ = self.command("set-agents", "claude")
        self.assertEqual(code, 0)
        disconnected = {key for key, profile, action in self.native.actions
                        if profile == "codex" and action == "disable"}
        self.assertEqual(disconnected, set(harness.MANAGED))

    def test_unmarking_a_marked_profile_without_receipt_disconnects_it(self):
        self.native.enabled[("memory", "codex")] = True
        self.settings(agents={"codex": True}, memoryEnabled=True)
        code, _ = self.command("set-agents", "claude")
        self.assertEqual(code, 0)
        self.assertIn(("memory", "codex", "disable"), self.native.actions)

    def test_turning_a_goal_off_still_disconnects_marked_profiles(self):
        self.native.enabled[("memory", "claude")] = True
        self.settings(agents={"claude": True}, memoryEnabled=True)
        code, _ = self.command("memory-all", "disable")
        self.assertEqual(code, 0)
        self.assertEqual(self.native.actions, [("memory", "claude", "disable")])

    def test_every_selection_and_goal_command_leaves_an_unmarked_codex_without_receipt_alone(self):
        self.connect_codex_by_hand()
        for command in (("set-agent", "claude", "true"), ("set-agents", "claude"), ("memory-all", "enable"),
                        ("memory-all", "disable"), ("superpowers-all", "enable"), ("superpowers-all", "disable")):
            with self.subTest(command=command):
                self.settings(memoryEnabled=True, superpowersEnabled=True)
                self.assertEqual(self.command(*command)[0], 0)
                self.assert_codex_untouched()
                self.assertNotIn("codex", harness.load_config().get("memory", {}))

    def released_claude_reconnected_by_hand(self):
        """Mark, unmark (the plugin disconnects and keeps a receipt), then the user reconnects."""
        harness.config_path().unlink(missing_ok=True)
        self.command("set-agents", "claude")
        self.command("set-agents", "none")
        self.assertNotIn(("superpowers", "claude"), self.native.enabled)
        for key in harness.MANAGED:
            self.native.enabled[(key, "claude")] = True
        self.native.actions.clear()

    def active_count(self, key):
        components = harness.status(harness.load_config())["components"]
        return next(row for row in components if row["id"] == key)["activeCount"]

    def test_a_connection_remade_by_hand_after_unmarking_is_left_alone(self):
        for command in (("set-agents", "codex"), ("set-agent", "codex", "true"), ("memory-all", "disable"),
                        ("superpowers-all", "enable"), ("superpowers-all", "disable")):
            with self.subTest(command=command):
                self.released_claude_reconnected_by_hand()
                self.command(*command)
                self.assertEqual([row for row in self.native.actions if row[1] == "claude"], [])
                self.assertTrue(all((key, "claude") in self.native.enabled for key in harness.MANAGED))

    def test_a_connection_remade_by_hand_does_not_count_as_active(self):
        self.released_claude_reconnected_by_hand()
        self.assertEqual(self.active_count("superpowers"), 0)

    def test_disconnecting_keeps_the_receipt(self):
        self.released_claude_reconnected_by_hand()
        receipt = harness.load_config()["superpowers"]["claude"]
        self.assertEqual(receipt["digest"], "off")
        self.assertIs(receipt["released"], True)

    def test_unmarking_a_marked_profile_after_a_new_enable_disconnects_it_again(self):
        self.released_claude_reconnected_by_hand()
        self.command("set-agents", "claude")
        self.assertNotIn("released", harness.load_config()["superpowers"]["claude"])
        self.command("set-agents", "none")
        self.assertIn(("superpowers", "claude", "disable"), self.native.actions)
        self.assertNotIn(("superpowers", "claude"), self.native.enabled)

    def test_turning_a_goal_off_after_a_new_enable_disconnects_it_again(self):
        self.released_claude_reconnected_by_hand()
        self.command("set-agents", "claude")
        self.command("superpowers-all", "disable")
        self.assertNotIn(("superpowers", "claude"), self.native.enabled)

    def test_a_failed_disable_is_retried_on_the_next_sync(self):
        self.command("set-agents", "claude")
        self.native.fail.add(("superpowers", "claude"))
        self.command("set-agents", "none")
        receipt = harness.load_config()["superpowers"]["claude"]
        self.assertEqual(receipt["lastError"], "fallo sintético")
        self.assertNotIn("released", receipt)
        self.assertEqual(self.active_count("superpowers"), 1)
        self.native.fail.clear()
        self.command("set-agents", "codex")
        self.assertNotIn(("superpowers", "claude"), self.native.enabled)
        self.assertIs(harness.load_config()["superpowers"]["claude"]["released"], True)

    def test_a_failed_disable_over_an_old_release_is_also_retried(self):
        # Re-marked while connected and verified, so no enable replaced the released receipt.
        self.native.enabled[("superpowers", "claude")] = True
        self.settings(agents={"claude": True}, superpowersEnabled=True,
                      superpowers={"claude": {"digest": "ok", "released": True}})
        self.native.fail.add(("superpowers", "claude"))
        self.command("set-agents", "none")
        receipt = harness.load_config()["superpowers"]["claude"]
        self.assertEqual(receipt["lastError"], "fallo sintético")
        self.assertNotIn("released", receipt)
        self.native.fail.clear()
        self.command("set-agents", "codex")
        self.assertNotIn(("superpowers", "claude"), self.native.enabled)

    def test_an_old_receipt_without_the_mark_is_still_managed(self):
        self.native.enabled[("superpowers", "claude")] = True
        self.settings(superpowersEnabled=True, superpowers={"claude": {"digest": "off"}})
        self.assertEqual(self.active_count("superpowers"), 1)
        self.command("set-agents", "codex")
        self.assertIn(("superpowers", "claude", "disable"), self.native.actions)


class ReconcileTests(ZeroTouchCase):
    def setUp(self):
        super().setUp()
        self.native = FakeNative(); self.native.install(self)
        (self.root / ".codex-2").mkdir()

    def marked(self, **values):
        return self.settings(**{"agents": {"codex": True}, **values})

    def test_reconcile_repairs_only_selected_enabled_components(self):
        for key in ("memory", "local-tools"):
            self.native.installed.add((key, "codex"))
        self.native.enabled[("memory", "codex-2")] = True
        config = self.marked(**{"memoryEnabled": True, "local-toolsEnabled": False})
        report = harness.reconcile_selected(config)
        self.assertEqual(report["repaired"], [{"agent": "codex", "component": "memory"}])
        self.assertEqual(report["errors"], [])
        self.assertEqual(self.native.actions, [("memory", "codex", "enable")])
        self.assertIn(("memory", "codex-2"), self.native.enabled)

    def test_second_reconcile_is_noop(self):
        self.native.installed.add(("memory", "codex"))
        config = self.marked(memoryEnabled=True)
        harness.reconcile_selected(config)
        self.native.actions.clear()
        before = snapshot(self.root)
        report = harness.reconcile_selected(harness.load_config())
        self.assertEqual(report, {"repaired": [], "pending": [], "errors": []})
        self.assertEqual(self.native.actions, [])
        self.assertEqual(snapshot(self.root), before)

    def test_reconcile_reports_conflict_and_retries(self):
        self.native.installed.add(("memory", "codex"))
        self.native.fail.add(("memory", "codex"))
        config = self.marked(memoryEnabled=True)
        report = harness.reconcile_selected(config)
        self.assertEqual(report["repaired"], [])
        self.assertEqual([row["component"] for row in report["errors"]], ["memory"])
        self.assertEqual(report["errors"][0]["agent"], "codex")
        self.assertIn("fallo sintético", report["errors"][0]["detail"])
        self.assertEqual(self.agent(harness.status(harness.load_config()), "codex")["setupState"], "error")
        before = snapshot(self.root)
        harness.reconcile_selected(harness.load_config())
        self.assertEqual(snapshot(self.root), before)  # The same failure rewrites nothing.
        self.native.fail.clear()
        report = harness.reconcile_selected(harness.load_config())
        self.assertEqual(report["repaired"], [{"agent": "codex", "component": "memory"}])
        self.assertEqual(self.agent(harness.status(harness.load_config()), "codex")["setupState"], "ready")

    def test_missing_dependency_is_reported_and_nothing_is_installed(self):
        config = self.marked(memoryEnabled=True, superpowersEnabled=True)
        with patch.object(harness, "manage_agent_runtime") as install:
            report = harness.reconcile_selected(config)
        install.assert_not_called()
        self.assertEqual(self.native.actions, [])
        self.assertEqual({row["component"] for row in report["pending"]}, {"memory", "superpowers"})

    def test_missing_cli_is_reported_without_installing_it(self):
        (self.bin / "codex").unlink()
        config = self.marked(memoryEnabled=True)
        with patch.object(harness, "manage_agent_runtime") as install:
            report = harness.reconcile_selected(config)
        install.assert_not_called()
        self.assertEqual(self.native.actions, [])
        self.assertIn({"agent": "codex", "component": "cli", "detail": ""},
                      [{**row, "detail": ""} for row in report["pending"]])

    def test_reconcile_never_refreshes_models_or_touches_backups(self):
        self.native.installed.add(("memory", "codex"))
        config = self.marked(memoryEnabled=True)
        with patch.object(harness.memory_backups, "run") as backup, \
             patch.object(harness.memory_backups, "apply_timer") as timer:
            harness.reconcile_selected(config)
        backup.assert_not_called(); timer.assert_not_called()
        harness.refresh_team_models.assert_not_called()

    def test_status_says_when_there_is_something_to_repair(self):
        self.native.installed.add(("memory", "codex"))
        config = self.marked(memoryEnabled=True)
        self.assertTrue(harness.status(config)["needsReconcile"])
        harness.reconcile_selected(config)
        self.assertFalse(harness.status(harness.load_config())["needsReconcile"])

    def test_repair_is_recorded_only_when_something_was_repaired(self):
        self.native.installed.add(("memory", "codex"))
        config = self.marked(memoryEnabled=True)
        code, reply = self.command("reconcile-selected")
        self.assertEqual(code, 0)
        self.assertEqual(reply["data"]["lastReconcile"]["repaired"], [{"agent": "codex", "component": "memory"}])
        recorded = harness.load_config()["lastReconcile"]
        self.command("reconcile-selected")
        self.assertEqual(harness.load_config()["lastReconcile"], recorded)

    def test_command_reports_errors_with_profile_and_component(self):
        self.native.installed.add(("memory", "codex"))
        self.native.fail.add(("memory", "codex"))
        self.marked(memoryEnabled=True)
        code, reply = self.command("reconcile-selected")
        self.assertEqual(code, 1)
        self.assertIn("Codex", reply["error"])
        self.assertIn("Memoria", reply["error"])
        self.assertEqual(self.agent(reply["data"], "codex")["setupState"], "error")


class CodexRepairTests(ZeroTouchCase):
    """The case that started this work, through the real memory adapter and real files."""

    def test_active_codex_without_instructions_is_repaired_and_unmarked_profile_is_left_alone(self):
        runtime = self.runtime()
        self.codex_config(runtime, enabled=True)
        self.codex_config(runtime, enabled=True, profile=".codex-2")
        marked = self.root / ".codex/config.toml"; other = self.root / ".codex-2/config.toml"
        untouched = other.read_bytes()
        config = self.settings(memoryEnabled=True, agents={"codex": True})
        requests = []

        def codex_request(binary, method, params):
            if method == "config/read":
                return {"layers": [{"name": {"type": "user", "file": str(marked)}, "version": "v1",
                                    "config": tomllib.loads(marked.read_text())}]}
            requests.append(params)
            value = next(edit["value"] for edit in params["edits"] if edit["keyPath"] == "developer_instructions")
            marked.write_text(f"developer_instructions = {json.dumps(value)}\n" + marked.read_text())
            return {}

        server = {"command": shared_memory.mcp_command(runtime, "codex")[0],
                  "args": shared_memory.mcp_command(runtime, "codex")[1:]}
        with patch.object(shared_memory, "inspect_runtime", return_value=runtime), \
             patch.object(shared_memory, "ensure_runtime", return_value=runtime), \
             patch.object(shared_memory, "probe"), \
             patch.object(harness.native_plugins, "codex_request", side_effect=codex_request), \
             patch.object(harness.native_plugins, "command", return_value=server):
            report = harness.reconcile_selected(config)
            state = harness.status(harness.load_config())
        self.assertEqual(report["repaired"], [{"agent": "codex", "component": "memory"}])
        self.assertEqual({edit["keyPath"] for edit in requests[0]["edits"]},
                         {"developer_instructions", f"mcp_servers.{shared_memory.SERVER}.default_tools_approval_mode"})
        self.assertEqual(requests[0]["expectedVersion"], "v1")
        self.assertEqual(self.agent(state, "codex")["setupState"], "ready")
        self.assertEqual(other.read_bytes(), untouched)


class RestoreBeforeAgentsTests(ZeroTouchCase):
    KEY = "a" * 64

    def backup(self):
        """A synthetic verified backup, as another machine would have written it."""
        source = self.root / "other-machine"
        document = source / "projects" / self.KEY / "project/decisions/mem_test.md"
        document.parent.mkdir(parents=True)
        document.write_bytes(b"synthetic memory\n")
        archive = self.root / "backup.zip"
        harness.memory_archive.export(source, archive, self.runtime())
        return archive

    def setUp(self):
        super().setUp()
        audit = patch.object(harness.memory_archive, "audit", return_value={"memoryCount": 1})
        audit.start(); self.addCleanup(audit.stop)

    def store(self):
        return shared_memory.data_root() / "memory" / "projects" / self.KEY

    def test_restore_preview_prepares_ecc_without_selecting_agents(self):
        archive = self.backup()
        runtime = self.runtime()
        with patch.object(shared_memory, "ensure_runtime", return_value=runtime) as ensure, \
             patch.object(shared_memory, "inspect_runtime", return_value={"installed": False, "path": "", "version": "", "node": "", "digest": ""}):
            code, reply = self.command("memory-preview", str(archive))
            self.assertEqual(code, 0, reply)
            ensure.assert_called_once_with()
            self.assertEqual(reply["operation"]["restorePreview"]["newCount"], 1)
            # Nobody confirmed: cancelling means nothing is published.
            self.assertFalse(self.store().exists())
            self.assertFalse(any(row["enabled"] for row in reply["data"]["agents"]))

    def test_confirmed_restore_connects_no_profile_and_schedules_no_backup(self):
        archive = self.backup()
        runtime = self.runtime()
        with patch.object(shared_memory, "ensure_runtime", return_value=runtime), \
             patch.object(shared_memory, "inspect_runtime", return_value=runtime), \
             patch.object(harness.memory_backups, "apply_timer") as timer:
            _, reply = self.command("memory-preview", str(archive))
            code, restored = self.command("memory-restore", str(archive), reply["operation"]["restorePreview"]["token"])
        self.assertEqual(code, 0, restored)
        timer.assert_not_called()
        self.assertEqual((self.store() / "project/decisions/mem_test.md").read_bytes(), b"synthetic memory\n")
        saved = harness.load_config()
        self.assertFalse(any(saved["agents"].values()))
        self.assertFalse(any(key + "Enabled" in saved for key in harness.MANAGED))
        for profile in (".claude", ".codex", ".pi"):
            self.assertFalse((self.root / profile).exists())

    def test_command_moves_a_project_to_a_new_folder_and_reports_what_stays(self):
        archive = self.backup()
        runtime = self.runtime()
        folder = self.root / "new folder"; folder.mkdir()
        moved = ["--project-map", f"{self.KEY}={folder}"]
        with patch.object(shared_memory, "ensure_runtime", return_value=runtime), \
             patch.object(shared_memory, "inspect_runtime", return_value=runtime):
            _, reply = self.command("memory-preview", str(archive), *moved)
            preview = reply["operation"]["restorePreview"]
            self.assertEqual(preview["projectMap"], {self.KEY: str(folder)})
            code, restored = self.command("memory-restore", str(archive), preview["token"], *moved)
        self.assertEqual(code, 0, restored)
        self.assertEqual(restored["operation"]["unverifiedProjects"], [])
        store = shared_memory.data_root() / "memory"
        self.assertTrue((harness.memory_mcp.project_store(folder, store) / "project/decisions/mem_test.md").is_file())

    def test_map_to_a_missing_folder_is_rejected_before_any_write(self):
        archive = self.backup()
        runtime = self.runtime()
        with patch.object(shared_memory, "ensure_runtime", return_value=runtime), \
             patch.object(shared_memory, "inspect_runtime", return_value=runtime):
            code, _ = self.command("memory-preview", str(archive), "--project-map", f"{self.KEY}={self.root}/missing")
        self.assertEqual(code, 1)
        self.assertFalse((shared_memory.data_root() / "memory").exists())

    def test_opening_the_panel_never_installs_ecc(self):
        with patch.object(shared_memory, "ensure_runtime") as ensure:
            self.command("status")
        ensure.assert_not_called()


class OnlyPiJourneyTests(ZeroTouchCase):
    """A machine with only Pi + Nous: real team and tools adapters, real generated files."""
    AGENT_BINARIES = ("pi",)

    def setUp(self):
        super().setUp()
        self.sentinel = self.root / "forbidden-cli-ran"
        for name in ("claude", "codex", "npm", "mise"):
            binary = self.bin / name
            binary.write_text(f"#!/bin/sh\ntouch {self.sentinel}\nexit 99\n"); binary.chmod(0o755)
        self.native = FakeNative(); self.native.install(self)
        real = {**harness.MANAGED,
                "team": (harness.MANAGED["team"][0], harness.native_team),
                "local-tools": (harness.MANAGED["local-tools"][0], harness.native_local_tools)}
        for patcher in (patch.object(harness, "MANAGED", real),
                        patch("native_skills.loaded", return_value=True),
                        patch.object(harness, "agent_tool", side_effect=self.tool)):
            patcher.start(); self.addCleanup(patcher.stop)

    def tool(self, agent, inventory):
        # Fixture stands in for detection, so the guard binaries above never count as installed CLIs.
        if agent != "pi":
            return {"installation": "missing", "binary": "", "version": ""}
        return {"installation": "installed", "binary": str(self.bin / "pi"), "version": "1.4.0"}

    def nous_settings(self, *models):
        rows = [{"id": model, "name": model, "context_length": 262144, "pricing": {"prompt": "0", "completion": "0"},
                 "top_provider": {}, "supported_parameters": ["tools", "reasoning", "reasoning_effort"]} for model in models]
        return self.settings(piNousCatalog=harness.native_pi.free_nous_catalog({"data": rows}))

    def test_only_pi_gets_working_recipes_for_programar_and_revisar(self):
        config = self.nous_settings("a:free", "b:free")
        harness.prepare_agent(config, "pi", make_default=True)
        state = harness.status(harness.load_config())
        self.assertEqual(self.agent(state, "pi")["setupState"], "ready")
        skill = (harness.native_team.layout("pi")["source"] / "SKILL.md").read_text()
        code = skill[skill.index("Programar:\n```sh"):]
        self.assertIn("pi -p --provider nous --model a:free", code[:code.index("```", 12)])
        review = skill[skill.index("Revisar:\n```sh"):]
        self.assertIn("pi -p --provider nous --model b:free", review[:review.index("```", 12)])
        self.assertIn("no tienen sandbox ni aprobación por herramienta", skill)
        self.assertIn("no tienen sandbox ni aprobación por herramienta", " ".join(state["team"]["warnings"]))
        startup = (harness.native_pi.home() / "AGENTS.md").read_text()
        self.assertIn("--model b:free", startup)

    def test_only_pi_never_touches_or_runs_another_cli(self):
        config = self.nous_settings("a:free", "b:free")
        harness.prepare_agent(config, "pi")
        harness.reconcile_selected(harness.load_config())
        self.assertFalse(self.sentinel.exists())
        for profile in (".claude", ".codex", ".codex-2"):
            self.assertFalse((self.root / profile).exists())

    def test_prepared_does_not_mean_authenticated_or_working(self):
        config = self.nous_settings("a:free", "b:free")
        harness.prepare_agent(config, "pi")
        state = harness.status(harness.load_config())
        self.assertEqual(self.agent(state, "pi")["setupState"], "ready")   # Native load only.
        self.assertFalse(state["pi"]["checks"].get("nous", {}).get("ready"))  # Authentication was not checked.
        self.assertEqual(state["aiCheck"], {})                              # No model ever ran.

    def test_preparing_again_changes_nothing(self):
        config = self.nous_settings("a:free", "b:free")
        harness.prepare_agent(config, "pi")
        before = snapshot(self.root)
        harness.prepare_agent(harness.load_config(), "pi")
        self.assertEqual(snapshot(self.root), before)

    def test_without_models_the_roles_stay_pending_and_say_so(self):
        config = self.settings()
        harness.prepare_agent(config, "pi")
        state = harness.status(harness.load_config())
        self.assertEqual(state["team"]["roles"], {})
        self.assertIn("No hay modelos gratuitos", " ".join(state["team"]["warnings"]))
        skill = (harness.native_team.layout("pi")["source"] / "SKILL.md").read_text()
        self.assertIn("Pendiente; no hay modelo disponible.", skill)

    def test_without_models_the_profile_is_not_reported_as_ready(self):
        harness.prepare_agent(self.settings(), "pi")
        state = harness.status(harness.load_config())
        row = self.agent(state, "pi")
        self.assertEqual((row["setupState"], row["missingComponents"]), ("pending", ["models"]))
        self.assertIn("Nous", row["setupIssues"][0]["detail"])
        self.assertFalse(state["needsReconcile"])  # Turning a provider on is the user's decision, never a repair.
        report = harness.reconcile_selected(harness.load_config())
        self.assertEqual(report["repaired"], [])
        self.assertEqual([item["component"] for item in report["pending"]], ["models"])


class NewMachineJourneyTests(RestoreBeforeAgentsTests):
    """Reinstall from zero: restore the backup, install a CLI, mark it, and it is ready."""

    def setUp(self):
        super().setUp()
        self.native = FakeNative(); self.native.install(self)
        self.runtime_fixture = self.runtime()
        for patcher in (patch.object(shared_memory, "ensure_runtime", return_value=self.runtime_fixture),
                        patch.object(shared_memory, "inspect_runtime", return_value=self.runtime_fixture)):
            patcher.start(); self.addCleanup(patcher.stop)

    def restored(self):
        archive = self.backup()
        _, reply = self.command("memory-preview", str(archive))
        code, _ = self.command("memory-restore", str(archive), reply["operation"]["restorePreview"]["token"])
        self.assertEqual(code, 0)

    def test_backup_restore_then_selection(self):
        self.restored()
        harness.prepare_agent(harness.load_config(), "claude", make_default=True)
        state = harness.status(harness.load_config())
        self.assertEqual(self.agent(state, "claude")["setupState"], "ready")
        self.assertEqual([row["memoryCount"] for row in state["memory"]["projects"]], [1])
        self.assertEqual(state["launcher"]["defaultAgent"], "claude")

    def test_partial_failure_retry_and_second_opening(self):
        self.restored()
        self.native.installed.add(("memory", "claude"))  # Restoring already prepared the ECC runtime.
        self.native.fail.add(("memory", "claude"))
        with self.assertRaises(harness.HarnessError):
            harness.prepare_agent(harness.load_config(), "claude")
        self.native.fail.clear()
        report = harness.reconcile_selected(harness.load_config())
        self.assertEqual(report["repaired"], [{"agent": "claude", "component": "memory"}])
        before = snapshot(self.root)
        report = harness.reconcile_selected(harness.load_config())
        self.assertEqual(report, {"repaired": [], "pending": [], "errors": []})
        self.assertEqual(snapshot(self.root), before)

    def test_a_failed_backup_after_restoring_keeps_the_last_good_copy(self):
        self.restored()
        folder = self.root / "copies"; folder.mkdir()
        config = harness.load_config()
        harness.memory_backups.configure(config, str(folder), 24, 1, False)
        harness.memory_backups.run(config, self.runtime_fixture)
        good = {path: path.read_bytes() for path in folder.glob("*.zip")}
        self.assertEqual(len(good), 1)
        with patch.object(harness.memory_archive, "audit", return_value={"memoryCount": 99}):
            with self.assertRaises(harness.memory_archive.ArchiveError):
                harness.memory_backups.run(config, self.runtime_fixture)
        self.assertEqual({path: path.read_bytes() for path in folder.glob("*.zip")}, good)
        self.assertTrue(harness.memory_backups.state()["lastError"])


class SetupStateTests(ZeroTouchCase):
    def test_deselected_codex_without_instructions_is_disabled(self):
        runtime = self.runtime()
        self.codex_config(runtime, enabled=False)
        config = self.settings(memoryEnabled=True)
        with patch.object(shared_memory, "inspect_runtime", return_value=runtime):
            before = snapshot(self.root)
            codex = self.agent(harness.status(config), "codex")
            self.assertEqual(snapshot(self.root), before)
        self.assertEqual(codex["setupState"], "disabled")
        self.assertEqual(codex["missingComponents"], [])

    def test_selected_codex_with_enabled_mcp_without_instructions_is_pending(self):
        runtime = self.runtime()
        self.codex_config(runtime, enabled=True)
        config = self.settings(memoryEnabled=True, agents={"codex": True})
        with patch.object(shared_memory, "inspect_runtime", return_value=runtime):
            before = snapshot(self.root)
            codex = self.agent(harness.status(config), "codex")
            self.assertEqual(snapshot(self.root), before)
        self.assertEqual(codex["setupState"], "pending")
        self.assertEqual(codex["missingComponents"], ["memory"])


class FirstSelectionTests(ZeroTouchCase):
    def setUp(self):
        super().setUp()
        self.native = FakeNative(); self.native.install(self)

    def test_marking_in_the_main_selector_on_a_new_install_prepares_everything(self):
        code, reply = self.command("set-agents", "claude")
        self.assertEqual(code, 0, reply)
        saved = harness.load_config()
        self.assertTrue(all(saved[key + "Enabled"] for key in ("superpowers", "memory", "local-tools")))
        self.assertTrue(saved["team"]["enabled"])
        self.assertEqual(self.agent(reply["data"], "claude")["setupState"], "ready")

    def test_selecting_with_existing_preferences_adds_no_defaults(self):
        self.settings(superpowersEnabled=False)
        code, _ = self.command("set-agents", "claude")
        self.assertEqual(code, 0)
        saved = harness.load_config()
        self.assertNotIn("memoryEnabled", saved)
        self.assertIs(saved["superpowersEnabled"], False)
        self.assertEqual(self.native.actions, [])

    def test_clearing_the_selection_on_a_new_install_enables_nothing(self):
        self.command("set-agents", "none")
        self.assertNotIn("memoryEnabled", harness.load_config())


class ReceiptTests(ZeroTouchCase):
    def test_identical_verification_without_a_date_keeps_the_settings_file(self):
        receipt = {"id": "x", "digest": "same"}
        config = self.settings(superpowers={"pi": {**receipt, "agentBinary": "/p", "agentVersion": "1"}})
        settings = harness.config_path()
        before = (settings.read_bytes(), settings.stat().st_mtime_ns)
        with patch.object(harness.native_pi, "apply_component", return_value=dict(receipt)):
            harness.apply_component(config, "superpowers", "pi", "verify", {"binary": "/p", "version": "1"})
        self.assertEqual((settings.read_bytes(), settings.stat().st_mtime_ns), before)

    def test_identical_verification_keeps_receipt_and_settings_file(self):
        receipt = {"id": "x", "digest": "same", "checkedAt": "first"}
        config = self.settings(superpowers={"claude": {**receipt, "agentBinary": "/c", "agentVersion": "1"}})
        tool = {"binary": "/c", "version": "1"}
        settings = harness.config_path()
        before = (settings.read_bytes(), settings.stat().st_mtime_ns)
        with patch.object(harness.native_plugins, "apply", return_value={**receipt, "checkedAt": "second"}):
            harness.apply_component(config, "superpowers", "claude", "verify", tool)
        self.assertEqual((settings.read_bytes(), settings.stat().st_mtime_ns), before)
        self.assertEqual(config["superpowers"]["claude"]["checkedAt"], "first")

    def test_a_real_change_or_past_error_updates_the_receipt(self):
        tool = {"binary": "/c", "version": "1"}
        config = self.settings(superpowers={"claude": {"digest": "old", "checkedAt": "first", "lastError": "x",
                                                       "agentBinary": "/c", "agentVersion": "1"}})
        with patch.object(harness.native_plugins, "apply", return_value={"digest": "old", "checkedAt": "second"}):
            harness.apply_component(config, "superpowers", "claude", "verify", tool)
        self.assertNotIn("lastError", harness.load_config()["superpowers"]["claude"])
        with patch.object(harness.native_plugins, "apply", return_value={"digest": "new", "checkedAt": "third"}):
            harness.apply_component(config, "superpowers", "claude", "verify", tool)
        self.assertEqual(harness.load_config()["superpowers"]["claude"]["checkedAt"], "third")

    def test_repeating_a_disconnection_keeps_receipt_and_settings_file(self):
        receipt = {"digest": "off", "checkedAt": "first"}
        config = self.settings(superpowers={"claude": {**receipt, "released": True, "agentBinary": "/c", "agentVersion": "1"}})
        settings = harness.config_path()
        before = (settings.read_bytes(), settings.stat().st_mtime_ns)
        with patch.object(harness.native_plugins, "apply", return_value={**receipt, "checkedAt": "second"}):
            harness.apply_component(config, "superpowers", "claude", "disable", {"binary": "/c", "version": "1"})
        self.assertEqual((settings.read_bytes(), settings.stat().st_mtime_ns), before)

    def test_a_new_enable_over_identical_data_drops_the_release(self):
        receipt = {"digest": "same", "checkedAt": "first"}
        config = self.settings(superpowers={"claude": {**receipt, "released": True, "agentBinary": "/c", "agentVersion": "1"}})
        with patch.object(harness.native_plugins, "apply", return_value=dict(receipt)):
            harness.apply_component(config, "superpowers", "claude", "enable", {"binary": "/c", "version": "1"})
        self.assertNotIn("released", harness.load_config()["superpowers"]["claude"])


if __name__ == "__main__":
    unittest.main()
