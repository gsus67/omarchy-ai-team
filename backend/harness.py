#!/usr/bin/env python3
"""On-demand native configuration and terminal launching; no resident service."""

import i18n
from i18n import tr

import argparse
import contextlib
import fcntl
from datetime import datetime, timezone
from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import native_plugins
import shared_memory
import native_launcher
import context_policy
import role_usage
import usage_start
import usage_summary
import price_table
import price_autofill
import memory_archive
import memory_backups
import memory_mcp
import safe_updates
import agent_runtime
import native_tools
import project_scope
import local_catalog
import native_pi
import nous_quota
import native_local_tools
import goals
import team
import native_team
import ai_check


AGENTS = {"claude": "Claude Code", "claude-2": "Claude Code-2", "codex": "Codex", "codex-2": "Codex-2", "pi": "Pi"}  # the order every list shows
CLI_NAME = {"codex-2": "codex", "claude-2": "claude"}
PROFILE_DIRS = {"codex-2": ".codex-2", "claude-2": ".claude-2"}
MANAGED = {"superpowers": ("Superpowers", native_plugins),
           "memory": (tr('Memoria compartida · ECC'), shared_memory),
           "local-tools": (tr('Herramientas locales'), native_local_tools),
           "team": (tr('Equipo'), native_team)}


class HarnessError(Exception):
    pass


def plugin_version():
    return json.loads((Path(__file__).resolve().parents[1] / 'manifest.json').read_text())['version']


USAGE_WRITER = Path(__file__).resolve().parents[1] / 'tools/usage/ai_team_usage.py'


def usage_window(config):
    return config.get('usageWindowDays', usage_start.DEFAULT_WINDOW_DAYS)


def refresh_usage_window():
    """One pass of the usage writer that republishes the breakdowns for the selected window; no limits are collected."""
    try:
        done = subprocess.run([sys.executable, str(USAGE_WRITER), '--breakdowns-only'], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise HarnessError(tr('No se pudo actualizar la medición; la ventana queda guardada. Reintenta.')) from error
    if done.returncode != 0:
        raise HarnessError(tr('No se pudo actualizar la medición; la ventana queda guardada. Reintenta.'))


def config_path():
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    if not base.is_absolute():
        raise HarnessError(tr('XDG_CONFIG_HOME debe ser una ruta absoluta.'))
    return base / "omarchy-ai-team" / "settings.json"


@contextlib.contextmanager
def after_update_lock():
    path = config_path().with_name('after-update.lock')
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def load_config():
    path = config_path()
    if path.is_symlink():
        raise HarnessError(tr('La configuración no puede ser un enlace simbólico.'))
    if not path.exists():
        return {"schemaVersion": 1, "agents": {key: False for key in AGENTS}}
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise HarnessError(tr('No se pudo leer la configuración; se ha conservado sin cambios.')) from error
    if not isinstance(value, dict) or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
        raise HarnessError(tr('Versión de configuración no compatible; no se ha modificado.'))
    agents = value.get("agents", {})
    if not isinstance(agents, dict) or any(type(agents.get(key, False)) is not bool for key in AGENTS):
        raise HarnessError(tr('Preferencias de agentes inválidas; no se han modificado.'))
    if value.get("defaultAgent", "") not in ("", *AGENTS):
        raise HarnessError(tr('El agente predeterminado no es válido; se conserva la configuración.'))
    if type(value.get("launcherMenuEnabled", False)) is not bool:
        raise HarnessError(tr('La preferencia del menú debe ser un booleano explícito.'))
    if value.get('language', 'system') not in ('system', 'es', 'en'):
        raise HarnessError(tr('El idioma del panel no es válido; se conserva la configuración.'))
    if 'usageWindowDays' in value and (type(value['usageWindowDays']) is not int or value['usageWindowDays'] not in usage_start.WINDOW_DAYS):
        raise HarnessError(tr('Ventana de medición inválida; elige 7 o 30 días.'))
    value.pop('lastSecurityAudit', None)
    installs = value.get('catalogInstalls', {})
    if (not isinstance(installs, dict) or any(
            key not in local_catalog.CATALOG or not isinstance(record, dict) or set(record) not in ({'deps'}, {'deps', 'versions'})
            or not isinstance(record['deps'], list) or any(not isinstance(dep, str) for dep in record['deps'])
            or ('versions' in record and (not isinstance(record['versions'], dict) or any(
                name not in [local_catalog.CATALOG[key].get('mise'), *record['deps']]
                or not isinstance(versions, list) or any(
                    not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+\-]*', version)
                    for version in versions)
                for name, versions in record['versions'].items())))
            for key, record in installs.items())):
        raise HarnessError(tr('El registro de instalaciones del catálogo no es válido; se conserva la configuración.'))
    memory_backups.settings(value)
    context_policy.settings(value)
    settings = local_catalog.validate(value)
    if 'localToolsSettings' in value:
        value['localToolsSettings'] = {key: settings[key] for key in value['localToolsSettings']}
    try:
        local_catalog.group_state(value)
    except local_catalog.CatalogError as error:
        raise HarnessError(str(error)) from error
    native_pi.provider_settings(value)
    desktop = value.get("launcherDesktop", {})
    if not isinstance(desktop, dict) or any(key not in AGENTS or not isinstance(digest, str) for key, digest in desktop.items()):
        raise HarnessError(tr('El registro de accesos del menú no es válido; se conserva.'))
    for component, (name, _) in MANAGED.items():
        if component + "Enabled" in value and type(value[component + "Enabled"]) is not bool:
            raise HarnessError(tr('La preferencia global de {name} no es válida; se ha conservado.', name=name))
        plugins = value.get(component, {})
        if component == 'team':
            if (not isinstance(plugins, dict) or type(plugins.get('enabled', False)) is not bool
                    or not isinstance(plugins.get('roles', {}), dict)
                    or not isinstance(plugins.get('notices', []), list)
                    or any(not isinstance(notice, str) for notice in plugins.get('notices', []))):
                raise HarnessError(tr('Preferencias de equipo inválidas; se conservan.'))
            for role, row in plugins.get('roles', {}).items():
                if (role not in team.ROLES or not isinstance(row, dict) or row.get('manual') is not True
                        or row.get('agent') not in ('claude', 'codex', 'pi')
                        or any(not isinstance(row.get(field), str) for field in ('model', 'effort'))):
                    raise HarnessError(tr('Papeles de equipo inválidos; se conservan.'))
            plugins = {key: row for key, row in plugins.items() if key not in ('enabled', 'roles', 'notices')}
        if (not isinstance(plugins, dict) or any(not isinstance(row, dict) for row in plugins.values())
                or any(not isinstance(row[key], str) for row in plugins.values()
                       for key in ("id", "version", "digest", "home", "checkedAt") if key in row)):
            raise HarnessError(tr('Preferencias de {name} inválidas; no se han modificado.', name=name))
    return value


def save_config(value):
    path = config_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".settings-", delete=False) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def mise_inventory():
    return native_tools.mise_inventory()


def is_launcher(path):
    return native_tools.is_launcher(path)


def detect_tool(name, inventory):
    return native_tools.detect_tool(name, inventory)


def team_context(config):
    selected = {key for key in AGENTS if config.get('agents', {}).get(key)}
    catalog = {'codex': config.get('teamModels', {}).get('codex', []),
               'pi': config.get('agentCatalogs', {}).get('pi', {}).get('models', []),
               'nousEffort': ['nous/'+model for model in config.get('piNousCatalog', {}).get('effortModels', [])]}
    free_pi = ['nous/'+row['id'] for row in config.get('piNousCatalog', {}).get('models', [])
               if row['id'].endswith(':free') and row.get('cost')
               and all(value == 0 for value in row['cost'].values())]
    return selected, catalog, free_pi


def usage_guard_pending(config, selected):
    if not config.get('team', {}).get('enabled') or not team.usage_guard(config)['enabled']:
        return []
    pending = []
    for agent in ('codex-2', 'codex'):
        if agent in selected:
            with agent_profile(agent) as cli:
                if not native_team.guard_hook_trusted(native_plugins.agent_home(cli)):
                    pending.append(AGENTS[agent])
    return pending


def team_state(config):
    selected, catalog, free_pi = team_context(config)
    return {**team.effective(config, selected, catalog, free_pi), 'selected': sorted(selected), 'usageGuard': team.usage_guard(config),
            'usageGuardPending': usage_guard_pending(config, selected)}


def component_options(key: str, config: dict) -> dict:
    if key == 'team':
        return {'settings_file': str(config_path()), 'team': team_state(config)}
    if key != "local-tools":
        return {}
    return {"settings_file": str(config_path()), "groups": local_catalog.enabled_groups(config)}


@contextmanager
def agent_profile(agent):
    previous = {name: os.environ.get(name) for name in ("CODEX_HOME", "CLAUDE_CONFIG_DIR")}
    if agent == "claude-2":
        os.environ["CLAUDE_CONFIG_DIR"] = str(Path.home() / ".claude-2")
    elif agent == "claude":
        os.environ.pop("CLAUDE_CONFIG_DIR", None)  # the main account never inherits claude-2's directory
    elif agent != "pi":
        os.environ["CODEX_HOME"] = str(Path.home() / (".codex-2" if agent == "codex-2" else ".codex"))
    try:
        yield "claude" if agent == "claude-2" else agent if agent in ("claude", "pi") else "codex"
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def agent_tool(agent, inventory):
    tool = detect_tool(CLI_NAME.get(agent, agent), inventory)
    if agent in PROFILE_DIRS and not (Path.home() / PROFILE_DIRS[agent]).is_dir():
        tool = {**tool, "installation": "missing-profile"}
    return tool


def manage_agent_cli(agent, action):
    # Install/uninstall act on the shared CLI; they never create or touch a profile directory.
    if action == 'uninstall' and agent in PROFILE_DIRS:
        raise HarnessError(tr('La CLI se comparte con el perfil principal; desinstálala desde ese perfil.'))
    inventory, warning = mise_inventory()
    if warning:
        raise HarnessError(warning)
    return agent_runtime.operate(CLI_NAME.get(agent, agent), action, inventory, detect_tool, mise_inventory)


def manage_agent_runtime(agent, action):
    inventory, warning = mise_inventory()
    if warning:
        raise HarnessError(warning)
    if agent in PROFILE_DIRS and not (Path.home() / PROFILE_DIRS[agent]).is_dir():
        raise HarnessError(tr('El perfil {profile} no existe; no se creará ni conectará una cuenta como efecto de instalar la CLI.', profile=agent))
    return agent_runtime.operate(CLI_NAME.get(agent, agent), action, inventory, detect_tool, mise_inventory)


def apply_component(config, component, agent, action, tool):
    with agent_profile(agent) as cli:
        options = component_options(component, config)
        if cli == 'pi' and component in ('memory','superpowers'):
            receipt = native_pi.apply_component(component, tool['binary'], action, config.get(component, {}).get(agent))
        else:
            receipt = MANAGED[component][1].apply(cli, tool["binary"], action, config.get(component, {}).get(agent), **options)
    receipt.update(agentBinary=tool["binary"], agentVersion=tool["version"])
    if action == "disable":
        receipt["released"] = True
    previous = config.setdefault(component, {}).get(agent, {})
    undated = lambda row: {key: value for key, value in row.items() if key != "checkedAt"}
    if undated(previous) == undated(receipt):
        return  # Nothing really changed: a new timestamp is not a reason to rewrite settings.
    config[component][agent] = receipt
    save_config(config)


def plugin_manages(config, key, agent, selected):
    """The plugin manages a selected profile or one with a receipt it has not released by disconnecting.

    A connection without a receipt, or remade after that release, belongs to the user.
    """
    receipt = config.get(key, {}).get(agent)
    return agent in selected or bool(receipt and not receipt.get("released"))


def manage_component(config, key, action, *, only=None, previous_selected=frozenset()):
    current = status(config)
    component = next(c for c in current["components"] if c["id"] == key)
    wanted = action == "enable" if action in ("enable", "disable") else component["enabled"]
    if action in ("enable", "disable", "sync"):
        if key == 'team':
            if config.get('team', {}).get('enabled', False) != wanted:
                config.setdefault('team', {})['enabled'] = wanted
                save_config(config)
        elif config.get(key + "Enabled", False) != wanted:
            config[key + "Enabled"] = wanted
            save_config(config)
    selected = {a["id"] for a in current["agents"] if a["enabled"]}
    if action in ("verify", "update") and not selected:
        raise HarnessError(tr('Selecciona los agentes en Modelos y agentes antes de comprobar o actualizar.'))
    inventory, _ = mise_inventory()
    errors = []
    for target in component["targets"]:
        agent = target["agent"]
        if only and agent != only:
            continue
        desired = wanted and agent in selected
        operation = None
        if action in ("enable", "disable", "sync"):
            if desired and (not target.get("enabled") or not target.get("verified")):
                operation = "enable"
            elif not desired and target.get("enabled") and plugin_manages(config, key, agent, selected | set(previous_selected)):
                operation = "disable"
        elif agent in selected:
            operation = action
        if operation:
            try:
                if target.get("blocked") and operation != "verify":
                    raise HarnessError(target["detail"])
                tool = agent_tool(agent, inventory)
                if tool["installation"] != "installed":
                    raise HarnessError(tr('El agente no está disponible para conectar el componente.'))
                apply_component(config, key, agent, operation, tool)
            except (HarnessError, native_plugins.PluginError, OSError, ValueError) as error:
                record = config.setdefault(key, {}).setdefault(agent, {})
                record.pop("digest", None)
                record.pop("released", None)  # The plugin still owes this operation: keep retrying.
                record["lastError"] = str(error)
                save_config(config)
                errors.append(f"{AGENTS[agent]}: {error}")
    if errors:
        raise HarnessError("; ".join(errors))


def refresh_connections(config, keys=MANAGED):
    errors = []
    if 'team' in keys and config.get('team', {}).get('enabled'):
        if team.families(team_context(config)[0]).count('codex'):
            warning = refresh_team_models(config)
            if warning: errors.append(warning)
        save_config(config)
    current = status(config); inventory, _ = mise_inventory()
    for component in current['components']:
        key = component['id']
        if key not in keys or not component.get('enabled'):
            continue
        for target in component['targets']:
            agent = target['agent']
            if config.get('agents', {}).get(agent) and target.get('enabled') and target.get('installed'):
                try:
                    apply_component(config, key, agent, 'verify', agent_tool(agent, inventory))
                except (HarnessError, native_plugins.PluginError, OSError, ValueError) as error:
                    receipt = config.setdefault(key, {}).setdefault(agent, {})
                    receipt.pop('digest', None); receipt['lastError'] = str(error); save_config(config)
                    errors.append(f'{key} · {AGENTS[agent]}: {error}')
    return errors


def refresh_team_models(config):
    """Refresh metadata via the neutral app-server, preserving cache on failure."""
    try:
        profile = next((key for key in ('codex-2', 'codex') if config.get('agents', {}).get(key)), None)
        if not profile: raise HarnessError(tr('Selecciona un perfil Codex para consultar su catálogo.'))
        inventory, _ = mise_inventory(); tool = agent_tool(profile, inventory)
        if tool['installation'] != 'installed': raise HarnessError(tr('La CLI Codex no está disponible.'))
        with agent_profile(profile):
            reply = native_plugins.codex_request(tool['binary'], 'model/list', {})
        rows = reply['data']
        if (not isinstance(rows, list) or not rows or any(
                not isinstance(row, dict) or not isinstance(row.get('id'), str)
                or not isinstance(row.get('supportedReasoningEfforts', []), list)
                or any(not isinstance(effort, dict) or not isinstance(effort.get('reasoningEffort'), str)
                       for effort in row.get('supportedReasoningEfforts', [])) for row in rows)):
            raise HarnessError(tr('El catálogo de modelos no es compatible.'))
        config['teamModels'] = {'codex': rows, 'checkedAt': datetime.now(timezone.utc).isoformat()}
        return ''
    except (HarnessError, native_plugins.PluginError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        warning = tr('Aviso: se conserva el catálogo anterior. {error}', error=str(error))
        notices = config.setdefault('team', {}).setdefault('notices', [])
        if warning not in notices: notices.append(warning)
        return warning


def team_agent_changed(config, agents):
    if not config.get('team', {}).get('enabled'):
        return False
    return any(agent['enabled'] and agent['installation'] == 'installed'
               and receipt.get('agentVersion') is not None
               and (receipt.get('agentBinary'), receipt['agentVersion']) != (agent['binary'], agent['version'])
               for agent in agents for receipt in [config.get('team', {}).get(agent['id'], {})])


def update_runtimes(config, choice):
    results = []; errors = []
    if choice in ("memory", "core"):
        try:
            installed = shared_memory.inspect_runtime()["installed"]
            if choice == "memory" or installed:
                results.append(safe_updates.memory_update())
                errors.extend(refresh_connections(config, ("memory",)))
        except (native_plugins.PluginError, OSError, ValueError, subprocess.SubprocessError) as error:
            errors.append(f"memory: {error}")
    if choice == "core":
        current = status(config); inventory, _ = mise_inventory()
        component = next(c for c in current["components"] if c["id"] == "superpowers")
        for target in component["targets"]:
            agent = target["agent"]
            if config.get("agents", {}).get(agent) and target.get("installed"):
                try:
                    before = target.get("version", "")
                    apply_component(config, "superpowers", agent, "update", agent_tool(agent, inventory))
                    after = config["superpowers"][agent].get("version", "")
                    changed = before != after or bool(target.get("digest") and target["digest"] != config["superpowers"][agent].get("digest"))
                    results.append({"component": "superpowers", "agent": agent, "version": after,
                                    "outcome": "updated" if changed else "current", "changed": changed})
                except (native_plugins.PluginError, OSError, ValueError) as error:
                    errors.append(f"Superpowers · {AGENTS[agent]}: {error}")
        updated_clis = set()
        for agent in current.get('agents', []):
            name = CLI_NAME.get(agent['id'], agent['id'])
            if (agent.get('enabled') and agent.get('installation') == 'installed' and agent.get('manager') == 'mise'
                    and name not in updated_clis):
                updated_clis.add(name)
                try:
                    results.append({'component': name + '-cli', **manage_agent_runtime(agent['id'], 'update')})
                except (native_plugins.PluginError, OSError, ValueError, subprocess.SubprocessError) as error:
                    errors.append(f"{agent['name']}: {error}")
        if updated_clis:
            errors.extend(refresh_connections(config))
        if config.get('local-toolsEnabled'):
            for row in local_catalog.snapshot(config):
                if row['mode'] == 'off' or not row['updateSupported']:
                    continue
                try:
                    config.setdefault('localToolsSettings', local_catalog.validate(config))['probes'].pop(row['id'], None)
                    save_config(config)
                    result = local_catalog.update(row['id'])
                    config['localToolsSettings']['probes'][row['id']] = result.pop('receipt')
                    save_config(config);results.append(result)
                except (native_plugins.PluginError, OSError, ValueError, subprocess.SubprocessError) as error:
                    errors.append(f"{row['name']}: {error}")
    # Keep partial successes reviewable even when another component is blocked.
    return {"updates": results, "updateErrors": errors}


def failure(error):
    reply = {"ok": False, "error": str(error)}
    try:
        reply["data"] = status()
    except (HarnessError, OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    print(json.dumps(i18n.localize(reply, i18n.current_language()), ensure_ascii=False))
    return 1


def component_status(key, config, agents):
    name, adapter = MANAGED[key]
    targets = []
    for agent in agents:
        receipt = config.get(key, {}).get(agent["id"], {})
        try:
            with agent_profile(agent["id"]) as cli:
                options = component_options(key, config)
                plugin = (native_pi.inspect_component(key, receipt)
                          if cli=='pi' and key in ('memory','superpowers') else adapter.inspect(cli, receipt, **options))
            if plugin["verified"] and (receipt.get("agentBinary") != agent["binary"] or receipt.get("agentVersion") != agent["version"]):
                plugin["verified"] = False
                plugin["detail"] = tr('El agente cambió desde la última comprobación. Pulsa Comprobar.')
            if receipt.get("lastError") and not plugin["verified"]:
                plugin["detail"] = i18n.saved_message(receipt["lastError"], i18n.current_language()) + "\n" + plugin["detail"]
            targets.append({"agent": agent["id"], "name": agent["name"], **plugin,
                            "available": agent["installation"] == "installed",
                            **({"lastError": receipt["lastError"]} if receipt.get("lastError") and not plugin["verified"] else {})})
        except (native_plugins.PluginError, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            targets.append({"agent": agent["id"], "name": agent["name"], "available": agent["installation"] == "installed", "blocked": True,
                            "installed": False, "enabled": False, "version": "", "verified": False,
                            "detail": str(error)})
    selected = {a["id"] for a in agents if a["enabled"]}
    wanted = config.get(key + "Enabled", key == "superpowers" and any(t.get("enabled") for t in targets))
    if key == 'team': wanted = config.get('team', {}).get('enabled', False)
    active = sum(t.get("enabled", False) and plugin_manages(config, key, t["agent"], selected) for t in targets)
    connected = sum(t["agent"] in selected and t.get("enabled", False) and t.get("verified", False) for t in targets)
    if not selected:
        detail = tr('Elige los agentes en Modelos y agentes. Se conectará al seleccionarlos.') if wanted else tr('Desactivado. Se aplicará a los agentes que selecciones.')
    elif wanted:
        detail = tr('Conexiones comprobadas: {connected} de {value2} agentes seleccionados.', connected=connected, value2=len(selected))
    elif active:
        detail = tr('Desactivación pendiente: {active} conexiones siguen activas.', active=active)
    else:
        detail = tr('Desactivado para los agentes seleccionados.')
    return {"id": key, "name": name, "installation": "managed", "integration": "native", "targets": targets, "enabled": wanted,
            "selectedCount": len(selected), "connectedCount": connected, "activeCount": active, "detail": detail}


def sync_launcher_menu(config):
    if config.get("launcherMenuEnabled") or config.get("launcherDesktop"):
        native_launcher.sync_menu(config, AGENTS, Path(__file__).resolve())
        save_config(config)


def select_agents(config: dict, wanted: set[str], *, reconcile: bool = False) -> None:
    previous = {agent for agent in AGENTS if config.get("agents", {}).get(agent, False)}
    wanted = set(wanted)
    added = wanted - previous
    if not reconcile and not added and not (previous - wanted):
        return

    inventory, _ = mise_inventory()
    targets = wanted if reconcile else added
    tools = {agent: agent_tool(agent, inventory) for agent in targets}
    for agent in targets:
        if agent not in previous and tools[agent]["installation"] == "missing-profile":
            # Explicitly selecting a second profile in the UI creates its (empty) directory; no account is created or connected.
            if agent in PROFILE_DIRS and detect_tool(CLI_NAME[agent], inventory)["installation"] == "installed":
                profile_dir = Path.home() / PROFILE_DIRS[agent]
                profile_dir.mkdir(mode=0o700, exist_ok=True)
                profile_dir.chmod(0o700)
                tools[agent] = agent_tool(agent, inventory)
            else:
                raise HarnessError(tr('Perfil {agent} no disponible.', agent=agent))

    errors = []
    failed = set()
    for agent in targets:
        if tools[agent]["installation"] != "installed":
            try:
                manage_agent_runtime(agent, 'install')
            except (HarnessError, native_plugins.PluginError, OSError, ValueError,
                    subprocess.TimeoutExpired) as error:
                errors.append(f"{AGENTS[agent]}: {error}")
                failed.add(agent)

    applied = wanted - (failed - previous)
    if applied == previous and errors and not reconcile:
        raise HarnessError('; '.join(errors))
    if applied != previous:
        if applied and is_new_config(config):
            initialize_goals(config)
        config["agents"] = {agent: agent in applied for agent in AGENTS}
        save_config(config)
    for key in MANAGED:
        try:
            manage_component(config, key, 'sync', previous_selected=previous)
        except (HarnessError, native_plugins.PluginError, OSError, ValueError,
                subprocess.TimeoutExpired) as error:
            errors.append(tr('{nombre}: {error}', nombre=MANAGED[key][0], error=str(error)))
    try:
        sync_launcher_menu(config)
    except (HarnessError, native_plugins.PluginError, OSError, ValueError,
            subprocess.TimeoutExpired) as error:
        errors.append(tr('Menú: {error}', error=str(error)))
    if errors:
        raise HarnessError('; '.join(errors))


def is_new_config(config):
    return not (any(config.get('agents', {}).values()) or 'enabled' in config.get('team', {})
                or any(key + 'Enabled' in config for key in (*MANAGED, *local_catalog.GROUPS)))


def initialize_goals(config):
    """Only for the first explicit selection: later, a goal that is off was turned off on purpose."""
    config.update({key + 'Enabled': True for key in MANAGED if key != 'team'})
    config.setdefault('team', {})['enabled'] = True


def prepare_agent(config, agent, *, make_default=False):
    """Explicit single step: select one profile and connect only that profile."""
    inventory, _ = mise_inventory()
    tool = agent_tool(agent, inventory)
    if tool['installation'] == 'missing-profile':
        raise HarnessError(tr('Perfil {agent} no disponible.', agent=agent))
    if tool['installation'] != 'installed':
        manage_agent_runtime(agent, 'install')
    before = json.dumps(config, sort_keys=True)
    if is_new_config(config):
        initialize_goals(config)
    config.setdefault('agents', {})[agent] = True
    if make_default:
        config['defaultAgent'] = agent
    if json.dumps(config, sort_keys=True) != before:
        save_config(config)
    errors = []
    if config.get('team', {}).get('enabled') and agent in ('codex', 'codex-2'):
        before = json.dumps(config, sort_keys=True)
        refresh_team_models(config)
        if json.dumps(config, sort_keys=True) != before:
            save_config(config)
    for key in MANAGED:
        try:
            manage_component(config, key, 'sync', only=agent)
        except (HarnessError, native_plugins.PluginError, OSError, ValueError,
                subprocess.TimeoutExpired) as error:
            errors.append(tr('{nombre}: {error}', nombre=MANAGED[key][0], error=str(error)))
    try:
        sync_launcher_menu(config)
    except (HarnessError, native_plugins.PluginError, OSError, ValueError,
            subprocess.TimeoutExpired) as error:
        errors.append(tr('Menú: {error}', error=str(error)))
    if errors:
        raise HarnessError('; '.join(errors))


def reconcile_selected(config):
    """Repair drift in marked profiles only; never installs, refreshes catalogs or touches backups."""
    current = status(config)
    repair, pending = goals.repair_plan(current['agents'], current['components'])
    report = {'repaired': [], 'pending': pending, 'errors': []}
    if not repair:
        return report
    inventory, _ = mise_inventory()
    for row in repair:
        agent, key = row['agent'], row['component']
        try:
            apply_component(config, key, agent, 'enable', agent_tool(agent, inventory))
            report['repaired'].append(row)
        except (HarnessError, native_plugins.PluginError, OSError, ValueError, subprocess.TimeoutExpired) as error:
            record = config.setdefault(key, {}).setdefault(agent, {})
            if record.get('lastError') != str(error) or 'digest' in record:
                record.pop('digest', None)
                record['lastError'] = str(error)
                save_config(config)
            report['errors'].append({**row, 'detail': str(error)})
    if report['repaired']:
        config['lastReconcile'] = {'at': datetime.now(timezone.utc).isoformat(), 'repaired': report['repaired']}
        save_config(config)
    return report


def launch_agent(config, agent, *, role='jefe', context_override=None, apply_context=True):
    if not agent:
        raise HarnessError(tr('Elige un agente predeterminado en Modelos y agentes.'))
    if not config.get("agents", {}).get(agent, False):
        raise HarnessError(tr('El perfil no está seleccionado. Selecciónalo en Modelos y agentes.'))
    inventory, _ = mise_inventory(); tool = agent_tool(agent, inventory)
    if tool["installation"] != "installed":
        raise HarnessError(tr('El perfil no está disponible; no se abrió ninguna sesión.'))
    with agent_profile(agent) as cli:
        native_launcher.launch(tool["binary"], agent, native_plugins.agent_home(cli),
                               config=config if apply_context else None, role=role,
                               context_override=context_override if apply_context else None)


def memory_status(config):
    result={"integration":"native","engine":"ECC Memory Vault","path":str(shared_memory.data_root()/"memory"),
            "projects":[],"runtime":{"installed":False,"version":""},"backupSettings":memory_backups.settings(config),
            "backupState":{},"timer":memory_backups.timer_status(config,Path(__file__).resolve())}
    try:
        runtime=shared_memory.inspect_runtime()
        result["runtime"]={"installed":bool(runtime["installed"] and runtime.get("node")),"version":runtime.get("version","")}
        result["projects"]=memory_archive.inventory(shared_memory.data_root()/"memory")
        result["backupState"]=memory_backups.state()
    except (OSError,ValueError,native_plugins.PluginError) as error:
        result["managementError"]=str(error)
    return result


def restore_key(project):
    if not project:return None
    path=Path(project).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise HarnessError(tr('Elige una carpeta de proyecto existente para reasociar la memoria.'))
    return memory_mcp.project_store(path,shared_memory.data_root()/"memory").name


def restore_map(pairs):
    """OLD=FOLDER pairs into archive key → store key, plus the folders to echo back to the panel."""
    keys,folders={},{}
    for pair in pairs:
        old,separator,folder=pair.partition('=')
        if not separator or not folder or old in keys:
            raise HarnessError(tr('Cada proyecto se reasocia una vez a una carpeta existente.'))
        keys[old]=restore_key(folder);folders[old]=folder
    return keys,folders


def status(config=None):
    config = config if config is not None else load_config()
    language = i18n.resolve(config.get('language', 'system'))
    with i18n.using(language):
        result = i18n.localize_saved_fields(i18n.localize(_status(config), language), language)
    result.update(language=language, languagePreference=config.get('language', 'system'),
                  translations=i18n.table(language))
    return result


def _status(config=None):
    config = config if config is not None else load_config()
    manifest_version = plugin_version()
    inventory, warning = mise_inventory()
    agents = []
    for key, name in AGENTS.items():
        tool = agent_tool(key, inventory)
        agents.append({"id": key, "name": name, **tool,
                       "manager": "mise" if agent_runtime.managed(CLI_NAME.get(key, key), tool, inventory) else "external" if tool['installation'] == 'installed' else "mise",
                       "cliInstalled": detect_tool(CLI_NAME.get(key, key), inventory)['installation'] == 'installed',
                       "enabled": config.get("agents", {}).get(key, False), "integration": "pending"})
    components = [component_status(key, config, agents) for key in MANAGED]
    models_missing = bool(config.get('team', {}).get('enabled') and not team_state(config)['roles'])
    for agent in agents:
        agent.update(goals.setup_state(agent, components, models_missing=models_missing))
    omarchy = shutil.which("omarchy")
    try:
        result = subprocess.run([omarchy, "version"], capture_output=True, text=True, timeout=3) if omarchy else None
        version = result.stdout.strip() if result and result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        version = ""
    default = config.get("defaultAgent", "")
    chosen = next((a for a in agents if a["id"] == default), None)
    can_launch = bool(chosen and chosen["enabled"] and chosen["installation"] == "installed")
    launch_detail = (tr('Predeterminado: {nombre}. Clic derecho en el icono abre su sesión nativa.', nombre=chosen['name']) if can_launch else
                     tr('Elige un perfil seleccionado para el botón Abrir predeterminado.'))
    memory_info = memory_status(config)
    pi_agent = next((row for row in agents if row['id']=='pi'), {})
    catalog = config.get('agentCatalogs', {}).get('pi', {})
    catalog = dict(catalog) if isinstance(catalog, dict) else {}
    if catalog and (catalog.get('agentBinary') != pi_agent.get('binary') or catalog.get('agentVersion') != pi_agent.get('version')):
        catalog.update(models=[], stale=True, detail=tr('La CLI cambió. Consulta su catálogo de nuevo; se conserva el modelo elegido.'))
    project_options = project_scope.project_options(config_path(), memory_info['projects'], local_catalog.validate(config)['projects'])
    provider_checks = {}
    for provider, record in config.get('piProviderChecks', {}).items():
        record = dict(record)
        if record.get('binary') != pi_agent.get('binary') or record.get('version') != pi_agent.get('version'):
            record.update(ready=False, remoteVerified=False, detail=tr('La CLI cambió; vuelve a comprobar autenticación y conexión.'))
        provider_checks[provider] = record
    local_component = next(row for row in components if row['id']=='local-tools')
    local_verified = bool(local_component['enabled'] and local_component['selectedCount'] > 0
                          and local_component['connectedCount'] == local_component['selectedCount'])
    result = {"schemaVersion": 1, "phase": 7, "omarchyVersion": version,
            "pluginVersion": manifest_version, "needsReconnect": config.get('pluginVersion') != manifest_version or team_agent_changed(config, agents),
            "agents": agents, "components": components,
            "needsReconcile": bool(goals.repair_plan(agents, components)[0]), "lastReconcile": config.get('lastReconcile', {}),
            "deferred": ["Grok"], "warning": warning,
            "launcher": {"defaultAgent": default, "canLaunch": can_launch, "detail": launch_detail,
                         "menu": native_launcher.inspect_menu(config, AGENTS, Path(__file__).resolve())},
            "memory": memory_info,
            "aiCheck": config.get('aiCheck', {}),
            "pi": {"providers": native_pi.provider_settings(config), "paidApproved": config.get("piPaidApproved",False),
                   "checks": provider_checks, "model": config.get("piManagedModel",{}).get("model", ""),
                   "catalog": catalog, "nousCatalog": config.get("piNousCatalog",{}), "quota": nous_quota.settings(config),
                   "quotaConnection": nous_quota.connection(config_path()) if native_pi.provider_settings(config)["nous"] else {"connected":False,"detail":tr('Desactivado; datos conservados.')}},
            "localTools": {"groups": {key: list(tools) for key, tools in local_catalog.GROUPS.items()}, "settings": local_catalog.validate(config), "rows": local_catalog.snapshot(config, bridge_verified=local_verified),
                           "enabled": local_component["enabled"], "bridgeVerified": local_verified,
                           "connectedCount": local_component["connectedCount"],
                           "projectOptions": project_options}}
    team_component = next(row for row in components if row['id'] == 'team')
    result['team'] = {**team_state(config), **team_component}
    result['context'] = context_policy.snapshot(config)
    result['usage'] = usage_summary.rows(agents)
    result['roleUsage'] = role_usage.summary(agents, window_days=usage_window(config))
    result['prices'] = {'rows': price_table.rows(), 'missing': price_table.missing(), 'autofill': config.get('priceAutofill', {})}
    result['goals'] = goals.rows(result, config)
    result['summary'] = goals.summary(result, result['goals'])
    return result


def catalog_context(settings_file, agent):
    # Read only the explicit config; no status/model metadata or agent execution.
    config = json.loads(memory_archive.read_file(Path(settings_file), 1024 * 1024))
    local_catalog.validate(config)
    if (config.get('schemaVersion') != 1 or type(config.get('local-toolsEnabled', False)) is not bool
            or not isinstance(config.get('agents', {}), dict)
            or type(config.get('agents', {}).get(agent, False)) is not bool):
        raise HarnessError(tr('Preferencias de capacidades locales no válidas.'))
    if not config.get('local-toolsEnabled') or not config.get('agents', {}).get(agent):
        return {'tools': [], 'detail': tr('Integración desactivada para este perfil.')}
    inventory, _ = mise_inventory()
    tool = agent_tool(agent, inventory)
    receipt = config.get('local-tools', {}).get(agent, {})
    with agent_profile(agent) as cli:
        options = component_options('local-tools', config)
        options['settings_file'] = str(settings_file)
        bridge = native_local_tools.inspect(cli, receipt, **options)
    verified = (bridge['verified'] and tool['installation'] == 'installed'
                and receipt.get('agentBinary') == tool['binary'] and receipt.get('agentVersion') == tool['version'])
    return local_catalog.context(config, Path.cwd(), verified)


def activate_tool(config: dict, key: str) -> None:
    value = local_catalog.validate(config)
    row = local_catalog.CATALOG[key]
    inventory, _ = mise_inventory()
    current = local_catalog.inspect(key, inventory)
    if row.get('mise') and current['installation'] != 'installed':
        def record_install(name, versions):
            record = config.setdefault('catalogInstalls', {}).setdefault(key, {'deps': []})
            if name != row['mise'] and name not in record['deps']:
                record['deps'].append(name)
            owned = record.setdefault('versions', {}).setdefault(name, [])
            record['versions'][name] = sorted(set(owned) | set(versions))
            save_config(config)
        local_catalog.install(key, on_install=record_install)
    inventory, _ = mise_inventory()
    value['probes'].pop(key, None)
    config['localToolsSettings'] = value
    save_config(config)
    value['probes'][key] = local_catalog.probe(key, inventory)
    save_config(config)


def uninstall_tool(config: dict, key: str) -> None:
    if key not in config.get('catalogInstalls', {}):
        raise HarnessError(tr('AI Team no instaló esta herramienta; se conserva.'))
    record = config['catalogInstalls'][key]
    versions = record.get('versions', {})
    names = [local_catalog.CATALOG[key]['mise'], *local_catalog.uninstall_deps(config, key)]
    try:
        for name in names:
            local_catalog.unuse(name, versions.get(name, []))
        inventory, warning = mise_inventory()
        if warning:raise HarnessError(warning)
        tool = local_catalog.inspect(key, inventory)
        if (local_catalog.managed(key, tool, inventory) or any(
                set(versions.get(name, [])) & local_catalog.installed_versions(inventory, name) for name in names)):
            raise HarnessError(tr('mise no confirmó la retirada; comprueba antes de reintentar.'))
    except (HarnessError, native_plugins.PluginError, OSError, ValueError, subprocess.SubprocessError) as error:
        raise HarnessError(f"{local_catalog.CATALOG[key]['name']}: {error}") from error
    # Keep provenance for a dependency still needed by another owned tool.
    for dependency in record['deps']:
        if dependency in names:continue
        for other, remaining in config['catalogInstalls'].items():
            if other != key and dependency in remaining['deps']:
                owned = remaining.setdefault('versions', {}).setdefault(dependency, [])
                remaining['versions'][dependency] = sorted(set(owned) | set(versions.get(dependency, [])))
                break
    del config['catalogInstalls'][key]
    value = local_catalog.validate(config)
    value['probes'].pop(key, None)
    config['localToolsSettings'] = value
    local_catalog.set_preference(config, key, 'default', 'auto')
    save_config(config)


def main(argv=None):
    try:
        preference = load_config().get('language', 'system')
    except (HarnessError, native_plugins.PluginError, OSError, ValueError):
        preference = 'system'
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) == 2 and arguments[0] == 'set-language' and arguments[1] in ('system', 'es', 'en'):
        preference = arguments[1]
    with i18n.using(i18n.resolve(preference)):
        return _main(argv)


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help=tr('Detectar sin instalar ni ejecutar agentes'))
    commands.add_parser("after-update", help=tr('Reconectar una vez las integraciones habilitadas tras actualizar el plugin'))
    commands.add_parser('ai-check', help=tr('Comprobar las conexiones con IA en proyectos temporales'))
    price_save = commands.add_parser('price-save', help=tr('Guardar precios de tokens (USD por millón)'))
    price_save.add_argument('changes', help=tr('JSON con los cambios de precio'))
    commands.add_parser('price-autofill', help=tr('Rellenar con la IA jefe los precios vacíos'))
    update = commands.add_parser("runtime-update", help=tr('Actualizar solo componentes instalados, conservando sesiones y preferencias'))
    update.add_argument("component", choices=("memory", "core"))
    agent_update = commands.add_parser("agent-update", help=tr('Actualizar la CLI mediante su gestor nativo sin cerrar sesiones'))
    agent_update.add_argument("agent", choices=AGENTS)
    commands.add_parser('agent-install', help=tr('Instalar la CLI con el gestor oficial sin crear perfiles ni cuentas')).add_argument('agent', choices=AGENTS)
    commands.add_parser('agent-uninstall', help=tr('Desinstalar la CLI gestionada por mise conservando cuentas y sesiones')).add_argument('agent', choices=AGENTS)
    catalog = commands.add_parser('agent-models', help=tr('Consultar el catálogo nativo de Pi sin iniciar inferencia'))
    catalog.add_argument('agent', choices=('pi',))
    auth = commands.add_parser('authenticate-profile', help=tr('Abrir autenticación nativa sin seleccionar otra cuenta ni activar componentes'))
    auth.add_argument('agent', choices=('claude','claude-2','codex','codex-2'))
    setting = commands.add_parser("set-agent", help=tr('Seleccionar los agentes que usarán los componentes del plugin'))
    setting.add_argument("agent", choices=AGENTS)
    setting.add_argument("enabled", choices=("true", "false"))
    def parse_agents(value):
        if value == "none":
            return set()
        agents = value.split(",")
        invalid = [agent for agent in agents if agent not in AGENTS]
        if invalid or not all(agents):
            choice = invalid[0] if invalid else value
            raise argparse.ArgumentTypeError(tr('Opción inválida: {choice} (elige entre {value2})', choice=repr(choice), value2=', '.join(AGENTS)))
        return set(agents)
    setting_many = commands.add_parser("set-agents", help=tr('Seleccionar varios agentes para los componentes del plugin'))
    setting_many.add_argument("agents", type=parse_agents, metavar="ids|none")
    commands.add_parser('reconcile-selected', help=tr('Reparar las conexiones de los perfiles marcados, sin instalar nada'))
    prepare = commands.add_parser('prepare-agent', help=tr('Preparar un perfil y, si se pide, usarlo como predeterminado'))
    prepare.add_argument('agent', choices=AGENTS)
    prepare.add_argument('--default', action='store_true')
    group = commands.add_parser("superpowers-all", help=tr('Gestionar una sola función sobre los agentes seleccionados'))
    group.add_argument("action", choices=("enable", "disable", "verify", "update"))
    memory = commands.add_parser("memory-all", help=tr('Gestionar la única memoria de los perfiles seleccionados'))
    memory.add_argument("action", choices=("enable", "disable", "verify"))
    default = commands.add_parser("set-default-agent", help=tr('Elegir el perfil del botón Abrir predeterminado'))
    default.add_argument("agent", choices=("none", *AGENTS))
    menu = commands.add_parser("launcher-menu", help=tr('Aplicar accesos de aplicaciones de los perfiles seleccionados'))
    menu.add_argument("enabled", choices=("true", "false"))
    launch = commands.add_parser("launch", help=tr('Abrir un perfil seleccionado mediante la terminal nativa'))
    launch.add_argument("agent", choices=AGENTS)
    launch_default = commands.add_parser("launch-default", help=tr('Abrir el perfil predeterminado del plugin'))
    def parse_context_override(text):
        value = json.loads(text)
        if not isinstance(value, dict):
            raise argparse.ArgumentTypeError(tr('La excepción de contexto debe ser un objeto JSON con motivo.'))
        return value
    for opening in (launch, launch_default):
        opening.add_argument('--role', choices=context_policy.ROLES, default='jefe')
        opening.add_argument('--context-override', type=parse_context_override)
    context_set = commands.add_parser('context-set', help=tr('Guardar un ajuste de contexto del plugin'))
    context_set.add_argument('key')
    context_set.add_argument('value')
    context_reset = commands.add_parser('context-reset', help=tr('Restaurar los ajustes de contexto recomendados'))
    for editing in (context_set, context_reset):
        editing.add_argument('--role', choices=context_policy.ROLES)
    for name in ('context-checkpoint', 'context-brief', 'context-precompact'):
        action = commands.add_parser(name, help=tr('Guardar estado explícito o consultar el índice mediante ECC'))
        action.add_argument('--instance', default=os.environ.get('AI_TEAM_CONTEXT'))
    backup_config=commands.add_parser("memory-configure",help=tr('Elegir destino, frecuencia y retención de copias'))
    backup_config.add_argument("destination")
    backup_config.add_argument("--hours",type=int,default=24,choices=(1,6,12,24,168))
    backup_config.add_argument("--keep",type=int,default=7)
    schedule=commands.add_parser("memory-schedule",help=tr('Aplicar el temporizador de respaldos'))
    schedule.add_argument("enabled",choices=("true","false"))
    backup=commands.add_parser("memory-backup",help=tr('Crear respaldo verificado con retención'))
    backup.add_argument("--scheduled",action="store_true")
    export=commands.add_parser("memory-export",help=tr('Exportar documentos nativos ECC'))
    export.add_argument("--project",default="all")
    commands.add_parser("memory-doctor",help=tr('Auditar almacén con las funciones oficiales de ECC'))
    for name in ("memory-preview","memory-restore"):
        restore=commands.add_parser(name,help=tr('Revisar o confirmar restauración sin sobrescribir'))
        restore.add_argument("archive")
        if name=="memory-restore":restore.add_argument("token")
        restore.add_argument("--project-path",default="")
        restore.add_argument("--project-map",action="append",default=[],metavar="CLAVE=CARPETA")
    commands.add_parser('team-models', help=tr('Actualizar catálogo nativo de modelos sin inferencia'))
    commands.add_parser('team-recommend', help=tr('Volver a los papeles recomendados'))
    usage_guard = commands.add_parser('usage-guard', help=tr('Guardar y parar al llegar a un porcentaje de consumo'))
    usage_guard.add_argument('enabled', choices=('true', 'false'))
    usage_guard.add_argument('percent', type=int)
    usage_window_command = commands.add_parser('usage-window', help=tr('Elegir la ventana de medición del consumo'))
    usage_window_command.add_argument('days', type=int, choices=usage_start.WINDOW_DAYS)
    team_set = commands.add_parser('team-set', help=tr('Elegir el modelo de un papel'))
    team_set.add_argument('role', choices=team.ROLES)
    team_set.add_argument('agent', choices=('claude', 'codex', 'pi'))
    team_set.add_argument('model')
    team_set.add_argument('--effort', default='')
    team_all = commands.add_parser('team-all', help=tr('Conectar el equipo a los perfiles seleccionados'))
    team_all.add_argument('action', choices=('enable', 'disable', 'verify', 'sync'))
    tools = commands.add_parser('local-tools-all', help=tr('Conectar conocimientos locales a los perfiles seleccionados'))
    tools.add_argument('action', choices=('enable','disable','verify','sync'))
    tool_group = commands.add_parser('tool-group', help=tr('Activar o desactivar un grupo del catálogo local'))
    tool_group.add_argument('group', choices=tuple(local_catalog.GROUPS))
    tool_group.add_argument('action', choices=('enable', 'disable'))
    choice = commands.add_parser('catalog-preference', help=tr('Configurar una herramienta por ámbito'))
    choice.add_argument('tool', choices=tuple(local_catalog.CATALOG))
    choice.add_argument('mode', choices=(*local_catalog.MODES,'inherit'))
    choice.add_argument('--scope', default='default')
    tool_update = commands.add_parser('catalog-update', help=tr('Actualizar una utilidad instalada mediante mise, sin podar versiones'))
    tool_update.add_argument('tool', choices=tuple(local_catalog.CATALOG))
    uninstall = commands.add_parser('catalog-uninstall', help=tr('Desinstalar solo utilidades instaladas por AI Team'))
    uninstall.add_argument('tool', choices=tuple(local_catalog.CATALOG))
    probe = commands.add_parser('catalog-probe', help=tr('Comprobar una versión sin instalar'))
    probe.add_argument('tool', choices=tuple(local_catalog.CATALOG))
    context = commands.add_parser('catalog-context', help=tr('Consultar capacidades desde la sesión sin inferencia'))
    context.add_argument('--settings', required=True)
    context.add_argument('--agent', choices=tuple(AGENTS), required=True)
    provider=commands.add_parser('pi-provider',help=tr('Guardar preferencias sin copiar credenciales ni activar pagos sin consentimiento'))
    provider.add_argument('provider',choices=('xai','nous'));provider.add_argument('enabled',choices=('true','false'))
    provider.add_argument('--confirm-paid',action='store_true')
    provider_check=commands.add_parser('pi-provider-check',help=tr('Comprobar autenticación local nativa, sin inferencia'))
    provider_check.add_argument('provider',choices=('xai','nous'))
    quota=commands.add_parser('pi-nous-quota',help=tr('Configurar el RPM oficial conocido y margen compartidos, sin inventar límites'))
    quota.add_argument('--rpm',type=int,required=True);quota.add_argument('--margin',type=int,default=25)
    resume_quota=commands.add_parser('pi-nous-resume',help=tr('Programar nueva admisión con un reset oficial declarado, sin iniciar modelos'))
    resume_quota.add_argument('reset')
    commands.add_parser('pi-nous-refresh',help=tr('Actualizar catálogo público gratuito y declaración Pi, sin autenticación ni inferencia'))
    probe_provider=commands.add_parser('pi-provider-probe',help=tr('Comprobar conexión con una consulta explícita al modelo elegido'))
    probe_provider.add_argument('provider',choices=('xai','nous'));probe_provider.add_argument('model')
    commands.add_parser('pi-auth',help=tr('Abrir la interfaz nativa Pi para autenticar de forma segura'))
    pi_model=commands.add_parser('pi-model',help=tr('Guardar el predeterminado Pi desde el catálogo, sin inferencia'))
    pi_model.add_argument('model')
    language_parser = commands.add_parser('set-language', help=tr('Guardar el idioma del panel'))
    language_parser.add_argument('language', choices=('system', 'es', 'en'))
    args = parser.parse_args(argv)
    if args.command in ('context-checkpoint', 'context-brief', 'context-precompact'):
        import context_state
        return context_state.main([args.command.removeprefix('context-'), *(['--instance', args.instance] if args.instance else [])])
    try:
        if args.command == 'catalog-context':
            try:
                print(json.dumps(catalog_context(args.settings, args.agent), ensure_ascii=False))
                return 0
            except (HarnessError, native_plugins.PluginError, OSError, ValueError, TypeError, KeyError):
                print(json.dumps(i18n.localize({'tools': [], 'error': tr('No se pudieron verificar las capacidades locales; no se ejecutó ningún agente.')}, i18n.current_language())))
                return 1
        config = load_config()
        message = ""
        operation = {}
        if args.command == 'set-language':
            config['language'] = args.language
            save_config(config)
            message = tr('Idioma guardado.')
        elif args.command in ('context-set', 'context-reset'):
            # Only the plugin's own settings.json changes: validate a copy, then save atomically.
            try:
                value = json.loads(args.value) if args.command == 'context-set' else None
            except ValueError:
                raise HarnessError(tr('El valor de contexto debe ser JSON válido.')) from None
            updated = (context_policy.with_value(config, args.key, value, args.role)
                       if args.command == 'context-set' else context_policy.without(config, args.role))
            config.clear()
            config.update(updated)
            save_config(config)
            message = tr('Ajuste de contexto guardado. Se aplica en sesiones nuevas.')
        elif args.command == 'price-save':
            try:
                price_table.set_values(json.loads(args.changes))
            except ValueError as error:
                raise HarnessError(str(error)) from None
            message = tr('Precio guardado.')
        elif args.command == 'price-autofill':
            outcome = price_autofill.run(config, status(config))
            config['priceAutofill'] = outcome
            save_config(config)
            if outcome['error']:
                raise HarnessError(outcome['error'])
            message = tr('Precios rellenados: {cantidad}.', cantidad=len(outcome['filled']))
        elif args.command == 'ai-check':
            config['aiCheck'] = ai_check.run(config, status(config))
            save_config(config)
            message = tr('Comprobación con IA terminada. Consulta los resultados por agente.')
        elif args.command == 'after-update':
            # One bar per monitor runs this at once: only the first may update and restart the shell.
            with after_update_lock():
                config = load_config()
                version = plugin_version()
                if config.get('pluginVersion') != version or team_agent_changed(config, status(config)['agents']):
                    errors = refresh_connections(config)
                    if native_pi.provider_settings(config)['nous']:
                        try:
                            nous_quota.configure(config_path(), True)
                        except (native_plugins.PluginError, OSError, ValueError) as error:
                            errors.append(str(error))
                    # Not on first install: nothing old is loaded yet.
                    if config.get('pluginVersion') not in (None, version): operation = {'shellRestart': True}
                    config['pluginVersion'] = version
                    save_config(config)
                    message = '; '.join(errors) if errors else tr('Reconectado tras actualizar a {version}', version=version)
        elif args.command in ('pi-provider','pi-provider-check','pi-model','pi-auth','pi-nous-refresh','pi-nous-quota','pi-provider-probe','pi-nous-resume'):
            if not config.get('agents',{}).get('pi'):
                raise HarnessError(tr('Selecciona Pi en Modelos y agentes antes de configurar sus proveedores.'))
            if args.command=='pi-provider':
                if args.enabled=='false':
                    config.setdefault('piProviders',{})[args.provider]=False
                    if args.provider=='xai':config['piPaidApproved']=False
                    save_config(config)
                native_pi.provider_choice(config,args.provider,args.enabled=='true',args.confirm_paid,settings_file=config_path())
                save_config(config)
                if args.provider=='nous' and args.enabled=='true':
                    operation={'nousCatalog':native_pi.refresh_nous(config,settings_file=config_path())}
                    save_config(config)
                message=tr('Catálogo Nous de precio cero y conexión nativa configurados; autenticación, acceso y cuotas pendientes. No se inició inferencia ni fallback de pago.') if args.provider=='nous' else tr('Preferencia xAI guardada. No se ejecutó inferencia; abre sesiones nuevas para cargar cambios.')
            elif args.command=='pi-provider-probe':
                inventory,_=mise_inventory();tool=agent_tool('pi',inventory)
                if tool['installation']!='installed':raise HarnessError(tr('Pi no está instalado.'))
                previous=config.setdefault('piProviderChecks',{}).get(args.provider)
                if previous:
                    previous.update(remoteVerified=False,detail=tr('Recomprobación remota pendiente; no se conserva como conexión activa tras un fallo.'))
                save_config(config)
                if args.provider=='xai':
                    catalog=config.get('agentCatalogs',{}).get('pi',{})
                    if catalog.get('agentBinary')!=tool['binary'] or catalog.get('agentVersion')!=tool['version']:
                        raise HarnessError(tr('La CLI cambió; consulta su catálogo antes de probar el modelo.'))
                record=native_pi.probe_provider(tool['binary'],config,args.provider,args.model,config_path())
                config.setdefault('piProviderChecks',{})[args.provider]={**record,'binary':tool['binary'],'version':tool['version']}
                save_config(config);message=record['detail']
            elif args.command=='pi-nous-resume':
                stamp=datetime.fromisoformat(args.reset.replace('Z','+00:00'))
                if stamp.tzinfo is None:raise HarnessError(tr('El reset debe incluir zona UTC o un offset explícito.'))
                operation=nous_quota.operate(config,nous_quota.paths(config_path())[1],'resume',reset_at=stamp.timestamp())
                message=tr('Admisión programada para el reset declarado. No se iniciaron modelos ni se reanudaron tareas automáticamente.')
            elif args.command=='pi-nous-quota':
                config['piNousQuota']={'requestsPerMinute':args.rpm,'marginPercent':args.margin}
                nous_quota.settings(config);save_config(config)
                message=tr('Límite declarado guardado con margen. No acredita una cuota del proveedor ni inicia consultas; solo limita RPM de sesiones que carguen nuestra extensión nativa. TPM y demás límites conservan los controles del proveedor.')
            elif args.command=='pi-nous-refresh':
                if not native_pi.provider_settings(config)['nous']:raise HarnessError(tr('Activa primero la preferencia Nous.'))
                operation={'nousCatalog':native_pi.refresh_nous(config,settings_file=config_path())};save_config(config)
                # Models can leave the catalog: the team skill must not keep pointing at them.
                if config.get('team',{}).get('enabled'):manage_component(config,'team','sync')
                message=tr('Catálogo de precio cero actualizado; cuenta y cuota efectivos siguen pendientes.')
            elif args.command=='pi-model':
                inventory,_=mise_inventory();tool=agent_tool('pi',inventory)
                catalog=config.get('agentCatalogs',{}).get('pi',{})
                if catalog.get('agentBinary')!=tool['binary'] or catalog.get('agentVersion')!=tool['version']:
                    raise HarnessError(tr('La CLI cambió; consulta su catálogo antes de elegir un predeterminado.'))
                native_pi.default_model(config,args.model);save_config(config)
                message=tr('Predeterminado Pi guardado. No se inició ninguna consulta; las sesiones abiertas conservan su modelo.')
            elif args.command=='pi-auth':
                launch_agent(config,'pi', apply_context=False)
                message=tr('Pi abierto. En su menú nativo /login elige el proveedor y completa la autenticación. AI Team no recibe la clave. Abrir no inicia una consulta al modelo.')
            else:
                if not native_pi.provider_settings(config)[args.provider]:raise HarnessError(tr('Activa primero ese proveedor.'))
                inventory,_=mise_inventory();tool=agent_tool('pi',inventory)
                if tool['installation']!='installed':raise HarnessError(tr('Pi no está instalado.'))
                record=native_pi.check_provider(tool['binary'],args.provider,config_path())
                config.setdefault('piProviderChecks',{})[args.provider]={**record,'binary':tool['binary'],'version':tool['version']}
                save_config(config);message=record['detail']
        elif args.command == 'tool-group':
            tools = local_catalog.set_group(config, args.group, args.action == 'enable')
            save_config(config)
            errors = []
            for key in tools:
                try:
                    activate_tool(config, key)
                except (HarnessError, native_plugins.PluginError, OSError, ValueError, subprocess.SubprocessError) as error:
                    errors.append(f"{local_catalog.CATALOG[key]['name']}: {error}")
            try:
                manage_component(config, 'local-tools', 'enable' if local_catalog.bridge_wanted(config) else 'disable')
            except (HarnessError, native_plugins.PluginError, OSError, ValueError, subprocess.SubprocessError) as error:
                errors.append(str(error))
            if errors:
                raise HarnessError('; '.join(errors))
            message = tr('Aplicado. Abre sesiones nuevas para usarlo.')
        elif args.command == 'catalog-preference':
            local_catalog.set_preference(config, args.tool, args.scope, args.mode)
            save_config(config)
            if args.mode == 'on':
                activate_tool(config, args.tool)
            bridge_wanted = local_catalog.bridge_wanted(config)
            if bridge_wanted != bool(config.get('local-toolsEnabled')):
                manage_component(config, 'local-tools', 'enable' if bridge_wanted else 'disable')
            message = tr('Preferencia guardada; Automático no instala y desactivar conserva instalación y datos.')
        elif args.command == 'catalog-uninstall':
            uninstall_tool(config, args.tool)
            message = tr('Desinstalado. Vuelve a Automático.')
        elif args.command == 'catalog-update':
            value = local_catalog.validate(config)
            value['probes'].pop(args.tool, None)
            config['localToolsSettings'] = value
            save_config(config)
            operation = local_catalog.update(args.tool)
            value['probes'][args.tool] = operation.pop('receipt')
            save_config(config)
            message = operation.get('detail') or (tr('Actualización comprobada.') if operation['changed'] else tr('La utilidad ya está en la versión disponible para su configuración.'))
        elif args.command == 'catalog-probe':
            inventory, _ = mise_inventory()
            value = local_catalog.validate(config)
            value['probes'].pop(args.tool, None)
            config['localToolsSettings'] = value
            save_config(config)
            value['probes'][args.tool] = local_catalog.probe(args.tool, inventory)
            config['localToolsSettings'] = value
            save_config(config)
            message = tr('Binario comprobado. La conexión nativa se verifica por separado.')
        elif args.command == 'usage-guard':
            team.set_usage_guard(config, args.enabled == 'true', args.percent)
            save_config(config)
            message = tr('Límite de consumo guardado.')
        elif args.command == 'usage-window':
            config['usageWindowDays'] = args.days
            save_config(config)
            refresh_usage_window()  # a failure keeps the saved preference: the state stays pending with a retry
            message = tr('Ventana de medición actualizada.')
        elif args.command in ('team-set', 'team-recommend', 'team-models'):
            if args.command == 'team-set':
                selected, catalog, free_pi = team_context(config)
                team.set_role(config, args.role, args.agent, args.model, args.effort,
                              selected=selected, catalog=catalog, free_pi=free_pi,
                              paid_ok=config.get('piPaidApproved', False))
                message = tr('Papel guardado.')
            elif args.command == 'team-recommend':
                team.reset(config)
                message = tr('Papeles recomendados restablecidos.')
            else:
                warning = refresh_team_models(config)
                message = warning or tr('Catálogo de modelos actualizado.')
                if warning:
                    operation = {'warnings': [message]}
            save_config(config)
            if config.get('team', {}).get('enabled'):
                manage_component(config, 'team', 'sync')
            warnings = team_state(config)['warnings']
            if warnings: operation.setdefault('warnings', []).extend(warnings)
        elif args.command == 'agent-models':
            if not config.get('agents', {}).get(args.agent):
                raise HarnessError(tr('Selecciona ese perfil en Modelos y agentes antes de consultar sus modelos.'))
            inventory, _ = mise_inventory(); tool = agent_tool(args.agent, inventory)
            if tool['installation'] != 'installed':
                raise HarnessError(tr('La CLI no está instalada; no se consultará otra cuenta o proveedor.'))
            operation = native_pi.model_catalog(tool['binary'], config.get('piNousCatalog'))
            config.setdefault('agentCatalogs', {})[args.agent] = {**operation, 'agentBinary': tool['binary'], 'agentVersion': tool['version']}
            save_config(config); message = operation['detail']
        elif args.command in ('agent-install', 'agent-uninstall'):
            verb = args.command.removeprefix('agent-')
            operation = manage_agent_cli(args.agent, verb)
            message = tr('CLI instalada y comprobada.') if verb == 'install' else tr('CLI desinstalada. Tus cuentas y sesiones se conservan.')
        elif args.command == "agent-update":
            operation = manage_agent_runtime(args.agent, 'update')
            message = (tr('CLI actualizada y comprobada.') if operation['changed'] else tr('La CLI ya está en la versión que permite su configuración nativa.')) + tr(' Abre sesiones nuevas; las abiertas y su ejecutable anterior se conservan.')
            errors = refresh_connections(config)
            if errors:
                print(json.dumps(i18n.localize({'ok': False, 'error': '; '.join(errors), 'message': message,
                                  'data': status(config), 'operation': operation}, i18n.current_language()), ensure_ascii=False))
                return 1
        elif args.command == "runtime-update":
            operation = update_runtimes(config, args.component)
            updated = sum(r["changed"] for r in operation["updates"])
            message = tr('Actualizaciones comprobadas: {updated}. Ya en versión disponible: {value2}.', updated=updated, value2=len(operation['updates']) - updated)
            if updated:
                message += tr(' Abre sesiones nuevas para cargar las mejoras. Las sesiones abiertas se conservan.')
            if operation["updateErrors"]:
                print(json.dumps(i18n.localize({"ok": False, "error": "; ".join(operation["updateErrors"]), "message": message,
                                  "data": status(config), "operation": operation}, i18n.current_language()), ensure_ascii=False))
                return 1
        elif args.command == "set-agent":
            wanted = {agent for agent in AGENTS if config.get("agents", {}).get(agent, False)}
            if args.enabled == "true":
                wanted.add(args.agent)
            else:
                wanted.discard(args.agent)
            select_agents(config, wanted, reconcile=True)
            message = tr('Agentes guardados. Abre sesiones nuevas.')
        elif args.command == "set-agents":
            select_agents(config, args.agents)
            message = tr('Agentes guardados. Abre sesiones nuevas.')
        elif args.command == 'reconcile-selected':
            with after_update_lock():
                config = load_config()
                report = reconcile_selected(config)
            if report['errors']:
                raise HarnessError('; '.join(tr('{perfil} · {componente}: {error}', perfil=AGENTS[row['agent']],
                                                componente=MANAGED[row['component']][0], error=row['detail'])
                                             for row in report['errors']))
            operation = {'reconcile': report}
            if report['repaired']:
                message = tr('Reparado: {lista}. Abre sesiones nuevas para usarlo.', lista=', '.join(
                    AGENTS[row['agent']] + ' · ' + MANAGED[row['component']][0] for row in report['repaired']))
        elif args.command == 'prepare-agent':
            prepare_agent(config, args.agent, make_default=args.default)
            message = tr('{nombre} preparado. Abre sesiones nuevas.', nombre=AGENTS[args.agent])
        elif args.command == "memory-configure":
            previous=memory_backups.settings(config)
            memory_backups.configure(config,args.destination,args.hours,args.keep,previous["enabled"])
            save_config(config)
            memory_backups.apply_timer(config,Path(__file__).resolve())
            save_config(config)
            message=tr('Destino y ajustes de copias guardados. No se han exportado datos.')
        elif args.command == "memory-schedule":
            previous=memory_backups.settings(config)
            memory_backups.configure(config,previous["destination"],previous["hours"],previous["keep"],args.enabled=="true")
            save_config(config)
            memory_backups.apply_timer(config,Path(__file__).resolve())
            save_config(config)
            message=tr('Programación aplicada. Se reintenta cada hora cuando la copia está pendiente.')
        elif args.command == "memory-backup":
            operation=memory_backups.run(config,shared_memory.inspect_runtime(),scheduled=args.scheduled)
            message=(tr('Respaldo verificado. {aviso}', aviso=operation.get('warning', ''))).strip() if "skipped" not in operation else tr('No corresponde crear una copia ahora.')
        elif args.command in ("memory-export","memory-doctor","memory-preview","memory-restore"):
            # Reviewing a backup is an explicit action, so a new machine may install ECC here; no agent is involved.
            restoring=args.command in ("memory-preview","memory-restore")
            runtime=shared_memory.ensure_runtime() if restoring else shared_memory.inspect_runtime()
            store=shared_memory.data_root()/"memory"
            key_map,folders=restore_map(args.project_map) if restoring else ({},{})
            with memory_backups.lock():
                if args.command=="memory-export":
                    import uuid
                    folder=memory_backups._destination(memory_backups.settings(config))
                    name="ai-team-export-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:12]+".zip"
                    operation=memory_archive.export(store,folder/name,runtime,project=args.project)
                    message=tr('Exportación verificada: {archivo}', archivo=operation['file'])
                elif args.command=="memory-doctor":
                    files=memory_archive.collect(store);memory_archive.validate_documents(files,runtime)
                    operation={"memoryCount":len(files)}
                    message=tr('ECC comprobó {cantidad} memorias sin problemas de integridad o secretos reconocidos.', cantidad=len(files))
                elif args.command=="memory-preview":
                    operation={"restorePreview":memory_archive.preview(Path(args.archive).expanduser(),store,runtime,restore_key(args.project_path),key_map=key_map)}
                    operation["restorePreview"].update(requestedArchive=args.archive,projectPath=args.project_path,projectMap=folders)
                    message=tr('Vista previa preparada. Confirma para añadir solo las memorias nuevas.')
                else:
                    operation=memory_archive.restore(Path(args.archive).expanduser(),store,runtime,args.token,restore_key(args.project_path),key_map=key_map)
                    message=tr('Restauración terminada: {cantidad} memorias nuevas; existentes conservadas. Abre sesiones nuevas para usarlas.', cantidad=operation['restoredCount'])
                    if operation['unverifiedProjects']:
                        message+=' '+tr('Siguen asociadas a su carpeta original: {lista}. Si cambió, restaura indicando la nueva.', lista=', '.join(operation['unverifiedProjects']))
        elif args.command == "set-default-agent":
            agent = "" if args.agent == "none" else args.agent
            if agent and not config.get("agents", {}).get(agent, False):
                raise HarnessError(tr('Selecciona el perfil antes de elegirlo como predeterminado.'))
            config["defaultAgent"] = agent
            save_config(config)
            message = tr('Predeterminado guardado. Se conserva el modelo configurado en la CLI.')
        elif args.command == "launcher-menu":
            config["launcherMenuEnabled"] = args.enabled == "true"
            save_config(config)
            native_launcher.sync_menu(config, AGENTS, Path(__file__).resolve())
            save_config(config)
            message = tr('Accesos del menú aplicados a los perfiles seleccionados.')
        elif args.command == 'authenticate-profile':
            inventory, _ = mise_inventory(); tool = agent_tool(args.agent, inventory)
            if (args.agent in PROFILE_DIRS and tool['installation'] == 'missing-profile'
                    and detect_tool(CLI_NAME[args.agent], inventory)['installation'] == 'installed'):
                # Explicit login click is the only place a profile directory is created.
                profile_dir = Path.home() / PROFILE_DIRS[args.agent]
                profile_dir.mkdir(mode=0o700, exist_ok=True)
                profile_dir.chmod(0o700)
                tool = agent_tool(args.agent, inventory)
            if tool['installation'] != 'installed':
                raise HarnessError(tr('Instala la CLI y prepara el perfil antes de autenticarlo.'))
            with agent_profile(args.agent) as cli:
                native_launcher.authenticate(tool['binary'], args.agent, native_plugins.agent_home(cli))
            message = tr('Autenticación nativa abierta en terminal. Completa el inicio de sesión con tu cuenta; abrirla no acredita autenticación ni activa componentes.')
        elif args.command in ("launch", "launch-default"):
            agent = args.agent if args.command == "launch" else config.get("defaultAgent", "")
            launch_agent(config, agent, role=args.role, context_override=args.context_override)
            message = tr('Lanzamiento solicitado para {nombre} con su modelo y permisos nativos.', nombre=AGENTS[agent])
        elif args.command in ("superpowers-all", "memory-all", "local-tools-all", "team-all"):
            key = args.command.removesuffix("-all")
            manage_component(config, key, args.action)
            message = tr('Aplicado. Abre sesiones nuevas para usarlo.')
        print(json.dumps(i18n.localize({"ok": True, "data": status(config), "message": message, "operation": operation}, i18n.resolve(config.get("language", "system"))), ensure_ascii=False))
        return 0
    except (KeyError, TypeError, AttributeError):
        return failure(tr('La respuesta o configuración del gestor nativo no es compatible. No se marcará la conexión como comprobada.'))
    except (HarnessError, team.TeamError, native_plugins.PluginError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        return failure(error)


if __name__ == "__main__":
    sys.exit(main())
