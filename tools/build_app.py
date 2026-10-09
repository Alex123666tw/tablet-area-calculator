#!/usr/bin/env python3
"""Build the Windows app without deleting existing releases or caller files.

Install requirements.txt and requirements-dev.txt into the selected Python
environment first. build_exe.bat is a noninteractive thin entrypoint; --check
validates paths/data/dependencies without building. Staging and diagnostics
remain under build/otd-packages so failed builds are inspectable.
"""
import argparse
from importlib import metadata
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

APP_NAME = "OTD_Area_Calculator"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def project_path(root, *parts):
    """Reject links/junctions in build outputs, even links within the project."""
    root = root.resolve()
    path = root.joinpath(*parts)
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink() or cursor.is_junction():
            raise ValueError(f"refusing linked output path: {cursor.name}")
    if not path.resolve().is_relative_to(root):
        raise ValueError("output path escapes the project")
    return path


def check_dependencies(root):
    from packaging.requirements import Requirement
    errors = []
    for manifest in ("requirements.txt", "requirements-dev.txt"):
        for line in (root / manifest).read_text(encoding="utf-8-sig").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            req = Requirement(line)
            if req.marker and not req.marker.evaluate():
                continue
            try:
                version = metadata.version(req.name)
            except metadata.PackageNotFoundError:
                errors.append(f"{req.name} is missing")
                continue
            if version not in req.specifier:
                errors.append(f"{req.name} {version} does not satisfy {req.specifier}")
    if errors:
        raise ValueError("; ".join(errors) + ". Install both requirement manifests in this Python environment")


def check_database(root):
    data = json.loads((root / "tablet_db.json").read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("tablets"), dict) or not data["tablets"]:
        raise ValueError("tablet_db.json must contain nonempty tablet definitions")
    for key, spec in data["tablets"].items():
        if not isinstance(spec, dict):
            raise ValueError(f"invalid tablet definition {key}")
        for field in ("max_x", "max_y", "width_mm", "height_mm"):
            v = spec.get(field)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                raise ValueError(f"invalid {field} for tablet {key}")


def build_command(root, stage):
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--noconsole",
           "--name", APP_NAME, "--icon=NONE", "--distpath", str(stage / "dist"),
           "--workpath", str(stage / "work"), "--specpath", str(stage),
           "--add-data", f"{root / 'tablet_db.json'}{os.pathsep}.",
           "--add-data", f"{root / 'requirements.txt'}{os.pathsep}."]
    for module in ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "hid", "win32gui", "win32con"):
        cmd.extend(("--hidden-import", module))
    cmd.extend(("--collect-all", "hid", str(root / "otd_area_calculator.py")))
    return cmd


def build_environment():
    env = os.environ.copy()
    if os.name == "nt":
        # DLL analysis must not collect same-named libraries from unrelated
        # applications on the caller PATH (e.g. Poppler's incompatible ICU).
        windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        paths = [Path(sys.executable).parent, Path(sys._base_executable).parent,
                 windows / "System32", windows, windows / "System32" / "Wbem"]
        env["PATH"] = os.pathsep.join(dict.fromkeys(str(path) for path in paths))
    return env


def check_executable(candidate, root):
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(candidate))
    # Reject incomplete packages before replacing a previously working release.
    if "tablet_db.json" not in archive.toc:
        raise ValueError("built executable is missing tablet_db.json")
    bundled_db = archive.extract("tablet_db.json")
    if bundled_db != (root / "tablet_db.json").read_bytes():
        raise ValueError("built executable contains a different tablet database")
    modules = archive.open_embedded_archive("PYZ.pyz").toc
    if not {"tablet_input", "area_analysis", "area_canvas", "numpy", "win32con"} <= set(modules):
        raise ValueError("built executable is missing required product modules")
    for native in ("hid", "win32gui"):
        if not any(Path(name).name.startswith(native + ".") and name.endswith(".pyd") for name in archive.toc):
            raise ValueError(f"built executable is missing {native}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="validate without writing/building")
    args = ap.parse_args(argv)
    root = PROJECT_ROOT
    try:
        build_dir = project_path(root, "build", "otd-packages")
        dist = project_path(root, "dist")
        target = project_path(root, "dist", APP_NAME + ".exe")
        for directory in (build_dir, dist):
            if directory.exists() and not directory.is_dir():
                raise ValueError(f"output directory is a file: {directory.name}")
        if target.exists() and not target.is_file():
            raise ValueError("release output is not a regular file")
        if not (root / "otd_area_calculator.py").is_file():
            raise ValueError("application entrypoint is missing")
        check_database(root)
        check_dependencies(root)
        if args.check:
            print("[ok] project paths, tablet database and dependency manifests validated")
            return 0
        inputs = {root / name: (root / name).read_bytes() for name in (
            "otd_area_calculator.py", "tablet_input.py", "area_analysis.py", "area_canvas.py",
            "tablet_db.json", "requirements.txt", "requirements-dev.txt",
        )}
        build_dir.mkdir(parents=True, exist_ok=True)
        # Every invocation gets its own workspace; no existing build/dist/spec is deleted.
        stage = Path(tempfile.mkdtemp(prefix="build-", dir=build_dir))
        command = build_command(root, stage)
        log = stage / "build.log"
        print(f"[build] staging: {stage}", flush=True)
        with log.open("w", encoding="utf-8") as fh:
            result = subprocess.run(command, cwd=root, env=build_environment(),
                                    stdout=fh, stderr=subprocess.STDOUT, check=False)
        if result.returncode:
            raise ValueError(f"PyInstaller failed (exit {result.returncode}); see {log}")
        candidate = project_path(root, "build", "otd-packages", stage.name, "dist", APP_NAME + ".exe")
        if not candidate.is_file() or candidate.stat().st_size < 2:
            raise ValueError(f"PyInstaller did not produce an executable; see {log}")
        with candidate.open("rb") as fh:
            if fh.read(2) != b"MZ":
                raise ValueError("build output is not a Windows executable")
        check_executable(candidate, root)
        if any(path.read_bytes() != before for path, before in inputs.items()):
            raise ValueError("application inputs changed during the build; rebuild the current sources")
        # Recheck after the external builder before publishing into a fixed release path.
        project_path(root, "dist", APP_NAME + ".exe")
        dist.mkdir(exist_ok=True)
        os.replace(candidate, target)
        print(f"[ok] executable: {target}")
        print(f"[info] build log: {log}")
        return 0
    except (OSError, ValueError, ImportError, RuntimeError, KeyError, EOFError, TypeError) as exc:
        print(f"[error] {exc}; existing release was not replaced", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
