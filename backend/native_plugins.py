"""Thin Superpowers adapter around the agents' official plugin managers."""

from i18n import tr

import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import time
import tomllib
from datetime import datetime, timezone


PLUGIN = "superpowers@superpowers-marketplace"
MARKETPLACE = "obra/superpowers-marketplace"
OFFICIAL_MARKETPLACES = {
    "obra/superpowers-marketplace", "anthropics/claude-plugins-official", "openai/plugins"
}


class PluginError(Exception):
    pass


def agent_home(agent):
    if agent == "pi":
        import native_pi
        return native_pi.home()
    key = "CLAUDE_CONFIG_DIR" if agent == "claude" else "CODEX_HOME"
    path = Path(os.environ.get(key, str(Path.home() / (".claude" if agent == "claude" else ".codex"))))
    if not path.is_absolute():
        raise PluginError(tr('{key} debe ser una ruta absoluta.', key=key))
    return path


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def normalize_repo(value):
    return str(value).removeprefix("git+").removeprefix("https://github.com/").removesuffix(".git").rstrip("/")


def manifest(path, agent):
    value = read_json(path / (".claude-plugin" if agent == "claude" else ".codex-plugin") / "plugin.json")
    if value.get("name") != "superpowers" or normalize_repo(value.get("repository")) != "obra/superpowers":
        raise PluginError(tr('La instalación encontrada no identifica el repositorio oficial de Superpowers.'))
    return value


def fingerprint(path, agent):
    files = [path / (".claude-plugin" if agent == "claude" else ".codex-plugin") / "plugin.json"]
    files += sorted((path / "skills").glob("*/SKILL.md"))
    files += sorted(p for p in (path / "hooks").glob("*") if p.is_file())
    digest = hashlib.sha256()
    for file in files:
        digest.update(str(file.relative_to(path)).encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def cache_versions(home, plugin_id):
    marketplace = plugin_id.split("@", 1)[1]
    root = home / "plugins/cache" / marketplace / "superpowers"
    return sorted(p.parent.parent.name for p in root.glob("*/.codex-plugin/plugin.json"))


BOOTSTRAP_START = "\n\n<!-- omarchy-ai-team:superpowers:start -->\n"
BOOTSTRAP_END = "\n<!-- omarchy-ai-team:superpowers:end -->"


def instruction_block(original, component, instruction=None):
    """Replace only our named block, preserving every surrounding byte of text."""
    start_marker = f"\n\n<!-- omarchy-ai-team:{component}:start -->\n"
    end_marker = f"\n<!-- omarchy-ai-team:{component}:end -->"
    if not isinstance(original, str):
        raise PluginError(tr('Las instrucciones nativas no tienen un formato compatible.'))
    start_tag = start_marker.strip()
    end_tag = end_marker.strip()
    if start_tag in original or end_tag in original:
        if original.count(start_tag) != 1 or original.count(end_tag) != 1 or start_marker not in original:
            raise PluginError(tr('El bloque gestionado está alterado; se conservan las instrucciones.'))
        start = original.index(start_marker)
        end = original.find(end_marker, start)
        if end < 0:
            raise PluginError(tr('El bloque gestionado está incompleto; se conservan las instrucciones.'))
        replacement = "" if instruction is None else start_marker + instruction + end_marker
        return original[:start] + replacement + original[end + len(end_marker):]
    return original if instruction is None else original + start_marker + instruction + end_marker


def codex_instructions(original, path=None):
    if path is None:
        return instruction_block(original, "superpowers")
    skill = str(Path(path) / "skills/using-superpowers/SKILL.md")
    instruction = (f"Superpowers: si el plugin {PLUGIN} está habilitado para esta sesión, "
                   f"antes de responder o actuar lee y sigue la skill oficial using-superpowers en {json.dumps(skill, ensure_ascii=False)}. "
                   "Usa las otras skills oficiales pertinentes. Respeta las instrucciones y permisos del usuario; "
                   "no actives la metodología si el proyecto o la sesión deshabilitan el plugin.")
    return instruction_block(original, "superpowers", instruction)


def codex_user_layer(binary):
    response = codex_request(binary, "config/read", {"includeLayers": True})
    file = str(agent_home("codex") / "config.toml")
    layers = [layer for layer in response.get("layers", [])
              if layer.get("name", {}).get("type") == "user"
              and layer["name"].get("file") == file and not layer["name"].get("profile")]
    if len(layers) != 1 or not isinstance(layers[0].get("version"), str):
        raise PluginError(tr('Codex no confirmó la versión de su configuración; se conserva sin sobrescribir.'))
    return file, layers[0]


def set_codex_connection(binary, plugin_id, enabled, path):
    """Write activation and startup instructions atomically with Codex's writer."""
    file, layer = codex_user_layer(binary)
    original = layer["config"].get("developer_instructions", "")
    updated = codex_instructions(original, path if enabled else None)
    edits = []
    if layer["config"].get("plugins", {}).get(plugin_id, {}).get("enabled") != enabled:
        edits.append({"keyPath": f"plugins.{plugin_id}.enabled", "value": enabled, "mergeStrategy": "replace"})
    if updated != original:
        edits.append({"keyPath": "developer_instructions", "value": updated or None, "mergeStrategy": "replace"})
    if edits:
        codex_request(binary, "config/batchWrite", {"filePath": file, "expectedVersion": layer["version"],
                                                  "edits": edits, "reloadUserConfig": False})


def inspect(agent, receipt=None):
    """Read local state only: no CLI execution, downloads, or config writes."""
    home = agent_home(agent)
    record = receipt or {}
    candidates = []
    if agent == "claude":
        settings = read_json(home / "settings.json")
        installed = read_json(home / "plugins/installed_plugins.json").get("plugins", {})
        for plugin_id, rows in installed.items():
            if plugin_id.startswith("superpowers@"):
                for row in rows:
                    if row.get("scope") == "user":
                        candidates.append((plugin_id, Path(row["installPath"]), settings.get("enabledPlugins", {}).get(plugin_id, True)))
        known = read_json(home / "plugins/known_marketplaces.json")
    else:
        settings_path = home / "config.toml"
        settings = tomllib.loads(settings_path.read_text()) if settings_path.exists() else {}
        known = settings.get("marketplaces", {})
        for plugin_id, options in settings.get("plugins", {}).items():
            if not plugin_id.startswith("superpowers@"):
                continue
            marketplace = plugin_id.split("@", 1)[1]
            versions = list((home / "plugins/cache" / marketplace / "superpowers").glob("*/.codex-plugin/plugin.json"))
            if record.get("id") == plugin_id and record.get("cacheVersions") == cache_versions(home, plugin_id):
                expected = home / "plugins/cache" / marketplace / "superpowers" / record.get("version", "") / ".codex-plugin/plugin.json"
                if expected in versions:
                    versions = [expected]
            if len(versions) != 1:
                raise PluginError(tr('Hay versiones de Superpowers sin comprobar. Pulsa Comprobar para consultar su gestor nativo.'))
            candidates.append((plugin_id, versions[0].parent.parent, options.get("enabled", True)))
    if not candidates:
        return {"id": PLUGIN, "installed": False, "enabled": False, "version": "", "verified": False,
                "detail": tr('Se instalará desde obra/superpowers al activarlo.')}
    if len(candidates) != 1:
        raise PluginError(tr('Hay varias instalaciones de Superpowers; no se activará una copia adicional.'))
    plugin_id, path, enabled = candidates[0]
    marketplace = plugin_id.split("@", 1)[1]
    source = known.get(marketplace, {}).get("source", {})
    repository = source.get("repo", source.get("url", "")) if isinstance(source, dict) else source
    if normalize_repo(repository) not in OFFICIAL_MARKETPLACES:
        raise PluginError(tr('El marketplace encontrado no identifica una fuente oficial admitida.'))
    value = manifest(path, agent)
    digest = fingerprint(path, agent)
    verified = record.get("id") == plugin_id and record.get("digest") == digest and record.get("home") == str(home)
    detail = tr('Comprobado localmente') if verified else tr('Instalado; falta comprobar la carga nativa.')
    if agent == "codex" and enabled:
        original = settings.get("developer_instructions", "")
        if original != codex_instructions(original, path):
            verified = False
            detail = tr('Falta conectar el inicio de Superpowers. Pulsa Comprobar.')
    return {"id": plugin_id, "installed": True, "enabled": bool(enabled), "version": value.get("version", ""),
            "path": str(path), "verified": verified, "digest": digest,
            "checkedAt": record.get("checkedAt", "") if verified else "",
            "detail": detail}


def command(binary, arguments, json_output=False):
    try:
        # A neutral cwd: from ~ Codex reads ~/.codex/config.toml as a project layer,
        # which leaks the main profile's settings into a second CODEX_HOME.
        with tempfile.TemporaryDirectory(prefix="ai-team-native-") as directory:
            result = subprocess.run([binary, *arguments], cwd=directory, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired as error:
        raise PluginError(tr('El gestor nativo tardó demasiado. Comprueba el estado antes de reintentar.')) from error
    if result.returncode:
        raise PluginError(tr('El gestor nativo no completó {value1} (salida {value2}). Se conserva su estado real.', value1=' '.join(arguments[:2]), value2=result.returncode))
    if not json_output:
        return result.stdout
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise PluginError(tr('El gestor nativo devolvió una respuesta no compatible.')) from error


def codex_skills(binary):
    """List skills for a neutral cwd: from ~ the main profile's ~/.codex would act as a project layer."""
    with tempfile.TemporaryDirectory(prefix="ai-team-skills-") as directory:
        return codex_request(binary, "skills/list", {"cwds": [directory], "forceReload": True})


def codex_request(binary, method, params):
    """Use Codex's own config writer / skill discovery, without starting a thread."""
    directory = tempfile.TemporaryDirectory(prefix="ai-team-native-")
    process = subprocess.Popen([binary, "app-server", "--stdio"], cwd=directory.name, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffer = b""

    def request(identifier, name, parameters):
        nonlocal buffer
        process.stdin.write((json.dumps({"id": identifier, "method": name, "params": parameters}) + "\n").encode())
        process.stdin.flush()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                reply = json.loads(line)
                if reply.get("id") == identifier:
                    if "error" in reply:
                        raise PluginError(tr('Codex no pudo completar {name}; no se simulará el resultado.', name=name))
                    return reply["result"]
            if selector.select(0.5):
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise PluginError(tr('El servicio local de Codex terminó antes de responder.'))
                buffer += chunk
        raise PluginError(tr('El servicio local de Codex no respondió a tiempo.'))

    try:
        version = read_json(Path(__file__).resolve().parent.parent / 'manifest.json').get('version', 'development')
        request(1, "initialize", {"clientInfo": {"name": "ai-team", "version": version},
                                  "capabilities": {"experimentalApi": True}})
        process.stdin.write(b'{"method":"initialized"}\n')
        process.stdin.flush()
        return request(2, method, params)
    finally:
        selector.close()
        process.stdin.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()
        directory.cleanup()


def set_codex_enabled(binary, plugin_id, enabled):
    codex_request(binary, "config/value/write", {"keyPath": f"plugins.{plugin_id}.enabled",
                                               "value": enabled, "mergeStrategy": "replace"})


def apply(agent, binary, action, receipt=None):
    if action == "verify":
        inventory = command(binary, ["plugin", "list", "--json"], True)
        rows = inventory if agent == "claude" else inventory["installed"]
        rows = [row for row in rows if str(row.get("id" if agent == "claude" else "pluginId", "")).startswith("superpowers@")
                and (agent != "claude" or row.get("scope") == "user")]
        if len(rows) != 1:
            raise PluginError(tr('El gestor nativo debe registrar una única instalación de Superpowers para comprobarla.'))
        row = rows[0]
        plugin_id = row["id" if agent == "claude" else "pluginId"]
        receipt = {"id": plugin_id, "version": row["version"],
                   "cacheVersions": cache_versions(agent_home(agent), plugin_id)}
    current = inspect(agent, receipt)
    plugin_id = current["id"]
    marketplace = plugin_id.split("@", 1)[1]
    if action == "enable":
        if not current["installed"]:
            if agent == "claude":
                command(binary, ["plugin", "install", "superpowers", "--marketplace", MARKETPLACE, "--scope", "user", "--json"], True)
            else:
                command(binary, ["plugin", "marketplace", "add", MARKETPLACE, "--json"], True)
                command(binary, ["plugin", "add", plugin_id, "--json"], True)
        elif agent == "claude":
            command(binary, ["plugin", "enable", plugin_id, "--scope", "user", "--json"], True)
    elif action == "disable":
        if not current["installed"]:
            raise PluginError(tr('Superpowers no está instalado en este agente.'))
        if agent == "claude":
            command(binary, ["plugin", "disable", plugin_id, "--scope", "user", "--json"], True)
    elif action == "update":
        if not current["installed"]:
            raise PluginError(tr('Activa Superpowers antes de actualizarlo.'))
        if agent == "claude":
            command(binary, ["plugin", "marketplace", "update", marketplace])
            command(binary, ["plugin", "update", plugin_id, "--scope", "user", "--json"], True)
        else:
            command(binary, ["plugin", "marketplace", "upgrade", marketplace, "--json"], True)
            try:
                command(binary, ["plugin", "add", plugin_id, "--json"], True)
            finally:
                if not current["enabled"]:
                    set_codex_enabled(binary, plugin_id, False)
    elif action != "verify":
        raise PluginError(tr('Acción de Superpowers no admitida.'))

    # Query the native inventory explicitly, never as a side effect of status.
    if agent == "claude":
        rows = command(binary, ["plugin", "list", "--json"], True)
        row = next((row for row in rows if row.get("id") == plugin_id and row.get("scope") == "user"), None)
        if row is None:
            raise PluginError(tr('Claude no registra la instalación de Superpowers.'))
        path = Path(row["installPath"])
        details = command(binary, ["plugin", "details", plugin_id])
        if "using-superpowers" not in details:
            raise PluginError(tr('Claude no reconoce las skills de Superpowers.'))
    else:
        rows = command(binary, ["plugin", "list", "--marketplace", marketplace, "--json"], True)["installed"]
        row = next((row for row in rows if row.get("pluginId") == plugin_id), None)
        if row is None:
            raise PluginError(tr('Codex no registra la instalación de Superpowers.'))
        path = agent_home(agent) / "plugins/cache" / marketplace / "superpowers" / row["version"]
        desired = action == "enable" if action in ("enable", "disable") else bool(row["enabled"])
        set_codex_connection(binary, plugin_id, desired, path)
        rows = command(binary, ["plugin", "list", "--marketplace", marketplace, "--json"], True)["installed"]
        row = next((row for row in rows if row.get("pluginId") == plugin_id), None)
        if row is None or bool(row["enabled"]) != desired:
            raise PluginError(tr('La configuración nativa no coincide con la conexión solicitada.'))
        if row["enabled"]:
            result = codex_skills(binary)
            skills = [skill for entry in result["data"] for skill in entry.get("skills", [])
                      if skill.get("pluginId") == plugin_id and skill.get("enabled")]
            if not any(skill["name"].endswith(":using-superpowers") for skill in skills):
                raise PluginError(tr('Codex no carga las skills de Superpowers en una sesión nueva.'))
    value = manifest(path, agent)
    if action in ("enable", "disable") and bool(row["enabled"]) != (action == "enable"):
        raise PluginError(tr('El estado nativo no coincide con el cambio solicitado.'))
    if action == "update" and bool(row["enabled"]) != current["enabled"]:
        raise PluginError(tr('El actualizador cambió la activación; revisa su estado antes de continuar.'))
    return {"id": plugin_id, "version": value["version"], "digest": fingerprint(path, agent),
            "home": str(agent_home(agent)), "checkedAt": datetime.now(timezone.utc).isoformat(),
            "cacheVersions": cache_versions(agent_home(agent), plugin_id) if agent == "codex" else []}
