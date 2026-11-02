"""Fingerprint a directory tree, to prove an operation wrote nothing."""


def snapshot(root):
    state = {}
    for path in sorted(root.rglob("*")):
        name = str(path.relative_to(root))
        if path.is_symlink():
            state[name] = ("link", str(path.readlink()))
        elif path.is_file():
            stat = path.stat()
            state[name] = (path.read_bytes(), stat.st_mtime_ns, stat.st_ino)
    return state
