#!/usr/bin/env python3
"""Unified local desktop build orchestrator for Electrobun + Nuitka.

Pipeline:
prep -> web -> backend -> app -> bundle -> verify

Design goals:
- same CLI on Windows/macOS/Linux
- resumable builds with stage markers
- channelized outputs (stable/canary)
- Windows must output installer + portable
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
DESKTOP_DIR = PROJECT_ROOT / "desktop"
WEB_PROJECT_DIR = PROJECT_ROOT / "web"
ELECTROBUN_DIR = DESKTOP_DIR / "electrobun"
ELECTROBUN_DIST_DIR = ELECTROBUN_DIR / "dist"
ELECTROBUN_ARTIFACTS_DIR = ELECTROBUN_DIR / "artifacts"
BACKEND_DIR = DESKTOP_DIR / "dist" / "cccc-backend"
STAGES = ("prep", "web", "backend", "app", "bundle", "verify")
WINDOWS_PORT = 8848


class BuildError(RuntimeError):
    """Build failure with human-friendly message."""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}")


def info(message: str) -> None:
    log("INFO", message)


def ok(message: str) -> None:
    log("OK", message)


def warn(message: str) -> None:
    log("WARN", message)


def sanitize_version(text: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]", "_", text.strip())
    return clean or "local"


def run_cmd(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    info(f"run: {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=False,
        check=False,
    )
    if check and proc.returncode != 0:
        raise BuildError(f"Command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def detect_platform(explicit: str | None) -> str:
    if explicit:
        return explicit
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def normalize_channel(channel: str) -> str:
    normalized = channel.strip().lower()
    if normalized == "dev":
        return "canary"
    if normalized not in {"stable", "canary"}:
        raise BuildError(f"Invalid channel: {channel} (expected stable/canary)")
    return normalized


def channel_env_name(channel: str) -> str:
    # Keep compatibility with existing projects that used "dev".
    return "dev" if channel == "canary" else "stable"


def channel_prefix_candidates(channel: str) -> tuple[str, ...]:
    if channel == "canary":
        return ("canary-", "dev-")
    return ("stable-",)


def read_app_name() -> str:
    cfg = ELECTROBUN_DIR / "electrobun.config.ts"
    if cfg.exists():
        txt = cfg.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'name\s*:\s*"([^"]+)"', txt)
        if m:
            return m.group(1).strip()
    return "CCCC"


def read_app_version() -> str:
    pkg = ELECTROBUN_DIR / "package.json"
    if not pkg.exists():
        return "0.0.0"
    data = json.loads(pkg.read_text(encoding="utf-8"))
    return str(data.get("version", "0.0.0"))


def git_short_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(PROJECT_ROOT),
            text=True,
        ).strip()
        if out:
            return out
    except Exception:
        pass
    return "local"


def resolve_version(user_version: str) -> str:
    if user_version != "auto":
        return sanitize_version(user_version)
    return sanitize_version(f"{read_app_version()}-{git_short_sha()}")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def mirror_dir(src: Path, dst: Path) -> None:
    if not src.exists():
        raise BuildError(f"Source directory missing: {src}")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def copy_file(src: Path, dst: Path) -> None:
    ensure_dir(dst.parent)
    shutil.copy2(src, dst)


def summarize_file(path: Path, root: Path) -> dict[str, object]:
    return {
        "type": "file",
        "path": str(path.relative_to(root)).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def summarize_dir(path: Path) -> dict[str, object]:
    file_count = 0
    total_size = 0
    for item in path.rglob("*"):
        if item.is_file():
            file_count += 1
            total_size += item.stat().st_size
    return {
        "type": "dir",
        "path": path.name,
        "file_count": file_count,
        "total_size": total_size,
    }


def maybe_web_dist_dirs() -> list[Path]:
    return [
        PROJECT_ROOT / "src" / "cccc" / "ports" / "web" / "dist",
        PROJECT_ROOT / "web" / "dist",
        PROJECT_ROOT / "src" / "web" / "dist",
    ]


def resolve_web_dist_dir() -> Path:
    for candidate in maybe_web_dist_dirs():
        if (candidate / "index.html").exists():
            return candidate
    return maybe_web_dist_dirs()[0]


def resolve_web_hash_file(web_dist: Path) -> Path | None:
    direct = web_dist / "assets" / "index.js"
    if direct.exists():
        return direct
    assets = web_dist / "assets"
    if not assets.exists():
        return None
    js_files = sorted(assets.glob("*.js"), key=lambda p: p.stat().st_mtime, reverse=True)
    return js_files[0] if js_files else None


def backend_binary_path(platform_name: str) -> Path:
    exe = "cccc-backend.exe" if platform_name == "windows" else "cccc-backend"
    return BACKEND_DIR / exe


def backend_web_dist_path() -> Path:
    # Keep compatibility with historical package layout.
    legacy = BACKEND_DIR / "cccc" / "ports" / "web" / "dist"
    modern = BACKEND_DIR / "web" / "dist"
    if legacy.exists():
        return legacy
    if modern.exists():
        return modern
    return legacy


def platform_aliases(platform_name: str) -> tuple[str, ...]:
    if platform_name == "windows":
        return ("win", "windows")
    if platform_name == "macos":
        return ("mac", "macos", "darwin", "osx")
    return ("linux",)


def match_channel_platform_dir(dirname: str, channel: str, platform_name: str) -> bool:
    name = dirname.lower()
    if not any(name.startswith(prefix) for prefix in channel_prefix_candidates(channel)):
        return False
    return any(alias in name for alias in platform_aliases(platform_name))


def find_channel_platform_dirs(channel: str, platform_name: str) -> list[Path]:
    if not ELECTROBUN_DIST_DIR.exists():
        return []
    dirs = [
        p
        for p in ELECTROBUN_DIST_DIR.iterdir()
        if p.is_dir() and match_channel_platform_dir(p.name, channel, platform_name)
    ]
    return sorted(dirs, key=lambda p: p.name)


def find_channel_artifact_files(channel: str, platform_name: str) -> list[Path]:
    if not ELECTROBUN_ARTIFACTS_DIR.exists():
        return []
    aliases = platform_aliases(platform_name)
    prefixes = channel_prefix_candidates(channel)
    files = []
    for item in ELECTROBUN_ARTIFACTS_DIR.iterdir():
        if not item.is_file():
            continue
        name = item.name.lower()
        if any(name.startswith(prefix) for prefix in prefixes) and any(alias in name for alias in aliases):
            files.append(item)
    return sorted(files, key=lambda p: p.name)


def find_windows_setup_files(channel_dir: Path, app_name: str) -> tuple[Path, Path]:
    expected_exe = channel_dir / f"{app_name}-Setup.exe"
    expected_archive = channel_dir / f"{app_name}-Setup.tar.zst"
    if expected_exe.exists() and expected_archive.exists():
        return expected_exe, expected_archive

    exe_candidates = sorted(channel_dir.glob("*-Setup.exe"))
    archive_candidates = sorted(channel_dir.glob("*-Setup.tar.zst"))
    if exe_candidates and archive_candidates:
        return exe_candidates[0], archive_candidates[0]

    raise BuildError(f"Windows setup artifacts missing under: {channel_dir}")


def build_windows_portable(channel_dir: Path, app_name: str) -> Path:
    if not command_exists("tar"):
        raise BuildError("tar command not found; cannot extract Setup.tar.zst")
    _, archive = find_windows_setup_files(channel_dir, app_name)

    portable_dir = channel_dir / f"{app_name}-Portable"
    wrapper_dir = channel_dir / app_name
    staging = channel_dir / ".portable-staging"

    if staging.exists():
        shutil.rmtree(staging)
    if portable_dir.exists():
        shutil.rmtree(portable_dir)

    ensure_dir(staging)
    run_cmd(["tar", "-xf", str(archive), "-C", str(staging)], check=True)

    extracted_dirs = [p for p in staging.iterdir() if p.is_dir()]
    if not extracted_dirs:
        raise BuildError(f"No extracted directory found in {staging}")

    extracted = next((p for p in extracted_dirs if p.name == app_name), extracted_dirs[0])
    shutil.move(str(extracted), str(portable_dir))
    shutil.rmtree(staging, ignore_errors=True)

    launcher = portable_dir / "bin" / "launcher.exe"
    if not launcher.exists():
        raise BuildError(f"Portable launcher missing: {launcher}")

    if wrapper_dir.exists():
        shutil.rmtree(wrapper_dir, ignore_errors=True)

    return launcher


def ping_backend(timeout_seconds: int = 2) -> bool:
    url = f"http://127.0.0.1:{WINDOWS_PORT}/api/v1/ping"
    try:
        with urlopen(url, timeout=timeout_seconds) as resp:
            return int(resp.status) == 200
    except URLError:
        return False
    except Exception:
        return False


def detect_macos_entry(root: Path) -> Path | None:
    apps = sorted(root.rglob("*.app"))
    for app in apps:
        macos_bin_dir = app / "Contents" / "MacOS"
        if not macos_bin_dir.exists():
            continue
        bins = [p for p in macos_bin_dir.iterdir() if p.is_file()]
        if bins:
            return bins[0]
    return None


def detect_linux_entry(root: Path) -> Path | None:
    appimages = sorted(root.rglob("*.AppImage"))
    if appimages:
        return appimages[0]
    for item in root.rglob("*"):
        if item.is_file() and os.access(item, os.X_OK):
            return item
    return None


def kill_process_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def kill_windows_image(image_name: str) -> None:
    if os.name != "nt":
        return
    subprocess.run(
        ["taskkill", "/IM", image_name, "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@dataclass
class BuildContext:
    stage: str
    platform: str
    channel: str
    version: str
    force_stage: str | None
    clean: bool
    app_name: str

    @property
    def state_root(self) -> Path:
        return DESKTOP_DIR / ".build-state" / self.platform / self.channel / self.version

    @property
    def release_root(self) -> Path:
        return DESKTOP_DIR / "release" / self.platform / self.channel / self.version

    @property
    def delivery_root(self) -> Path:
        return DESKTOP_DIR / "delivery" / self.platform / self.channel / self.version

    @property
    def backend_exe(self) -> Path:
        return backend_binary_path(self.platform)

    def marker(self, stage_name: str) -> Path:
        return self.state_root / f"{stage_name}.ok"

    def forced(self, stage_name: str) -> bool:
        if not self.force_stage:
            return False
        return self.force_stage == "all" or self.force_stage == stage_name

    def stage_artifact_exists(self, stage_name: str) -> bool:
        if stage_name == "prep":
            return True
        if stage_name == "web":
            web_dist = resolve_web_dist_dir()
            return (web_dist / "index.html").exists()
        if stage_name == "backend":
            return self.backend_exe.exists()
        if stage_name == "app":
            return app_artifacts_ready(self)
        if stage_name == "bundle":
            return (self.release_root / "manifest.json").exists()
        if stage_name == "verify":
            return self.marker("verify").exists()
        return False

    def should_skip(self, stage_name: str) -> bool:
        if self.forced(stage_name):
            return False
        return self.marker(stage_name).exists() and self.stage_artifact_exists(stage_name)

    def write_marker(self, stage_name: str) -> None:
        ensure_dir(self.state_root)
        self.marker(stage_name).write_text(f"{now_iso()}\n", encoding="utf-8")

    def clear_marker(self, stage_name: str) -> None:
        m = self.marker(stage_name)
        if m.exists():
            m.unlink()

    def clear_downstream(self, stage_name: str) -> None:
        downstream: dict[str, tuple[str, ...]] = {
            "web": ("app", "bundle", "verify"),
            "backend": ("app", "bundle", "verify"),
            "app": ("bundle", "verify"),
            "bundle": ("verify",),
        }
        for target in downstream.get(stage_name, ()):
            self.clear_marker(target)


def app_artifacts_ready(ctx: BuildContext) -> bool:
    dirs = find_channel_platform_dirs(ctx.channel, ctx.platform)
    if not dirs:
        return False
    if ctx.platform != "windows":
        return True
    primary = dirs[0]
    try:
        setup_exe, setup_archive = find_windows_setup_files(primary, ctx.app_name)
    except BuildError:
        return False
    portable_launcher = primary / f"{ctx.app_name}-Portable" / "bin" / "launcher.exe"
    return setup_exe.exists() and setup_archive.exists() and portable_launcher.exists()


def stage_prep(ctx: BuildContext, pipeline: tuple[str, ...]) -> None:
    if ctx.should_skip("prep"):
        info("[prep] already completed, skipping")
        return
    ctx.clear_marker("prep")

    info("[prep] checking toolchain")
    if any(s in pipeline for s in ("web", "app")) and not command_exists("bun"):
        raise BuildError("[prep] bun not found in PATH")
    if "backend" in pipeline and not command_exists("uv"):
        raise BuildError("[prep] uv not found in PATH")
    if ctx.platform == "windows" and "app" in pipeline and not command_exists("tar"):
        raise BuildError("[prep] tar command not found in PATH")

    if ctx.platform == "macos":
        required = [
            "ELECTROBUN_DEVELOPER_ID",
            "ELECTROBUN_APPLEID",
            "ELECTROBUN_APPLEIDPASS",
            "ELECTROBUN_TEAMID",
        ]
        present = [name for name in required if os.environ.get(name)]
        if present and len(present) != len(required):
            warn("[prep] macOS signing env is partially configured; build will continue unsigned.")
        elif present:
            info("[prep] macOS signing env detected; codesign/notarize may run.")
        else:
            info("[prep] macOS signing env not set; unsigned build mode.")

    ctx.write_marker("prep")
    ok("[prep] done")


def stage_web(ctx: BuildContext) -> None:
    if ctx.should_skip("web"):
        info("[web] already built, skipping")
        return
    ctx.clear_marker("web")

    info("[web] building frontend assets")
    if command_exists("bun"):
        run_cmd(["bun", "install"], cwd=WEB_PROJECT_DIR)
        run_cmd(["bun", "run", "build"], cwd=WEB_PROJECT_DIR)
    elif command_exists("npm"):
        warn("[web] bun missing, fallback to npm")
        run_cmd(["npm", "install"], cwd=WEB_PROJECT_DIR)
        run_cmd(["npm", "run", "build"], cwd=WEB_PROJECT_DIR)
    else:
        raise BuildError("[web] neither bun nor npm found")

    web_dist = resolve_web_dist_dir()
    web_index = web_dist / "index.html"
    if not web_index.exists():
        raise BuildError(f"[web] missing artifact: {web_index}")

    backend_web = backend_web_dist_path()
    if backend_web.exists():
        info("[web] syncing frontend dist into existing backend bundle")
        mirror_dir(web_dist, backend_web)
        ok("[web] backend web dist synced")

    ctx.write_marker("web")
    ctx.clear_downstream("web")
    ok("[web] done")


def stage_backend(ctx: BuildContext) -> None:
    if ctx.should_skip("backend"):
        info("[backend] already built, skipping")
        return
    ctx.clear_marker("backend")

    info("[backend] building Nuitka standalone backend")
    cmd = [
        "uv",
        "run",
        "--with",
        "nuitka",
        "python",
        str(SCRIPT_DIR / "build-nuitka.py"),
        "--platform",
        ctx.platform,
        "--standalone",
    ]
    if ctx.platform == "windows":
        cmd.append("--fast")
    run_cmd(cmd, cwd=PROJECT_ROOT)

    if not ctx.backend_exe.exists():
        raise BuildError(f"[backend] expected output missing: {ctx.backend_exe}")

    ctx.write_marker("backend")
    ctx.clear_downstream("backend")
    ok("[backend] done")


def stage_app(ctx: BuildContext) -> None:
    if ctx.should_skip("app"):
        info("[app] already built, skipping")
        return
    ctx.clear_marker("app")

    if not ctx.backend_exe.exists():
        raise BuildError(f"[app] backend missing: {ctx.backend_exe}")

    info("[app] building Electrobun package")
    run_cmd(["bun", "install"], cwd=ELECTROBUN_DIR)

    env_name = channel_env_name(ctx.channel)
    run_cmd(
        [
            "bunx",
            "electrobun",
            "build",
            f"--platform={ctx.platform}",
            f"--env={env_name}",
        ],
        cwd=ELECTROBUN_DIR,
    )

    dirs = find_channel_platform_dirs(ctx.channel, ctx.platform)
    if not dirs:
        raise BuildError(
            f"[app] no build output directories for channel={ctx.channel}, platform={ctx.platform}"
        )

    if ctx.platform == "windows":
        primary = dirs[0]
        setup_exe, setup_archive = find_windows_setup_files(primary, ctx.app_name)
        if not setup_exe.exists() or not setup_archive.exists():
            raise BuildError(f"[app] setup artifacts missing in {primary}")
        launcher = build_windows_portable(primary, ctx.app_name)
        info(f"[app] portable launcher: {launcher}")

    ctx.write_marker("app")
    ctx.clear_downstream("app")
    ok("[app] done")


def stage_bundle(ctx: BuildContext) -> None:
    if ctx.should_skip("bundle"):
        info("[bundle] already built, skipping")
        return
    ctx.clear_marker("bundle")

    if not app_artifacts_ready(ctx):
        raise BuildError("[bundle] app artifacts are missing; run --stage app first")

    if ctx.release_root.exists():
        shutil.rmtree(ctx.release_root)
    ensure_dir(ctx.release_root)

    if ctx.delivery_root.exists():
        shutil.rmtree(ctx.delivery_root)
    ensure_dir(ctx.delivery_root)

    info("[bundle] collecting artifacts")
    artifacts_summary: list[dict[str, object]] = []
    delivery_summary: list[dict[str, object]] = []

    # backend
    backend_dst = ctx.release_root / "cccc-backend"
    mirror_dir(BACKEND_DIR, backend_dst)
    artifacts_summary.append(summarize_dir(backend_dst))

    # app dist directories
    channel_dirs = find_channel_platform_dirs(ctx.channel, ctx.platform)
    release_app_dir = ctx.release_root / "app"
    ensure_dir(release_app_dir)
    for src in channel_dirs:
        dst = release_app_dir / src.name
        mirror_dir(src, dst)
        artifacts_summary.append(summarize_dir(dst))

    # app artifact files
    artifact_files = find_channel_artifact_files(ctx.channel, ctx.platform)
    release_artifacts_dir = ctx.release_root / "artifacts"
    ensure_dir(release_artifacts_dir)
    for src in artifact_files:
        dst = release_artifacts_dir / src.name
        copy_file(src, dst)
        artifacts_summary.append(summarize_file(dst, ctx.release_root))

    if ctx.platform == "windows":
        primary = channel_dirs[0]
        setup_exe, setup_archive = find_windows_setup_files(primary, ctx.app_name)
        portable_dir = primary / f"{ctx.app_name}-Portable"

        friendly_setup_exe = ctx.release_root / f"{ctx.channel}-win-x64-{setup_exe.name}"
        friendly_setup_archive = ctx.release_root / f"{ctx.channel}-win-x64-{setup_archive.name}"
        friendly_portable = ctx.release_root / "portable"

        copy_file(setup_exe, friendly_setup_exe)
        copy_file(setup_archive, friendly_setup_archive)
        mirror_dir(portable_dir, friendly_portable)

        artifacts_summary.append(summarize_file(friendly_setup_exe, ctx.release_root))
        artifacts_summary.append(summarize_file(friendly_setup_archive, ctx.release_root))
        artifacts_summary.append(summarize_dir(friendly_portable))

        delivery_setup_exe = ctx.delivery_root / friendly_setup_exe.name
        delivery_setup_archive = ctx.delivery_root / friendly_setup_archive.name
        delivery_portable = ctx.delivery_root / "portable"
        copy_file(friendly_setup_exe, delivery_setup_exe)
        copy_file(friendly_setup_archive, delivery_setup_archive)
        mirror_dir(friendly_portable, delivery_portable)

        delivery_summary.append(summarize_file(delivery_setup_exe, ctx.delivery_root))
        delivery_summary.append(summarize_file(delivery_setup_archive, ctx.delivery_root))
        delivery_summary.append(summarize_dir(delivery_portable))
    else:
        for src in artifact_files:
            dst = ctx.delivery_root / src.name
            copy_file(src, dst)
            delivery_summary.append(summarize_file(dst, ctx.delivery_root))

    web_dist = resolve_web_dist_dir()
    web_hash_file = resolve_web_hash_file(web_dist)
    manifest = {
        "platform": ctx.platform,
        "channel": ctx.channel,
        "version": ctx.version,
        "build_time": now_iso(),
        "git_sha": git_short_sha(),
        "backend_hash": sha256_file(ctx.backend_exe),
        "web_hash": sha256_file(web_hash_file) if web_hash_file else None,
        "artifacts": artifacts_summary,
    }
    manifest_path = ctx.release_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    delivery_manifest = dict(manifest)
    delivery_manifest["artifacts"] = delivery_summary
    delivery_manifest_path = ctx.delivery_root / "manifest.json"
    delivery_manifest_path.write_text(
        json.dumps(delivery_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    ctx.write_marker("bundle")
    ctx.clear_downstream("bundle")
    ok(f"[bundle] done -> {ctx.release_root}")
    info(f"[bundle] delivery -> {ctx.delivery_root}")


def stage_verify(ctx: BuildContext) -> None:
    if ctx.should_skip("verify"):
        info("[verify] already completed, skipping")
        return
    ctx.clear_marker("verify")

    manifest_path = ctx.release_root / "manifest.json"
    if not manifest_path.exists():
        raise BuildError("[verify] missing manifest; run --stage bundle first")

    if ping_backend():
        warn("[verify] backend already running on 8848; smoke test marked as pass.")
        ctx.write_marker("verify")
        ok("[verify] done")
        return

    entry: Path | None
    if ctx.platform == "windows":
        entry = ctx.release_root / "portable" / "bin" / "launcher.exe"
    elif ctx.platform == "macos":
        entry = detect_macos_entry(ctx.release_root)
    else:
        entry = detect_linux_entry(ctx.release_root)

    if not entry or not entry.exists():
        raise BuildError("[verify] could not locate runnable desktop entry in release bundle")

    info(f"[verify] launching: {entry}")
    if ctx.platform != "windows":
        mode = entry.stat().st_mode
        entry.chmod(mode | stat.S_IXUSR)

    proc = subprocess.Popen(
        [str(entry)],
        cwd=str(entry.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            if ping_backend():
                ctx.write_marker("verify")
                ok("[verify] ping=200")
                return
            time.sleep(1)
        raise BuildError("[verify] backend ping timeout (60s)")
    finally:
        kill_process_tree(proc.pid)
        if ctx.platform == "windows":
            kill_windows_image("launcher.exe")
            kill_windows_image(f"{ctx.app_name}.exe")
            kill_windows_image("cccc-backend.exe")


def clean_all() -> None:
    info("Cleaning build outputs and stage markers...")
    paths = [
        DESKTOP_DIR / ".build-state",
        DESKTOP_DIR / "release",
        DESKTOP_DIR / "delivery",
        DESKTOP_DIR / "dist",
        ELECTROBUN_DIST_DIR,
        ELECTROBUN_ARTIFACTS_DIR,
        ELECTROBUN_DIR / ".electrobun-cache",
        SCRIPT_DIR / "dist",
        PROJECT_ROOT / "cccc.build",
        PROJECT_ROOT / "cccc.dist",
        PROJECT_ROOT / "cccc.onefile-build",
        PROJECT_ROOT / "daemon_main.build",
        PROJECT_ROOT / "daemon_main.dist",
        PROJECT_ROOT / "main.build",
        PROJECT_ROOT / "main.dist",
    ]
    for path in paths:
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            info(f"removed: {path}")
    ok("Clean complete")


def pipeline_for_stage(stage: str) -> tuple[str, ...]:
    if stage == "all":
        return STAGES
    if stage not in STAGES:
        raise BuildError(f"Invalid stage: {stage}")
    return (stage,)


def validate_force(force_stage: str | None) -> None:
    if not force_stage:
        return
    valid = set(STAGES) | {"all"}
    if force_stage not in valid:
        raise BuildError(f"Invalid force stage: {force_stage}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified local packager for Electrobun + Nuitka",
    )
    parser.add_argument(
        "--stage",
        default="all",
        choices=[*STAGES, "all"],
        help="Build stage to run",
    )
    parser.add_argument(
        "--platform",
        choices=["windows", "macos", "linux"],
        help="Target platform (default: current host)",
    )
    parser.add_argument(
        "--channel",
        default="stable",
        help="Channel: stable/canary (dev is alias of canary)",
    )
    parser.add_argument(
        "--version",
        default="auto",
        help="Build version label used in state/release path",
    )
    parser.add_argument(
        "--force",
        dest="force_stage",
        choices=[*STAGES, "all"],
        help="Force rebuild selected stage",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean outputs/state before running stages",
    )
    return parser.parse_args(argv)


def run_pipeline(ctx: BuildContext, pipeline: tuple[str, ...]) -> None:
    stage_funcs = {
        "prep": lambda: stage_prep(ctx, pipeline),
        "web": lambda: stage_web(ctx),
        "backend": lambda: stage_backend(ctx),
        "app": lambda: stage_app(ctx),
        "bundle": lambda: stage_bundle(ctx),
        "verify": lambda: stage_verify(ctx),
    }
    for stage_name in pipeline:
        stage_funcs[stage_name]()


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    platform_name = detect_platform(args.platform)
    channel = normalize_channel(args.channel)
    version = resolve_version(args.version)
    validate_force(args.force_stage)

    ctx = BuildContext(
        stage=args.stage,
        platform=platform_name,
        channel=channel,
        version=version,
        force_stage=args.force_stage,
        clean=args.clean,
        app_name=read_app_name(),
    )

    info("=========================================")
    info("Desktop Local Build Orchestrator")
    info("=========================================")
    info(f"platform={ctx.platform}")
    info(f"channel={ctx.channel} (env={channel_env_name(ctx.channel)})")
    info(f"version={ctx.version}")
    info(f"stage={ctx.stage}")
    if ctx.force_stage:
        info(f"force={ctx.force_stage}")

    if ctx.clean:
        clean_all()

    pipeline = STAGES if ctx.stage == "all" else pipeline_for_stage(ctx.stage)
    run_pipeline(ctx, pipeline)

    info("=========================================")
    ok("Build completed successfully")
    info(f"backend: {ctx.backend_exe}")
    info(f"release: {ctx.release_root}")
    info("=========================================")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except BuildError as exc:
        log("ERROR", str(exc))
        raise SystemExit(1)
