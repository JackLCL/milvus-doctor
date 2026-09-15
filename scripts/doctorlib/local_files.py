"""One bounded regular-file reader shared by every local evidence input.

Never read process environments, devices, pipes, or a substituted leaf link.
On Linux the opened descriptor is checked before any contents are read, since
an ancestor may change after path resolution. Errors never expose input paths.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys


_SPECIAL_ROOTS = (Path("/proc"), Path("/sys"), Path("/dev"))


def _special(path):
    return any(path == root or root in path.parents for root in _SPECIAL_ROOTS)


def read_regular_file(path, max_bytes=1048576):
    """Return at most max_bytes from an explicitly selected regular file.

    Normal ancestor symlinks are allowed, but leaf symlinks are not. All callers
    share these boundaries, including JSON snapshots, saved reports and context.
    """
    if type(max_bytes) is not int or not 1 <= max_bytes <= 16 * 1024 * 1024:
        raise ValueError("max_bytes must be a positive integer at most 16777216")
    descriptor = None
    try:
        selected = Path(path).expanduser()
        if selected.is_symlink():
            raise ValueError("Selected input must not be a symlink")
        # Resolve ancestors only. Resolving the leaf would follow a symlink
        # substituted after is_symlink(), defeating the kernel's O_NOFOLLOW.
        selected = selected.parent.resolve(strict=True) / selected.name
        if _special(selected):
            raise ValueError("Special filesystem input is not supported")
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(str(selected), flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Selected input must be a regular file")
        if info.st_size > max_bytes:
            raise ValueError("Selected file exceeds max_bytes; provide a smaller reviewed file")
        if sys.platform.startswith("linux"):
            # Fail closed if the actual Linux descriptor cannot be resolved.
            # This check precedes os.read; no environment/device contents are
            # read even when an ancestor was replaced during open().
            actual = Path(os.readlink("/proc/self/fd/{}".format(descriptor)))
            if not actual.is_absolute() or _special(actual):
                raise ValueError("Special filesystem input is not supported")
            if actual != selected:
                raise ValueError("Selected input changed during opening; select the file again")
        chunks = []
        total = 0
        while total <= max_bytes:
            chunk = os.read(descriptor, min(65536, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Selected file exceeds max_bytes; provide a smaller reviewed file")
            chunks.append(chunk)
        return b"".join(chunks)
    except (OSError, TypeError, RuntimeError):
        raise ValueError("Unable to read the selected regular input file") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
