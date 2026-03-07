#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]


class InstallerError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a single-file Windows installer with Inno Setup")
    parser.add_argument("--delivery-parent", required=True, help="Parent directory containing versioned delivery folders")
    parser.add_argument("--app-name", required=True, help="Application name")
    parser.add_argument("--app-identifier", required=True, help="Stable app identifier used for AppId")
    parser.add_argument("--publisher", default="ChesterRa", help="Publisher name")
    return parser.parse_args()


def resolve_delivery_root(delivery_parent: Path) -> Path:
    manifests = sorted(delivery_parent.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not manifests:
        raise InstallerError(f"No delivery manifest found under {delivery_parent}")
    return manifests[0].parent


def find_iscc() -> Path:
    candidates = [
        shutil.which("iscc"),
        shutil.which("ISCC"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    raise InstallerError("ISCC.exe not found; install Inno Setup first")


def summarize_file(path: Path, root: Path) -> dict[str, object]:
    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "type": "file",
        "path": str(path.relative_to(root)).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": digest,
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


def build_script_text() -> str:
    return r'''
#ifndef AppName
  #define AppName "CCCC"
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppPublisher
  #define AppPublisher "ChesterRa"
#endif
#ifndef AppId
  #define AppId "{{00000000-0000-0000-0000-000000000000}}"
#endif
#ifndef SourceDir
  #error SourceDir is not defined
#endif
#ifndef OutputDir
  #error OutputDir is not defined
#endif
#ifndef OutputBaseFilename
  #error OutputBaseFilename is not defined
#endif

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf64}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\bin\launcher.exe
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DisableProgramGroupPage=yes
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\bin\launcher.exe"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\bin\launcher.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\bin\launcher.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
'''.lstrip()


def main() -> int:
    args = parse_args()
    delivery_root = resolve_delivery_root(Path(args.delivery_parent).resolve())
    manifest_path = delivery_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    portable_dir = delivery_root / "portable"
    launcher = portable_dir / "bin" / "launcher.exe"
    if not launcher.exists():
        raise InstallerError(f"Portable launcher not found: {launcher}")

    existing_setup = sorted(delivery_root.glob("*-Setup.exe"))
    output_base = existing_setup[0].stem if existing_setup else f"{args.app_name}-Setup"

    for path in delivery_root.glob("*-Setup.exe"):
        path.unlink(missing_ok=True)
    for path in delivery_root.glob("*-Setup.tar.zst"):
        path.unlink(missing_ok=True)

    iscc = find_iscc()
    app_id = "{" + str(uuid.uuid5(uuid.NAMESPACE_DNS, args.app_identifier)) + "}"

    with tempfile.TemporaryDirectory(prefix="cccc-inno-") as temp_dir:
        temp_path = Path(temp_dir)
        script_path = temp_path / "cccc-desktop.iss"
        script_path.write_text(build_script_text(), encoding="utf-8")

        cmd = [
            str(iscc),
            f"/DAppName={args.app_name}",
            f"/DAppVersion={manifest['version']}",
            f"/DAppPublisher={args.publisher}",
            f"/DAppId={app_id}",
            f"/DSourceDir={portable_dir}",
            f"/DOutputDir={delivery_root}",
            f"/DOutputBaseFilename={output_base}",
            str(script_path),
        ]
        subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True)

    installer_path = delivery_root / f"{output_base}.exe"
    if not installer_path.exists():
        raise InstallerError(f"Inno Setup output missing: {installer_path}")

    manifest["artifacts"] = [
        summarize_file(installer_path, delivery_root),
        summarize_dir(portable_dir),
    ]
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(installer_path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InstallerError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
