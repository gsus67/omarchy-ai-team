"""Test-only helpers: keep every state write of a fixture inside its own temporary directory."""
import os
from pathlib import Path
from unittest.mock import patch

import atomic_files


def _inside(root, path):
    return Path(os.path.abspath(path)).is_relative_to(root)


def guard_writes(case, root):
    """Fail with AssertionError before any mkdir/atomic_write whose destination leaves `root`."""
    root = Path(os.path.abspath(root))
    real_mkdir, real_write = Path.mkdir, atomic_files.atomic_write

    def mkdir(self, *args, **kwargs):
        if not _inside(root, self):  # not `assert`: python -O would drop the guard
            raise AssertionError(f'mkdir outside the fixture: {self}')
        return real_mkdir(self, *args, **kwargs)

    def write(destination, content):
        if not _inside(root, destination):
            raise AssertionError(f'write outside the fixture: {destination}')
        return real_write(destination, content)

    for patcher in (patch.object(Path, 'mkdir', mkdir), patch.object(atomic_files, 'atomic_write', write)):
        patcher.start()
        case.addCleanup(patcher.stop)


def isolate_env(case, root):
    """HOME and every XDG base directory under `root`; restored by cleanup."""
    root = Path(root)
    patcher = patch.dict(os.environ, {
        'HOME': str(root / 'home'), 'XDG_STATE_HOME': str(root / 'state'),
        'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_DATA_HOME': str(root / 'data'),
        'XDG_CACHE_HOME': str(root / 'cache')})
    patcher.start()
    case.addCleanup(patcher.stop)
