"""Muse Code's native CLI (`muse`). Only what `muse --help` proves is integrated.

Supported: detection/version, terminal launch, native login and `muse exec` for the
review/simple team roles. Not integrated (nothing in the help documents the format):
the MCP file for stdio servers, the instructions file and hooks. Those components are
reported as unsupported instead of writing a guessed configuration.
"""

import json
import os
from pathlib import Path
import re
import stat

from i18n import tr

import native_plugins

UNSUPPORTED = tr('Muse Code no admite este componente: su ayuda no documenta el formato necesario. No se escribió ninguna configuración.')
TEXTS = (UNSUPPORTED,)
FALLBACK_MODELS = ['muse-wonder-1.0', 'muse-spark-1.3', 'muse-spark-1.2']
MODEL_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
MAX_CATALOG_BYTES = 1 << 20
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def catalog_dir():
    base = os.environ.get('XDG_DATA_HOME') or ''
    root = Path(base) if base and os.path.isabs(base) else Path.home() / '.local' / 'share'
    return root / 'muse' / 'model-catalog'


def muse_models():
    """Visible model ids from Muse's own catalog (read-only), newest first; fallback if unreadable."""
    found = {}
    try:
        names = sorted(os.listdir(catalog_dir()))
    except OSError:
        return list(FALLBACK_MODELS)
    for name in names:
        if not name.endswith('.json'):
            continue
        try:
            fd = os.open(catalog_dir() / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    continue
                raw = handle.read(MAX_CATALOG_BYTES + 1)
            if len(raw) > MAX_CATALOG_BYTES:
                continue
            rows = json.loads(raw.decode('utf-8')).get('rows')
        except (OSError, ValueError, AttributeError, RecursionError):
            continue
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or row.get('visibility') != 'visible':
                continue
            model, date = row.get('model_id'), row.get('release_date')
            if isinstance(model, str) and MODEL_ID.fullmatch(model):
                found[model] = max(found.get(model, ''), date if isinstance(date, str) and DATE.fullmatch(date) else '')
    return sorted(found, key=lambda m: (found[m], m), reverse=True) or list(FALLBACK_MODELS)


COMPONENTS = ('memory', 'superpowers', 'local-tools', 'team')


def inspect_component(component, receipt=None):
    """Static state: never reads or writes Muse's configuration."""
    return {'id': component, 'installed': False, 'enabled': False, 'verified': False, 'version': '',
            'detail': UNSUPPORTED, 'unsupported': True}


def apply_component(component, binary, action, receipt=None):
    if action == 'disable':
        return {'id': component, 'version': '', 'digest': '', 'unsupported': True}  # nothing was ever written
    raise native_plugins.PluginError(UNSUPPORTED)
