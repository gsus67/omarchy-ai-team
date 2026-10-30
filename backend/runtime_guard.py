"""Short-lived maintenance locks; native clients keep a shared lease while alive."""

from i18n import tr

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat

import native_plugins


class BusyError(native_plugins.PluginError):
    pass


@contextmanager
def lease(path, *, exclusive=False, inherit=False, wait=False):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise BusyError(tr('La ruta de mantenimiento no es segura; se conserva sin modificar.'))
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise BusyError(tr('El bloqueo de mantenimiento no es un archivo regular; se conserva.'))
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as error:
            raise BusyError(tr('Hay una sesión o actualización activa. Espera a que termine y reintenta; no se cerrará ninguna sesión.')) from error
        if inherit:
            os.set_inheritable(fd, True)
        yield
    finally:
        os.close(fd)


def memory_lock(store):
    return Path(store).parent / 'state/ecc-memory-maintenance.lock'


def assert_idle(kind, proc=Path('/proc')):
    """Conservatively include older/native clients. Never read environ or log argv."""
    if kind not in ('memory', 'pi'):
        raise BusyError(tr('Tipo de cliente no admitido para mantenimiento.'))
    for process in proc.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            arguments = (process / 'cmdline').read_bytes().split(b'\0')
            executable = Path(os.fsdecode(arguments[0])).name if arguments and arguments[0] else ''
            if kind == 'memory':
                active = any(Path(os.fsdecode(a)).name == 'memory-mcp.mjs' for a in arguments)
            else:
                active = executable == 'pi' or any(Path(os.fsdecode(a)).name == 'pi' or 'pi-coding-agent' in Path(os.fsdecode(a)).parts for a in arguments[:4])
            if active:
                raise BusyError(tr('Hay clientes de memoria activos.') if kind == 'memory' else tr('Hay una sesión Pi activa. Finalízala desde sus controles nativos antes de actualizar su paquete Superpowers.'))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as error:
            raise BusyError(tr('No se pudo comprobar un proceso del usuario; no se actualizará sin comprobar las sesiones.')) from error
