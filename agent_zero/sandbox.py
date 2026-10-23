from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path


class Sandbox:
    """File-tool boundary; not OS isolation. No shell or arbitrary code runner."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.mounts: dict[str, tuple[Path, str]] = {}

    def mount(self, host_path: str, mount_as: str, mode: str):
        host = Path(host_path).expanduser().resolve(strict=True)
        if not host.is_dir() or mode not in ("ro", "rw"):
            raise ValueError("Mount requires a directory and ro/rw mode")
        if not mount_as.isidentifier() or mount_as in ("sandbox",):
            raise ValueError("Mount alias must be a simple identifier")
        self.mounts[mount_as] = (host, mode)

    def _target(self, name: str, write=False):
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise PermissionError("Path must be relative and cannot traverse parents")
        base, mode = self.root, "rw"
        if path.parts[0] == "mounts":
            if len(path.parts) < 3 or path.parts[1] not in self.mounts:
                raise PermissionError("Unknown mount")
            base, mode = self.mounts[path.parts[1]]
            path = Path(*path.parts[2:])
        if write and mode != "rw":
            raise PermissionError("Mount is read-only")
        # Reject every symlink component, not just the final file.
        current = base
        for part in path.parts:
            current /= part
            if current.is_symlink():
                raise PermissionError("Symlinks are not permitted")
        target = current.resolve()
        if not target.is_relative_to(base):
            raise PermissionError("Path escapes sandbox")
        return target

    def read(self, name):
        target = self._target(name)
        with self._parent_fd(name, target) as (parent_fd, leaf):
            descriptor = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        with os.fdopen(descriptor, "r") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise PermissionError("Only regular files are supported")
            if info.st_size > 1_000_000:
                raise ValueError("File exceeds 1MB tool limit")
            return handle.read(1_000_001)

    def write(self, name, content):
        if len(content.encode()) > 1_000_000:
            raise ValueError("File exceeds 1MB tool limit")
        target = self._target(name, write=True)
        # Parent directories must exist; no arbitrary mkdir traversal.
        with self._parent_fd(name, target) as (parent_fd, leaf):
            descriptor = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent_fd)
        with os.fdopen(descriptor, "w") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise PermissionError("Only regular files are supported")
            os.ftruncate(handle.fileno(), 0)
            handle.write(content)

    @contextmanager
    def _parent_fd(self, name, target):
        # Walk using directory descriptors: a symlink swap in a parent cannot
        # redirect the eventual open outside the selected workspace/mount.
        path = Path(name)
        base = self.root
        if path.parts[0] == "mounts":
            base = self.mounts[path.parts[1]][0]
            path = Path(*path.parts[2:])
        descriptor = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in path.parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield descriptor, path.name
        finally:
            os.close(descriptor)
