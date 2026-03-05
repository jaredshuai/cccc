"""Desktop backend entrypoint for Nuitka builds.

This wrapper provides a desktop-friendly default entry:
1. Start daemon in a child process (same executable, special arg)
2. Start web server in foreground
3. Shutdown daemon on exit

It also keeps normal CLI behavior for explicit commands.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from cccc.cli.main import main as cli_main
from cccc.daemon.server import call_daemon
from cccc.daemon_main import main as daemon_main
from cccc.paths import ensure_home


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _spawn_daemon_process() -> subprocess.Popen[bytes]:
    home = ensure_home()
    daemon_dir = home / "daemon"
    daemon_dir.mkdir(parents=True, exist_ok=True)
    log_path = daemon_dir / "ccccd.log"

    exe_path = str(Path(sys.argv[0]).resolve())
    env = os.environ.copy()
    env["CCCC_HOME"] = str(home)

    log_file = log_path.open("a", encoding="utf-8")
    try:
        return subprocess.Popen(
            [exe_path, "--desktop-daemon-run"],
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            env=env,
            cwd=str(home),
            start_new_session=True,
        )
    finally:
        log_file.close()


def _wait_daemon_ready(timeout_s: float = 8.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if call_daemon({"op": "ping"}, timeout_s=0.5).get("ok"):
            return True
        time.sleep(0.1)
    return False


def _shutdown_daemon() -> None:
    try:
        call_daemon({"op": "shutdown"}, timeout_s=2.0)
    except Exception:
        pass


def _desktop_default_entry() -> int:
    daemon_proc: subprocess.Popen[bytes] | None = None

    if not call_daemon({"op": "ping"}, timeout_s=0.8).get("ok"):
        print("[cccc] Starting daemon...", file=sys.stderr)
        try:
            daemon_proc = _spawn_daemon_process()
        except Exception as exc:
            print(f"[cccc] Failed to start daemon: {exc}", file=sys.stderr)
            return 1

        if not _wait_daemon_ready():
            print("[cccc] Daemon failed to start in time", file=sys.stderr)
            if daemon_proc.poll() is None:
                daemon_proc.kill()
            return 1
        print("[cccc] Daemon started", file=sys.stderr)

    host = str(os.environ.get("CCCC_WEB_HOST") or "").strip() or "0.0.0.0"
    port = str(os.environ.get("CCCC_WEB_PORT") or "8848").strip() or "8848"
    log_level = str(os.environ.get("CCCC_WEB_LOG_LEVEL") or "").strip() or "info"

    web_argv = ["web", "--host", host, "--port", port, "--log-level", log_level]
    if _env_flag("CCCC_WEB_RELOAD", default=False):
        web_argv.append("--reload")

    try:
        return int(cli_main(web_argv))
    finally:
        _shutdown_daemon()
        if daemon_proc is not None and daemon_proc.poll() is None:
            try:
                daemon_proc.terminate()
                daemon_proc.wait(timeout=3.0)
            except Exception:
                try:
                    daemon_proc.kill()
                except Exception:
                    pass


def main() -> int:
    argv = sys.argv[1:]

    # Internal daemon worker mode for compiled desktop backend.
    if len(argv) == 1 and argv[0] == "--desktop-daemon-run":
        return int(daemon_main(["run"]))

    # Compatibility for legacy "-m cccc.daemon_main ..." call patterns.
    if len(argv) >= 2 and argv[0] == "-m" and argv[1] == "cccc.daemon_main":
        return int(daemon_main(argv[2:]))

    # No args means desktop default entry (daemon + web).
    if not argv:
        return int(_desktop_default_entry())

    # Explicit CLI commands keep original behavior.
    return int(cli_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
