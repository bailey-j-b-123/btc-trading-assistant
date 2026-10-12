"""Run trusted repository tests with explicit offline/state guards.

This is a regression guard, not a sandbox for hostile native code. In CI,
run it inside an OS network namespace on a disposable GitHub-hosted runner.
Never run this against an operator's live checkout.
"""

import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import unquote, urlsplit


def install_guard(root: Path, scratch: Path) -> None:
    roots = (root.resolve(), scratch.resolve())

    def allowed_path(value):
        path = Path(os.fsdecode(value)).resolve()
        if path == Path(os.devnull):
            return True
        return any(path.is_relative_to(parent) for parent in roots)

    def audit(event, args):
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo",
                     "socket.gethostbyname", "socket.gethostbyaddr",
                     "subprocess.Popen", "os.system", "os.kill", "os.killpg"}:
            raise PermissionError(f"Offline test guard denied {event}")
        if event == "sqlite3.connect":
            database = os.fsdecode(args[0])
            if database == ":memory:":
                return
            if database.startswith("file:"):
                uri = urlsplit(database)
                if "mode=memory" in uri.query:
                    return
                database = unquote(uri.path)
            if not allowed_path(database):
                raise PermissionError("SQLite path outside isolated test workspace")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            name = Path(os.fsdecode(args[0])).name
            if name == ".env" or name.startswith(".env.") and name != ".env.example":
                raise PermissionError("Environment files are not available to tests")
            mode = args[1]
            flags = args[2]
            writing = isinstance(mode, str) and any(c in mode for c in "wax+")
            writing = writing or isinstance(flags, int) and bool(
                flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
            )
            if writing and not allowed_path(args[0]):
                raise PermissionError("Write outside isolated test workspace")

    sys.addaudithook(audit)


def main():
    root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != root:
        raise SystemExit("Run from the fresh source-only checkout root")
    if (root / ".env").exists():
        raise SystemExit("Refusing a checkout containing .env")
    with tempfile.TemporaryDirectory(prefix="brain-tests-") as temporary:
        scratch = Path(temporary)
        # Do not pass inherited database settings or credentials to tests.
        os.environ.clear()
        os.environ.update({"HOME": str(scratch), "TMPDIR": str(scratch),
                           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                           "PYTHONDONTWRITEBYTECODE": "1"})
        tempfile.tempdir = str(scratch)
        install_guard(root, scratch)
        sys.path.insert(0, str(root))
        import pytest
        return pytest.main(["--basetemp", str(scratch / "pytest"), *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
