#!/usr/bin/env python3
"""Read the current Codex profile's five-hour quota without starting a model."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import context_run
import harness
import native_plugins


def five_hour_window(response):
    buckets = response.get("rateLimitsByLimitId") or {}
    bucket = buckets.get("codex") or response.get("rateLimits") or {}
    for name in ("primary", "secondary"):
        window = bucket.get(name)
        if not isinstance(window, dict) or window.get("windowDurationMins") != 300:
            continue
        used = window.get("usedPercent")
        if type(used) not in (int, float) or not 0 <= used <= 100:
            raise ValueError("El proveedor no devolvió un porcentaje de cuota válido.")
        return {"windowDurationMins": 300, "usedPercent": used,
                "remainingPercent": round(100 - used, 2), "resetsAt": window.get("resetsAt"),
                "handoffRequired": used >= 90}
    raise ValueError("El proveedor no confirmó una ventana de cuota de cinco horas.")


def continuation_availability(response, window):
    bucket=(response.get("rateLimitsByLimitId") or {}).get("codex") or response.get("rateLimits") or {}
    blocked=[window] if window["handoffRequired"] else []
    for key in ("primary","secondary"):
        value=bucket.get(key)
        if isinstance(value,dict) and type(value.get("usedPercent")) in (int,float) and value["usedPercent"]>=100:
            blocked.append(value)
    resets=[value.get("resetsAt") for value in blocked]
    known=all(type(value) in (int,float) for value in resets)
    return {"availableForContinuation":not blocked,
            "continuationResetsAt":max(resets) if resets and known else None}


def task_tokens(before, after):
    """Per-task token delta from two private snapshots; never touches quota, auth or models."""
    try:
        delta = context_run.token_delta(*(json.loads(Path(path).read_text()) for path in (before, after)))
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    # Cost is reported only when a CLI delivers it; the snapshots carry no cost.
    print(json.dumps({"ok": True, "tokens": delta, "cost": None}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("current", "codex", "codex-2"), default="current")
    parser.add_argument("--task-tokens", action="store_true", help="Compare two token snapshots instead of reading quota")
    parser.add_argument("--before")
    parser.add_argument("--after")
    args = parser.parse_args()
    if args.task_tokens:
        if not (args.before and args.after):
            parser.error("--task-tokens requires --before and --after")
        return task_tokens(args.before, args.after)
    if args.profile != "current":
        os.environ["CODEX_HOME"] = str(Path.home() / (".codex" if args.profile == "codex" else ".codex-2"))
    try:
        inventory, _ = harness.mise_inventory()
        tool = harness.detect_tool("codex", inventory)
        if tool["installation"] != "installed":
            raise ValueError("No se encontró una CLI de Codex instalada.")
        response = native_plugins.codex_request(tool["binary"], "account/rateLimits/read", {})
        window = five_hour_window(response)
        print(json.dumps({"ok": True, "profileHome": str(native_plugins.agent_home("codex")),
                          "observedAt": datetime.now(timezone.utc).isoformat(), **window,
                          **continuation_availability(response,window)}))
        return 0
    except (native_plugins.PluginError, OSError, ValueError, TypeError, AttributeError) as error:
        print(json.dumps({"ok": False, "error": str(error), "handoffRequired": None}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
