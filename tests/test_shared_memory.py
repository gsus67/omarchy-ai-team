import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import memory_mcp
import shared_memory as memory


class SharedMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"HOME": str(self.root), "XDG_DATA_HOME": str(self.root / "data")})
        environment.start()
        self.addCleanup(environment.stop)
        for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
            # patch.dict restores the original environment on cleanup.
            os.environ.pop(key, None)

    def package(self):
        package = self.root / "package with spaces"
        (package / "scripts/lib").mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({"name": "ecc-universal", "version": "2.2.3", "license": "MIT",
            "repository": {"url": "git+https://github.com/affaan-m/ECC.git"}, "bin": {"ecc-memory-mcp": "scripts/memory-mcp.mjs"}}))
        for name in ("memory-mcp.mjs", "lib/memory-vault.js", "lib/memory-vault-format.js", "lib/path-safety.js", "lib/missing-dependency.js"):
            (package / "scripts" / name).write_text("// upstream fixture\n")
        return package

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def test_worktrees_and_subfolders_share_but_same_name_repositories_do_not(self):
        a, b = self.root / "one/app", self.root / "two/app"
        for path in (a, b):
            path.mkdir(parents=True)
            self.git("init", "-q", str(path))
        (a / "subfolder").mkdir()
        self.git("-c", "user.name=Synthetic", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "fixture", cwd=a)
        worktree = self.root / "worktree"
        self.git("worktree", "add", "-q", "-b", "synthetic", str(worktree), cwd=a)
        base = self.root / "central"
        first = memory_mcp.project_store(a, base)
        self.assertEqual(first, memory_mcp.project_store(a / "subfolder", base))
        self.assertEqual(first, memory_mcp.project_store(worktree, base))
        self.assertNotEqual(first, memory_mcp.project_store(b, base))
        self.assertFalse(base.exists())

    def test_foreign_git_environment_cannot_redirect_the_session_memory(self):
        a, b = self.root / "a", self.root / "b"
        for path in (a, b):
            path.mkdir()
            self.git("init", "-q", str(path))
        base = self.root / "central"
        expected = memory_mcp.project_store(b, base)
        for overrides in ({"GIT_DIR": str(a / ".git")}, {"GIT_COMMON_DIR": str(a / ".git")},
                          {"GIT_DIR": str(a / ".git"), "GIT_WORK_TREE": str(a)}):
            with self.subTest(overrides=list(overrides)), patch.dict(os.environ, overrides):
                self.assertEqual(expected, memory_mcp.project_store(b, base))
        plain = self.root / "plain"; plain.mkdir()
        plain_scope = memory_mcp.project_store(plain, base)
        with patch.dict(os.environ, {"GIT_DIR": str(a / ".git")}):
            self.assertEqual(plain_scope, memory_mcp.project_store(plain, base))

    def test_non_git_and_symlink_paths_have_stable_separate_scopes(self):
        a = self.root / "folder with spaces"; a.mkdir()
        link = self.root / "alias"; link.symlink_to(a, target_is_directory=True)
        base = self.root / "central"
        self.assertEqual(memory_mcp.project_store(a, base), memory_mcp.project_store(link, base))
        self.assertNotEqual(memory_mcp.project_store(a, base), memory_mcp.project_store(self.root, base))

    def test_runtime_status_is_read_only_and_rejects_a_foreign_package(self):
        package = self.package()
        def which(name):
            return str(package / "scripts/memory-mcp.mjs") if name == "ecc-memory-mcp" else "/usr/bin/node"
        with patch.object(memory.shutil, "which", side_effect=which), patch.object(memory.subprocess, "run") as run:
            state = memory.inspect_runtime()
            self.assertTrue(state["installed"])
            self.assertEqual(state["version"], "2.2.3")
            run.assert_not_called()
            file = package / "package.json"
            value = json.loads(file.read_text()); value["repository"]["url"] = "https://github.com/someone/fork"
            file.write_text(json.dumps(value))
            with self.assertRaises(memory.MemoryError):
                memory.inspect_runtime()
        self.assertFalse((self.root / "data").exists())

    def test_relative_data_directory_is_rejected_without_writes(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "relative"}):
            with self.assertRaises(memory.MemoryError):
                memory.data_root()

    def test_codex_native_write_preserves_other_instructions_and_server_settings(self):
        runtime = memory.runtime_at(self.package(), "/usr/bin/node")
        home = self.root / ".codex"
        config = {"developer_instructions": "Keep my instructions.\n", "mcp_servers": {
            memory.SERVER: {"command": memory.mcp_command(runtime, "codex")[0],
                            "args": memory.mcp_command(runtime, "codex")[1:], "enabled": False,
                            "startup_timeout_sec": 25}, "foreign": {"command": "untouched"}}}
        layer = {"name": {"type": "user", "file": str(home / "config.toml")}, "version": "fixture", "config": config}
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}), \
             patch.object(memory.native_plugins, "codex_request", side_effect=[{"layers": [layer]}, {}]) as request:
            memory.set_codex_connection("/installed/codex", runtime, True, {})
        written = request.call_args_list[-1].args[2]
        self.assertEqual(written["expectedVersion"], "fixture")
        self.assertFalse(written["reloadUserConfig"])
        edits = {edit["keyPath"]: edit["value"] for edit in written["edits"]}
        self.assertEqual(memory.instructions(edits["developer_instructions"]), "Keep my instructions.\n")
        self.assertEqual(edits[f"mcp_servers.{memory.SERVER}.enabled"], True)
        self.assertNotIn("mcp_servers", edits)  # Do not replace other servers or their settings.
        self.assertNotIn(f"mcp_servers.{memory.SERVER}.startup_timeout_sec", edits)

    def codex_write(self, runtime, profile, config, enabled, *, reply=None):
        """Run the connection writer against a synthetic layer; return the batch write, if any."""
        home = self.root / profile
        layer = {"name": {"type": "user", "file": str(home / "config.toml")}, "version": "v1", "config": config}
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}), \
             patch.object(memory.native_plugins, "codex_request", side_effect=[{"layers": [layer]}, reply or {}]) as request:
            memory.set_codex_connection("/installed/codex", runtime, enabled, {})
        return request.call_args_list[1].args[2] if request.call_count > 1 else None

    def matching_server(self, runtime, **extra):
        command = memory.mcp_command(runtime, "codex")
        return {"command": command[0], "args": command[1:], "enabled": True,
                "default_tools_approval_mode": "approve", **extra}

    def test_matching_codex_connection_does_not_write(self):
        runtime = memory.runtime_at(self.package(), "/usr/bin/node")
        for profile in (".codex", ".codex-2"):
            with self.subTest(profile=profile):
                config = {"developer_instructions": memory.instructions("Keep.\n", True),
                          "mcp_servers": {memory.SERVER: self.matching_server(runtime)}}
                self.assertIsNone(self.codex_write(runtime, profile, config, True))

    def test_absent_codex_config_writes_server_and_block_together(self):
        runtime = memory.runtime_at(self.package(), "/usr/bin/node")
        for profile in (".codex", ".codex-2"):
            with self.subTest(profile=profile):
                written = self.codex_write(runtime, profile, {}, True)
                edits = {edit["keyPath"]: edit["value"] for edit in written["edits"]}
                self.assertEqual(written["expectedVersion"], "v1")
                self.assertEqual(edits[f"mcp_servers.{memory.SERVER}.enabled"], True)
                self.assertIn(f"mcp_servers.{memory.SERVER}.command", edits)
                self.assertEqual(memory.instructions(edits["developer_instructions"]), "")

    def test_active_mcp_without_instructions_only_adds_the_block(self):
        runtime = memory.runtime_at(self.package(), "/usr/bin/node")
        for profile in (".codex", ".codex-2"):
            with self.subTest(profile=profile):
                config = {"developer_instructions": "Keep.\n", "mcp_servers": {memory.SERVER: self.matching_server(runtime)}}
                written = self.codex_write(runtime, profile, config, True)
                self.assertEqual([edit["keyPath"] for edit in written["edits"]], ["developer_instructions"])
                self.assertTrue(written["edits"][0]["value"].startswith("Keep.\n"))

    def test_repair_keeps_native_policy_filters_and_cwd(self):
        runtime = memory.runtime_at(self.package(), "/usr/bin/node")
        server = self.matching_server(runtime, default_tools_approval_mode="prompt",
                                      enabled_tools=["memory_search", "memory_read"], cwd="/fixed")
        written = self.codex_write(runtime, ".codex", {"mcp_servers": {memory.SERVER: server}}, True)
        self.assertEqual([edit["keyPath"] for edit in written["edits"]], ["developer_instructions"])

    def test_disable_keeps_disabled_declaration_and_second_disable_does_not_write(self):
        runtime = memory.runtime_at(self.package(), "/usr/bin/node")
        for profile in (".codex", ".codex-2"):
            with self.subTest(profile=profile):
                config = {"developer_instructions": memory.instructions("Keep.\n", True),
                          "mcp_servers": {memory.SERVER: self.matching_server(runtime)}}
                written = self.codex_write(runtime, profile, config, False)
                edits = {edit["keyPath"]: edit["value"] for edit in written["edits"]}
                self.assertEqual(edits, {f"mcp_servers.{memory.SERVER}.enabled": False, "developer_instructions": "Keep.\n"})
                config = {"developer_instructions": "Keep.\n",
                          "mcp_servers": {memory.SERVER: self.matching_server(runtime, enabled=False)}}
                self.assertIsNone(self.codex_write(runtime, profile, config, False))

    def test_codex_version_conflict_surfaces_without_retrying_the_write(self):
        runtime = memory.runtime_at(self.package(), "/usr/bin/node")
        home = self.root / ".codex"
        layer = {"name": {"type": "user", "file": str(home / "config.toml")}, "version": "stale", "config": {}}
        conflict = memory.native_plugins.PluginError("version conflict")
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}), \
             patch.object(memory.native_plugins, "codex_request", side_effect=[{"layers": [layer]}, conflict]) as request:
            with self.assertRaises(memory.native_plugins.PluginError):
                memory.set_codex_connection("/installed/codex", runtime, True, {})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1].args[2]["expectedVersion"], "stale")

    def test_conflicting_server_is_preserved_and_never_started(self):
        home = self.root / "claude"; home.mkdir()
        file = home / ".claude.json"
        file.write_text(json.dumps({"mcpServers": {memory.SERVER: {"command": "foreign", "args": []}}}))
        before = file.read_bytes()
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(home)}), \
             patch.object(memory, "ensure_runtime") as ensure, patch.object(memory.native_plugins, "command") as command:
            with self.assertRaises(memory.MemoryError):
                memory.apply("claude", "/installed/claude", "enable")
            ensure.assert_not_called(); command.assert_not_called()
        self.assertEqual(file.read_bytes(), before)

    def test_disable_never_removes_runtime_or_data_and_preserves_claude_instructions(self):
        runtime = memory.runtime_at(self.package(), "/usr/bin/node")
        home = self.root / "claude"; home.mkdir()
        cmd = memory.mcp_command(runtime, "claude")
        file = home / ".claude.json"
        file.write_text(json.dumps({"mcpServers": {memory.SERVER: {"type": "stdio", "command": cmd[0], "args": cmd[1:]}}}))
        instructions = home / "CLAUDE.md"; instructions.write_text(memory.instructions("Keep this.\n", True))
        data = memory.data_root() / "memory/synthetic.md"; data.parent.mkdir(parents=True); data.write_text("keep memory")
        def remove(*args, **kwargs):
            file.write_text(json.dumps({"mcpServers": {}}))
            return "Removed"
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(home)}), \
             patch.object(memory, "inspect_runtime", return_value=runtime), \
             patch.object(memory.native_plugins, "command", side_effect=remove):
            memory.apply("claude", "/installed/claude", "disable")
        self.assertEqual(instructions.read_text(), "Keep this.\n")
        self.assertEqual(data.read_text(), "keep memory")
        self.assertTrue((Path(runtime["path"]) / "package.json").exists())

    def test_missing_state_does_not_start_or_install_anything(self):
        with patch.object(memory.subprocess, "run") as run, patch.object(memory.shutil, "which", return_value=None):
            self.assertFalse(memory.inspect("claude")["enabled"])
            self.assertFalse(memory.inspect("codex")["verified"])
            run.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_empty_foreign_declaration_is_not_claimed_without_a_receipt(self):
        with self.assertRaises(memory.MemoryError):
            memory.check_owned({}, memory.runtime_at(self.package(), "/usr/bin/node"), "codex", {})

    def test_other_memory_connection_blocks_installation_of_a_second_engine(self):
        home = self.root / "claude"; home.mkdir()
        (home / ".claude.json").write_text(json.dumps({"mcpServers": {"agentmemory": {"command": "npx", "args": ["@agentmemory/mcp"]}}}))
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(home)}), \
             patch.object(memory, "ensure_runtime") as install:
            with self.assertRaises(memory.MemoryError):
                memory.apply("claude", "/installed/claude", "enable")
            install.assert_not_called()

    def test_disabled_codex_can_disconnect_a_damaged_runtime_without_starting_it(self):
        home = self.root / "codex"; home.mkdir()
        runtime = memory.runtime_at(self.package(), "/usr/bin/node")
        server = memory.expected_server(runtime, "codex")
        file = home / "config.toml"
        file.write_text('developer_instructions = ' + json.dumps(memory.instructions("Keep this.", True)) + '\n'
                        '[mcp_servers.ai-team-memory]\ncommand = ' + json.dumps(server["command"]) + '\nargs = ' + json.dumps(server["args"]) + '\n')
        layer = {"name": {"type": "user", "file": str(file)}, "version": "fixture", "config": memory.tomllib.loads(file.read_text())}
        def native(binary, method, params):
            if method == "config/read":return {"layers": [layer]}
            file.write_text('developer_instructions = "Keep this."\n[mcp_servers.ai-team-memory]\ncommand = ' + json.dumps(server["command"]) + '\nargs = ' + json.dumps(server["args"]) + '\nenabled = false\n')
            return {}
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}), \
             patch.object(memory, "inspect_runtime", side_effect=memory.MemoryError("damaged runtime")), \
             patch.object(memory.native_plugins, "codex_request", side_effect=native), \
             patch.object(memory, "probe") as probe:
            memory.apply("codex", "/installed/codex", "disable", {"server": server, "node": runtime["node"], "version": "2.2.3"})
            probe.assert_not_called()
        self.assertFalse(memory.tomllib.loads(file.read_text())["mcp_servers"][memory.SERVER]["enabled"])

    def test_native_host_environment_filter_cannot_change_the_shared_store(self):
        package = self.package()
        # Stand in only for Node; run our launcher as a real process.
        node = self.root / "fake-node"
        node.write_text('#!' + sys.executable + '\nimport json, os\nprint(json.dumps(os.environ["AI_TEAM_SESSION_STORE"]))\n')
        node.chmod(0o700)
        runtime = memory.runtime_at(package, str(node))
        command = memory.mcp_command(runtime, "codex")
        filtered = {key: value for key, value in os.environ.items() if key != "XDG_DATA_HOME"}
        # Not the home folder: a session there has no project of its own.
        session = self.root / "proyecto"; session.mkdir()
        result = subprocess.run(command, cwd=session, env=filtered, capture_output=True, text=True, check=True)
        selected = Path(json.loads(result.stdout))
        self.assertTrue(selected.is_relative_to(self.root / "data/omarchy-ai-team/memory"), selected)
        self.assertFalse((self.root / "data/omarchy-ai-team/memory").exists())
        self.assertTrue((self.root / "data/omarchy-ai-team/state/ecc-memory-maintenance.lock").is_file())

    def test_successful_native_process_without_startup_effect_is_not_verified(self):
        runtime = memory.runtime_at(self.package(), "/usr/bin/node")
        home = self.root / "codex"; home.mkdir()
        server = memory.expected_server(runtime, "codex")
        (home / "config.toml").write_text('[mcp_servers.ai-team-memory]\ncommand = ' + json.dumps(server["command"]) + '\nargs = ' + json.dumps(server["args"]) + '\nenabled = true\n')
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}), \
             patch.object(memory, "inspect_runtime", return_value=runtime), patch.object(memory, "ensure_runtime", return_value=runtime), \
             patch.object(memory, "probe"), patch.object(memory, "set_codex_connection"), \
             patch.object(memory.native_plugins, "command", return_value={"transport": server}):
            with self.assertRaises(memory.MemoryError):
                memory.apply("codex", "/installed/codex", "verify")

    def test_native_tool_filters_cannot_leave_unusable_memory_verified(self):
        runtime = memory.runtime_at(self.package(), "/usr/bin/node")
        home = self.root / "codex"; home.mkdir()
        server = memory.expected_server(runtime, "codex")
        file = home / "config.toml"
        original = 'developer_instructions = ' + json.dumps(memory.instructions("Keep this.", True)) + '\n'
        original += '[mcp_servers.ai-team-memory]\ncommand = ' + json.dumps(server["command"]) + '\nargs = ' + json.dumps(server["args"]) + '\nenabled = true\n'
        receipt = {"digest": memory.connection_digest(runtime), "node": runtime["node"], "home": str(home)}
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}), patch.object(memory, "inspect_runtime", return_value=runtime):
            file.write_text(original)
            self.assertTrue(memory.inspect("codex", receipt)["verified"])
            for policy in ('enabled_tools = ["memory_doctor"]\n', 'enabled_tools = []\n',
                           'disabled_tools = ["memory_read"]\n'):
                with self.subTest(policy=policy):
                    file.write_text(original + policy)
                    before = file.read_bytes()
                    state = memory.inspect("codex", receipt)
                    self.assertTrue(state["enabled"])
                    self.assertFalse(state["verified"])
                    self.assertIn("memory_", state["detail"])
                    self.assertEqual(file.read_bytes(), before)
            file.write_text(original + 'default_tools_approval_mode = "prompt"\n')
            self.assertFalse(memory.inspect("codex", receipt)["verified"])
            file.write_text(original + 'enabled_tools = ["memory_search", "memory_read"]\n')
            self.assertFalse(memory.inspect("codex", receipt)["verified"])
            refreshed = {**receipt, "digest": memory.connection_digest(runtime, {
                **server, "enabled_tools": ["memory_search", "memory_read"]})}
            state = memory.inspect("codex", refreshed)
            self.assertTrue(state["verified"])
            self.assertIn("solo lectura", state["detail"])

    def test_fixed_native_mcp_cwd_is_reported_without_overwriting_it(self):
        runtime = memory.runtime_at(self.package(), "/usr/bin/node")
        home = self.root / "codex"; home.mkdir()
        server = memory.expected_server(runtime, "codex")
        file = home / "config.toml"
        file.write_text('developer_instructions = ' + json.dumps(memory.instructions("Keep this.", True)) + '\n'
                        '[mcp_servers.ai-team-memory]\ncommand = ' + json.dumps(server["command"]) + '\nargs = ' + json.dumps(server["args"]) + '\ncwd = ' + json.dumps(str(self.root)) + '\n')
        before = file.read_bytes()
        receipt = {"digest": memory.connection_digest(runtime), "node": runtime["node"], "home": str(home)}
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}), patch.object(memory, "inspect_runtime", return_value=runtime):
            state = memory.inspect("codex", receipt)
        self.assertFalse(state["verified"])
        self.assertIn("cwd", state["detail"])
        self.assertEqual(file.read_bytes(), before)

    def test_claude_default_enabled_memory_plugin_prevents_a_second_engine(self):
        home = self.root / "claude"; (home / "plugins").mkdir(parents=True)
        (home / "plugins/installed_plugins.json").write_text(json.dumps({"plugins": {"agentmemory@official": [{"scope": "user"}]}}))
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(home)}), \
             patch.object(memory, "ensure_runtime", side_effect=memory.MemoryError("unexpected installation")) as install:
            with self.assertRaises(memory.MemoryError):
                memory.apply("claude", "/installed/claude", "enable")
            install.assert_not_called()


class DiagnoseTests(unittest.TestCase):
    RUNTIME = {"installed": True, "path": "/rt", "version": "2.2.3", "node": "/usr/bin/node", "digest": "d"}

    def run_diagnose(self, agent="codex", server="expected", exchange=None, runtime=None):
        runtime = runtime or self.RUNTIME
        declared = ({"command": memory.expected_server(runtime, agent)["command"],
                     "args": memory.expected_server(runtime, agent)["args"]} if server == "expected" else server)
        config = {"mcp_servers": {memory.SERVER: declared}} if declared is not None else {}
        exchange = exchange or (lambda *a, **k: (set(memory.TOOLS), True, 0.5))
        with patch.object(memory, "local_config", return_value=config), \
             patch.object(memory, "inspect_runtime", return_value=runtime), \
             patch.object(memory, "exchange", side_effect=exchange):
            return memory.diagnose(agent)

    def test_all_levels_ok(self):
        report = self.run_diagnose()
        self.assertTrue(all(report[key]["ok"] for key in ("configured", "connected", "usable")))

    def test_not_declared_skips_probe_and_names_cause(self):
        report = self.run_diagnose(server=None, exchange=lambda *a, **k: self.fail("must not start"))
        self.assertEqual(report["configured"]["causes"], ["not-declared"])
        self.assertFalse(report["connected"]["ok"])

    def test_disabled_and_foreign_command_never_start_the_server(self):
        for server, cause in (({"command": "x", "args": [], "enabled": False}, "disabled"),
                              ({"command": "x", "args": []}, "foreign-or-stale-command")):
            report = self.run_diagnose(server=server, exchange=lambda *a, **k: self.fail("must not start"))
            self.assertIn(cause, report["configured"]["causes"])
            self.assertEqual(report["usable"]["causes"], ["skipped-not-configured"])

    def test_configured_but_server_does_not_answer(self):
        def broken(*a, **k):
            raise ValueError("timeout")
        report = self.run_diagnose(exchange=broken)
        self.assertTrue(report["configured"]["ok"])
        self.assertEqual(report["connected"]["causes"], ["timeout"])
        self.assertFalse(report["usable"]["ok"])

    def test_slow_start_over_codex_timeout_is_connected_but_not_ok(self):
        report = self.run_diagnose(exchange=lambda *a, **k: (set(memory.TOOLS), True, 11.0))
        self.assertIn("slower-than-startup-timeout", report["connected"]["causes"])
        self.assertFalse(report["usable"]["ok"])

    def test_missing_server_tools_and_failed_doctor(self):
        report = self.run_diagnose(exchange=lambda *a, **k: ({"memory_search"}, False, 0.1))
        self.assertEqual(report["usable"]["causes"], ["server-tools-missing", "doctor-failed"])

    def test_missing_runtime_is_reported(self):
        report = self.run_diagnose(runtime={**self.RUNTIME, "installed": False},
                                   exchange=lambda *a, **k: self.fail("must not start"))
        self.assertIn("runtime-missing", report["configured"]["causes"])

    def test_malformed_config_is_reported_not_raised(self):
        for config in ({"mcp_servers": None}, {"mcp_servers": {memory.SERVER: "x"}}, {"mcp_servers": []}):
            with patch.object(memory, "local_config", return_value=config), \
                 patch.object(memory, "inspect_runtime", return_value=self.RUNTIME):
                report = memory.diagnose("codex")
            self.assertEqual(report["configured"]["causes"], ["config-unreadable"])
            self.assertEqual(report["usable"]["causes"], ["skipped-not-configured"])

    def test_node_missing_is_not_also_foreign(self):
        report = self.run_diagnose(runtime={**self.RUNTIME, "node": ""}, server={"command": "x", "args": []},
                                   exchange=lambda *a, **k: self.fail("must not start"))
        self.assertEqual(report["configured"]["causes"], ["node-missing"])

    def exchange_with(self, **run):
        with patch.object(memory.subprocess, "run", **run):
            return memory.exchange(self.RUNTIME, "codex")

    def completed(self, stdout="", code=0):
        return subprocess.CompletedProcess([], code, stdout, "")

    def test_exchange_cause_codes(self):
        good = "".join(json.dumps(x) + "\n" for x in (
            {"id": 1, "result": {}}, {"id": 2, "result": {"tools": [{"name": n} for n in memory.TOOLS]}},
            {"id": 3, "result": {"content": [{"text": json.dumps({"ok": True})}]}}))
        tools, ok, _ = self.exchange_with(return_value=self.completed(good))
        self.assertEqual((tools, ok), (memory.TOOLS, True))
        cases = ((dict(side_effect=subprocess.TimeoutExpired("x", 1)), "timeout"),
                 (dict(side_effect=FileNotFoundError()), "cannot-start"),
                 (dict(return_value=self.completed("not json", 1)), "exit-status"),
                 (dict(return_value=self.completed("not json")), "bad-reply"),
                 (dict(return_value=self.completed(json.dumps({"id": 1, "error": {"code": -1}}))), "protocol-error"))
        for run, cause in cases:
            with self.assertRaises(ValueError) as caught:
                self.exchange_with(**run)
            self.assertEqual(str(caught.exception), cause)

    def test_probe_still_raises_memory_error(self):
        with patch.object(memory, "exchange", side_effect=ValueError("timeout")), self.assertRaises(memory.MemoryError):
            memory.probe(self.RUNTIME, "codex")
        with patch.object(memory, "exchange", return_value=({"memory_search"}, True, 0.1)), self.assertRaises(memory.MemoryError):
            memory.probe(self.RUNTIME, "codex")
        with patch.object(memory, "exchange", return_value=(set(memory.TOOLS), True, 0.1)):
            memory.probe(self.RUNTIME, "codex")

    def test_reconnect_steps_only_when_not_usable(self):
        report = self.run_diagnose(server=None)
        self.assertIn("not-declared", report["configured"]["causes"])
        self.assertTrue(any("mcp list" in step for step in report["reconnect"]))
        self.assertNotIn("reconnect", self.run_diagnose())

    def test_profile_alias_is_reported_and_reads_selected_home(self):
        from harness import agent_profile
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}), \
             patch.object(Path, "home", return_value=Path(home)), \
             patch.object(memory, "inspect_runtime", return_value=self.RUNTIME):
            with agent_profile("codex-2") as base:
                self.assertEqual(base, "codex")
                self.assertEqual(os.environ["CODEX_HOME"], str(Path(home) / ".codex-2"))
                report = memory.diagnose(base, "codex-2")
        self.assertEqual(report["agent"], "codex-2")
        self.assertIn("not-declared", report["configured"]["causes"])


if __name__ == "__main__":
    unittest.main()
