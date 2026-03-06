#!/usr/bin/env python3
"""
CCCC Backend Builder using Nuitka (optimized for resume-friendly Windows builds).

Defaults on Windows:
1. Fast mode (ccache) enabled
2. Standalone mode enabled (better startup performance than onefile)
3. Output normalized to: desktop/dist/cccc-backend/cccc-backend.exe
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
DESKTOP_DIR = PROJECT_ROOT / "desktop"
OUTPUT_DIR = DESKTOP_DIR / "dist"
NORMALIZED_BACKEND_DIR = OUTPUT_DIR / "cccc-backend"
BACKEND_WRAPPER_ENTRY = Path(__file__).parent / "backend_entry.py"
CACHE_DIR = Path(__file__).parent / ".nuitka-cache"
LEGACY_OUTPUT_DIR = Path(__file__).parent / "dist"


def _windows_compiler_choice() -> str:
    compiler = os.environ.get("CCCC_NUITKA_WINDOWS_COMPILER", "mingw64").strip().lower()
    if compiler in {"", "auto"}:
        return "mingw64"
    if compiler not in {"mingw64", "msvc"}:
        raise ValueError(
            "CCCC_NUITKA_WINDOWS_COMPILER must be one of: mingw64, msvc, auto"
        )
    return compiler


def setup_cache_env() -> dict[str, str]:
    """Setup cache environment variables for faster recompilation."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["NUITKA_CACHE_DIR"] = str(CACHE_DIR)
    env["NUITKA_CACHE_DIR_BYTECODE"] = str(CACHE_DIR / "bytecode")
    env["NUITKA_CACHE_DIR_DOWNLOADS"] = str(CACHE_DIR / "downloads")

    (CACHE_DIR / "bytecode").mkdir(exist_ok=True)
    (CACHE_DIR / "downloads").mkdir(exist_ok=True)
    (CACHE_DIR / "ccache").mkdir(exist_ok=True)
    return env


def _target_platform(target_platform: str | None = None) -> str:
    plat = (target_platform or platform.system()).lower()
    if plat == "darwin":
        return "macos"
    return plat


def get_output_filename(target_platform: str | None = None) -> str:
    """Generate output filename based on platform."""
    plat = _target_platform(target_platform)
    if plat == "windows":
        return "cccc-backend.exe"
    if plat in ("macos", "linux"):
        return "cccc-backend"
    return f"cccc-backend-{plat}"


def clean_build() -> None:
    """Clean all build artifacts."""
    print("[Nuitka] Cleaning build artifacts...")
    dirs_to_clean = [
        OUTPUT_DIR,
        LEGACY_OUTPUT_DIR,
        PROJECT_ROOT / "cccc.build",
        PROJECT_ROOT / "cccc.dist",
        PROJECT_ROOT / "cccc.onefile-build",
        PROJECT_ROOT / "daemon_main.build",
        PROJECT_ROOT / "daemon_main.dist",
        PROJECT_ROOT / "main.build",
        PROJECT_ROOT / "main.dist",
    ]

    for d in dirs_to_clean:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            print(f"  Removed: {d}")

    print("[Nuitka] Clean complete!")


def _base_build_command(
    *,
    target_platform: str | None,
    onefile: bool,
    output_filename: str,
) -> list[str]:
    target = _target_platform(target_platform)
    current = _target_platform()
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        f"--output-filename={output_filename}",
        f"--output-dir={OUTPUT_DIR}",
        "--enable-plugin=anti-bloat",
        "--no-debug-c-warnings",
        f"--jobs={max(1, (os.cpu_count() or 2) - 1)}",
        "--include-package=cccc",
        "--include-package=uvicorn",
        "--include-package=fastapi",
        "--include-package=pydantic",
        "--include-package=httpx",
        "--include-package=yaml",
        "--include-package-data=cccc",
        "--python-flag=no_warnings,-O,no_docstrings",
        "--assume-yes-for-downloads",
    ]

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--standalone")

    if target == "windows":
        windows_compiler = _windows_compiler_choice()
        if windows_compiler == "msvc":
            cmd.append("--msvc=latest")
        else:
            cmd.append("--mingw64")
        cmd.append("--windows-console-mode=disable")

    if target == "macos":
        cmd.extend(
            [
                "--macos-create-app-bundle",
                "--macos-app-name=CCCC Backend",
            ]
        )

    cmd.append(str(BACKEND_WRAPPER_ENTRY))
    return cmd


def _candidate_dist_dirs() -> list[Path]:
    if not OUTPUT_DIR.exists():
        return []
    return [p for p in OUTPUT_DIR.glob("*.dist") if p.is_dir()]


def _normalize_standalone_output(
    *,
    output_filename: str,
    before_dist_dirs: set[str],
) -> Path:
    candidates = _candidate_dist_dirs()
    if not candidates:
        raise FileNotFoundError("No standalone dist directory produced by Nuitka.")

    scored: list[tuple[int, Path]] = []
    for item in candidates:
        score = 0
        if (item / output_filename).exists():
            score += 5
        if item.name.lower().startswith("cccc-backend"):
            score += 2
        if str(item.resolve()) not in before_dist_dirs:
            score += 1
        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    winner_score, winner = scored[0]
    if winner_score <= 0:
        raise FileNotFoundError("Cannot identify standalone output directory from Nuitka build.")

    if NORMALIZED_BACKEND_DIR.exists() and NORMALIZED_BACKEND_DIR.resolve() != winner.resolve():
        shutil.rmtree(NORMALIZED_BACKEND_DIR, ignore_errors=True)

    if winner.resolve() != NORMALIZED_BACKEND_DIR.resolve():
        if NORMALIZED_BACKEND_DIR.exists():
            shutil.rmtree(NORMALIZED_BACKEND_DIR, ignore_errors=True)
        shutil.move(str(winner), str(NORMALIZED_BACKEND_DIR))

    output_path = NORMALIZED_BACKEND_DIR / output_filename
    if not output_path.exists():
        raise FileNotFoundError(f"Standalone output missing executable: {output_path}")
    return output_path


def _run_nuitka_build(
    *,
    target_platform: str | None,
    onefile: bool,
    fast_mode: bool,
) -> Path:
    print(f"[Nuitka] Starting build for {target_platform or platform.system()}...")
    print(f"[Nuitka] Project root: {PROJECT_ROOT}")
    print(f"[Nuitka] Entry point: {BACKEND_WRAPPER_ENTRY}")
    print(f"[Nuitka] Cache dir: {CACHE_DIR}")
    print(f"[Nuitka] Mode: {'fast(ccache)' if fast_mode else 'normal'} + {'onefile' if onefile else 'standalone'}")
    if _target_platform(target_platform) == "windows":
        print(f"[Nuitka] Windows compiler: {_windows_compiler_choice()}")

    env = setup_cache_env()
    if fast_mode:
        ccache_dir = CACHE_DIR / "ccache"
        ccache_dir.mkdir(exist_ok=True)
        env["NUITKA_CACHE_DIR_CCACHE"] = str(ccache_dir)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_filename = get_output_filename(target_platform)
    before = {str(p.resolve()) for p in _candidate_dist_dirs()}
    cmd = _base_build_command(target_platform=target_platform, onefile=onefile, output_filename=output_filename)

    print(f"[Nuitka] Running command: {' '.join(cmd[:10])} ...")
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True, env=env)

    if onefile:
        output_path = OUTPUT_DIR / output_filename
        if not output_path.exists():
            raise FileNotFoundError(f"Onefile output not found: {output_path}")
        return output_path

    return _normalize_standalone_output(output_filename=output_filename, before_dist_dirs=before)


def _resolve_modes(args: argparse.Namespace) -> tuple[bool, bool]:
    target = _target_platform(args.platform)

    if args.onefile:
        onefile = True
    elif args.standalone:
        onefile = False
    else:
        # Windows default: standalone for better startup performance.
        onefile = target != "windows"

    if args.no_fast:
        fast_mode = False
    elif args.fast:
        fast_mode = True
    else:
        # Windows default: fast build mode.
        fast_mode = target == "windows"

    return onefile, fast_mode


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build CCCC desktop backend with Nuitka (resume-friendly defaults)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--platform",
        "-p",
        choices=["windows", "macos", "linux"],
        help="Target platform (default: current system)",
    )
    parser.add_argument(
        "--clean",
        "-c",
        action="store_true",
        help="Clean build artifacts before building",
    )
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Force standalone directory output",
    )
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Force onefile output",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Force fast mode with ccache",
    )
    parser.add_argument(
        "--no-fast",
        action="store_true",
        help="Disable fast mode even on Windows",
    )
    args = parser.parse_args()

    if args.clean:
        clean_build()
        if not args.platform:
            return

    onefile, fast_mode = _resolve_modes(args)
    try:
        output_path = _run_nuitka_build(
            target_platform=args.platform,
            onefile=onefile,
            fast_mode=fast_mode,
        )
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print("\n[Nuitka] Build successful!")
        print(f"[Nuitka] Output: {output_path}")
        print(f"[Nuitka] Size: {size_mb:.2f} MB")
        if not onefile:
            print(f"[Nuitka] Normalized backend dir: {NORMALIZED_BACKEND_DIR}")
    except subprocess.CalledProcessError as e:
        print(f"\n[Nuitka] Build failed with exit code {e.returncode}")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"\n[Nuitka] Build output error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
