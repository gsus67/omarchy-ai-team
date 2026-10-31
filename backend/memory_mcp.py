#!/usr/bin/env python3
"""Select ECC's official project root, then replace this process with its MCP."""

import i18n
from i18n import tr

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

import runtime_guard


def project_identity(cwd):
    directory = Path(cwd).resolve(strict=True)
    try:
        # Resolve this session's cwd, not a repository selected by inherited GIT_*.
        git_environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        result = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                cwd=directory, env=git_environment, capture_output=True, text=True, timeout=3)
        identity = Path(result.stdout.strip()).resolve() if result.returncode == 0 else directory
    except FileNotFoundError:
        identity = directory
    return identity


def project_store(cwd, base):
    key = hashlib.sha256(os.fsencode(project_identity(cwd))).hexdigest()
    return Path(base) / "projects" / key


def record_project(store, cwd):
    # Optional display metadata, never used to grant access or execute a path.
    # Preserve foreign files and keep normal MCP operation if labels cannot be saved.
    try:
        if not store.is_dir() or any(p.is_symlink() for p in (store,*store.parents)):return
        identity=project_identity(cwd)
        name=identity.parent.name if identity.name=='.git' else identity.name
        fd=os.open(store/'.ai-team-project.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as out:json.dump({'schemaVersion':1,'name':name[:120]},out)
    except (OSError,ValueError,subprocess.TimeoutExpired):pass


def main():
    with i18n.using(i18n.configured_language()):
        return _main()


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("claude", "codex", "pi"), required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--store", required=True)
    args = parser.parse_args()
    if not Path(args.store).is_absolute():
        parser.error(tr('El almacén de memoria debe ser una ruta absoluta.'))
    home=Path.home()
    environment = {**os.environ, "ECC_MEMORY_HARNESS": args.agent, "ECC_MEMORY_ALLOW_USER_SCOPE": "0",
                   "AI_TEAM_ECC_RUNTIME": str(args.runtime), "AI_TEAM_MEMORY_STORE": str(args.store),
                   "AI_TEAM_PROJECTS_ROOT": str(home/"Work")}
    # A session opened in the home folder belongs to no project: it says which one in conversation.
    if Path.cwd().resolve()!=home.resolve():
        store=project_store(Path.cwd(),args.store)
        record_project(store,Path.cwd())
        identity=project_identity(Path.cwd())
        environment.update(AI_TEAM_SESSION_STORE=str(store),
                           AI_TEAM_SESSION_PROJECT=identity.parent.name if identity.name==".git" else identity.name)
    script = str(Path(__file__).with_name("memory_projects.mjs"))
    with runtime_guard.lease(runtime_guard.memory_lock(args.store), inherit=True):
        os.execvpe(args.node, [args.node, script], environment)


if __name__ == "__main__":
    main()
