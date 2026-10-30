"""Atomically replace a managed text file with private permissions."""

from pathlib import Path
import tempfile


def atomic_write(destination, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=destination.parent,
                                         prefix='.ai-team-', delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            temporary.chmod(0o600)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
