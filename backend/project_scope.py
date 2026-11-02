"""Project identifiers and labels shared by local tool preferences and memory."""

from i18n import tr

import json
from pathlib import Path
import re

import memory_archive
import memory_mcp
import native_plugins


class ScopeError(native_plugins.PluginError):
    pass


def scope_key(scope):
    if not isinstance(scope, str) or (scope != 'default' and not re.fullmatch('[a-f0-9]{64}', scope)):
        raise ScopeError(tr('El ámbito de proyecto no es válido.'))
    return scope


def project_key(project):
    return memory_mcp.project_store(project, Path('/')).name


def project_options(settings_file, memories=(), projects=()):
    result = {key: {'value': key, 'label': tr('Proyecto {nombre}', nombre=key[:10])} for key in projects}
    result.update({row['key']: {'value': row['key'], 'label': row['label']} for row in memories})
    root = memory_archive.safe_path(Path(settings_file).parent / 'projects')
    if root.exists():
        for folder in root.iterdir():
            if re.fullmatch('[a-f0-9]{64}', folder.name):
                metadata = folder / '.ai-team-project.json'
                try:
                    row = json.loads(memory_archive.read_file(metadata, 4096))
                    if row.get('schemaVersion') == 1 and isinstance(row.get('name'), str):
                        result[folder.name] = {'value': folder.name, 'label': ' · '.join(filter(None, [memory_archive.clean_name(row['name'], 100), folder.name[:8]]))}
                except (OSError, ValueError, native_plugins.PluginError):
                    pass
    return [{'value': 'default', 'label': tr('Preferencias generales')}, *sorted(result.values(), key=lambda row: (row['label'], row['value']))]
