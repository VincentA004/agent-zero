"""One-request file worker, launched with a macOS deny-by-default OS policy."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys


class FileWorker:
    def __init__(self, sandbox):
        self.sandbox = sandbox
        self.isolated = sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists()

    def call(self, operation, path, content=""):
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValueError("Tool path and content must be strings")
        request = {"operation": operation, "path": path, "content": content,
                   "root": str(self.sandbox.root),
                   "mounts": {k: [str(p), mode] for k, (p, mode) in self.sandbox.mounts.items()}}
        script = str(Path(__file__).resolve())
        command = [sys.executable, "-I", script]
        if self.isolated:
            # Python and module imports are readable; only selected tool roots writable.
            # Denies network, child processes, credentials and arbitrary home reads.
            reads = {str(Path(sys.base_prefix).resolve()), str(Path(sys.prefix).resolve()),
                     str(Path(script).parent), "/System", "/usr/lib", "/usr/share", "/dev/urandom", "/dev/null"}
            roots = [(self.sandbox.root, "rw"), *self.sandbox.mounts.values()]
            policy = ['(version 1)', '(deny default)', '(allow process-exec (literal ' + json.dumps(str(Path(sys.executable).resolve())) + '))',
                      '(allow process-fork)', '(allow process-exec (subpath ' + json.dumps(str(Path(sys.base_prefix).resolve())) + '))', '(allow sysctl-read)', '(allow mach-lookup)', '(allow file-read-metadata)',
                      '(allow file-read* (literal "/"))']
            for item in reads:
                policy.append('(allow file-read* (subpath ' + json.dumps(item) + '))')
            for root, mode in roots:
                policy.append('(allow file-read* (subpath ' + json.dumps(str(root)) + '))')
                if mode == "rw":
                    policy.append('(allow file-write* (subpath ' + json.dumps(str(root)) + '))')
            command = ["/usr/bin/sandbox-exec", "-p", "\n".join(policy), *command]
        result = subprocess.run(command, input=json.dumps(request), text=True, capture_output=True,
                                timeout=8, cwd=self.sandbox.root, env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        if result.returncode:
            raise RuntimeError("File worker failed or was denied by the OS sandbox")
        response = json.loads(result.stdout)
        if "error" in response:
            raise PermissionError(response["error"])
        return response["result"]


def main():
    # Import only our stdlib-only adapter; -I excludes the user site and cwd.
    import importlib.util
    spec = importlib.util.spec_from_file_location("file_boundary", Path(__file__).with_name("sandbox.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        request = json.load(sys.stdin)
        boundary = module.Sandbox(Path(request["root"]))
        boundary.mounts = {k: (Path(p), mode) for k, (p, mode) in request["mounts"].items()}
        operation, path = request["operation"], request["path"]
        if operation == "read":
            value = boundary.read(path)
        elif operation == "write":
            boundary.write(path, request["content"])
            value = {"written": path}
        elif operation == "list":
            # Reuse descriptor walking, including symlink rejection, for directories.
            name = "./" if path in ("", ".") else path
            mount_parts = Path(name).parts
            mount_root = (boundary.mounts.get(mount_parts[1], (None, None))[0]
                          if len(mount_parts) == 2 and mount_parts[0] == "mounts" else None)
            target = mount_root or (boundary.root if name == "./" else boundary._target(name))
            if target == boundary.root or mount_root:
                descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            else:
                with boundary._parent_fd(name, target) as (parent, leaf):
                    descriptor = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                value = sorted(os.listdir(descriptor))[:200]
            finally:
                os.close(descriptor)
        else:
            raise ValueError("Unknown file operation")
        print(json.dumps({"result": value}))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"error": str(exc)[:300]}))


if __name__ == "__main__":
    main()
