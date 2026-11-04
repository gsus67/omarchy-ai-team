"""Stage official runtimes, protect live clients, and retain recoverable originals."""

from i18n import tr

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request
import uuid

import native_plugins
import shared_memory
import memory_archive
import memory_backups
import runtime_guard


BACKEND_OPERATION_TIMEOUT_SECONDS = 180


class UpdateError(native_plugins.PluginError):
    pass


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise UpdateError(tr('La fuente oficial no confirmó una versión estable válida.'))
    return tuple(map(int, value.split('.')))


def metadata(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Omarchy-AI-Team', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024:
        raise UpdateError(tr('Los metadatos oficiales superan el límite admitido.'))
    return json.loads(content)


def recovery_dir(kind):
    root = memory_archive.safe_path(shared_memory.data_root() / 'recovery')
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    folder = root / (kind + '-' + uuid.uuid4().hex)
    folder.mkdir(mode=0o700)
    return folder


def record(folder, value):
    path = memory_archive.safe_path(folder / 'result.json')
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as output:
        json.dump({'checkedAt': datetime.now(timezone.utc).isoformat(), **value}, output, ensure_ascii=False, indent=2)
        output.flush(); os.fsync(output.fileno())
    if not memory_archive.sync_directory(folder):
        raise UpdateError(tr('No se confirmó la persistencia del registro de recuperación.'))
    return value


def sync_folders(*paths):
    if not all(memory_archive.sync_directory(path) for path in paths):
        raise UpdateError(tr('No se confirmó la persistencia de la sustitución; se recuperará la versión anterior.'))


def memory_update():
    current = shared_memory.inspect_runtime()
    if not current['installed']:
        raise UpdateError(tr('Memoria no está instalada; Actualizar no instala componentes nuevos.'))
    prefix = memory_archive.safe_path(shared_memory.data_root() / 'tools/ecc-memory')
    expected = prefix / 'node_modules' / shared_memory.PACKAGE
    memory_archive.safe_path(expected)
    if Path(current['path']) != expected:
        raise UpdateError(tr('ECC pertenece a otra instalación. Se conserva y debe actualizarse con su gestor original.'))
    if any(p.name not in ('node_modules', 'package.json', 'package-lock.json') for p in prefix.iterdir()):
        raise UpdateError(tr('El prefijo ECC contiene archivos ajenos; se conserva sin sustituir.'))
    npm = shutil.which('npm')
    if not npm or not current['node']:
        raise UpdateError(tr('Node y npm deben estar disponibles mediante su gestor nativo.'))
    store = shared_memory.data_root() / 'memory'
    with runtime_guard.lease(runtime_guard.memory_lock(store), exclusive=True), memory_backups.lock():
        runtime_guard.assert_idle('memory')
        latest = metadata('https://registry.npmjs.org/ecc-universal/latest')
        target = latest.get('version')
        if (latest.get('name') != shared_memory.PACKAGE or latest.get('license') != 'MIT'
                or native_plugins.normalize_repo(latest.get('repository', {}).get('url')) != 'affaan-m/ECC'):
            raise UpdateError(tr('La publicación npm no confirmó origen y licencia oficiales.'))
        if version(target) <= version(current['version']):
            with tempfile.TemporaryDirectory(prefix='ai-team-update-probe-') as temporary:
                shared_memory.probe(current, 'codex', store=Path(temporary) / 'memory')
            return {'component': 'memory', 'outcome': 'current', 'version': current['version'], 'changed': False}
        folder = recovery_dir('ecc')
        files = memory_archive.collect(store)
        archive = folder / 'memory.zip'
        if files:
            memory_archive.export(store, archive, current)
        with tempfile.TemporaryDirectory(prefix='.ecc-update-', dir=prefix.parent) as temporary:
            staged = Path(temporary) / 'ecc-memory'
            result = subprocess.run([npm, 'install', '--prefix', str(staged), '--omit=dev', '--ignore-scripts',
                                     '--no-audit', '--no-fund', '--registry', 'https://registry.npmjs.org', f'ecc-universal@{target}'], capture_output=True, text=True, timeout=BACKEND_OPERATION_TIMEOUT_SECONDS)
            if result.returncode:
                raise UpdateError(tr('npm no completó la preparación; la instalación actual se conserva.'))
            candidate = shared_memory.runtime_at(staged / 'node_modules' / shared_memory.PACKAGE, current['node'])
            if candidate['version'] != target:
                raise UpdateError(tr('npm no instaló la versión solicitada; no se sustituirá la instalación.'))
            if files:
                loaded, _ = memory_archive.load(archive, candidate)
                if loaded != files:
                    raise UpdateError(tr('La versión nueva no recuperó la memoria previa.'))
            shared_memory.probe(candidate, 'codex', store=Path(temporary) / 'probe-memory')
            runtime_guard.assert_idle('memory')
            if shared_memory.inspect_runtime()['digest'] != current['digest'] or memory_archive.collect(store) != files:
                raise UpdateError(tr('La instalación o memoria cambió durante la preparación; reintenta.'))
            previous = folder / 'previous-runtime'
            os.rename(prefix, previous)
            try:
                os.rename(staged, prefix)
                installed = shared_memory.inspect_runtime()
                if installed['digest'] != candidate['digest'] or installed['version'] != target:
                    raise UpdateError(tr('No se confirmó la instalación nueva.'))
                shared_memory.probe(installed, 'codex', store=Path(temporary) / 'final-probe')
                sync_folders(prefix.parent, folder)
                result = record(folder, {'component': 'memory', 'outcome': 'updated', 'changed': True,
                                        'previousVersion': current['version'], 'version': target,
                                        'previousDigest': current['digest'], 'digest': installed['digest'],
                                        'recovery': str(folder), 'memoryCount': len(files)})
            except Exception:
                # Data were only read/validated. Never roll back another writer's data.
                if prefix.exists():
                    os.rename(prefix, folder / 'rejected-runtime')
                os.rename(previous, prefix)
                raise
        return result
