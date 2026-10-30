#!/usr/bin/env python3
"""Lint against the installed native UI without copying it into the plugin."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="ai-team-qml-imports-") as directory:
    shell = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "shell"
    (Path(directory) / "qs").symlink_to(shell, target_is_directory=True)
    result = subprocess.run(["/usr/lib/qt6/bin/qmllint", "-I", directory, "--json", "-",
                             *[str(p) for p in sorted(ROOT.glob("*.qml"))]],
                            capture_output=True, text=True)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        print(result.stderr or result.stdout, file=sys.stderr)
        sys.exit(1)
    failed = result.returncode != 0
    for file in report["files"]:
        print(Path(file["filename"]).name + (": OK" if file["success"] else ": FAILED"))
        failed = failed or not file["success"]
        for warning in file["warnings"]:
            print(f'{warning["line"]}: {warning["type"]}: {warning["message"]}')
    sys.exit(1 if failed else 0)
