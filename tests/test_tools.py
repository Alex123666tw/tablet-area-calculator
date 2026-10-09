"""Exercise maintenance CLIs using disposable user data and an external HID stand-in."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
IMPORTER = ROOT / "tools" / "import_otd_configs.py"


def config():
    return {
        "Name": "Example tablet",
        "Specifications": {"Digitizer": {"Width": 152, "Height": 95, "MaxX": 15200, "MaxY": 9500},
                           "Pen": {"MaxPressure": 4095}},
        "DigitizerIdentifiers": [{"VendorID": 1386, "ProductID": 884, "InputReportLength": 10,
                                   "ReportParser": "Wacom.TestParser"}],
    }


def cli(script, *args, cwd=None, env=None):
    return subprocess.run([sys.executable, "-X", "utf8", str(script), *map(str, args)],
                          cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8")


@pytest.mark.parametrize("bad", ["missing", "empty", "broken", "array", "zero", "negative", "nan", "bool", "string", "identifier"])
def test_import_failure_keeps_previous_readable_database(tmp_path, bad):
    source = tmp_path / "configs"
    output = tmp_path / "database.json"
    old = b'{"tablets":{"old":{"name":"keep me"}}}\n'
    output.write_bytes(old)
    if bad != "missing":
        source.mkdir()
    if bad not in ("missing", "empty"):
        data = config()
        if bad == "array":
            data = []
        elif bad == "identifier":
            data["DigitizerIdentifiers"][0]["VendorID"] = 65536
        elif bad != "broken":
            data["Specifications"]["Digitizer"]["Width"] = {
                "zero": 0, "negative": -1, "nan": float("nan"), "bool": True, "string": "152",
            }[bad]
        (source / "config.json").write_text("{" if bad == "broken" else json.dumps(data), encoding="utf-8")
    result = cli(IMPORTER, source, output, cwd=tmp_path)
    assert result.returncode != 0
    assert "output was not replaced" in result.stderr
    assert output.read_bytes() == old
    assert json.loads(output.read_text())["tablets"]["old"]["name"] == "keep me"
    assert not list(tmp_path.glob(".database.json.*.tmp"))


def test_import_legal_interfaces_then_conflicting_area(tmp_path):
    source = tmp_path / "configs"
    source.mkdir()
    data = config()
    variant = copy.deepcopy(data["DigitizerIdentifiers"][0])
    variant["InputReportLength"] = 8
    data["DigitizerIdentifiers"].append(variant)
    (source / "tablet.json").write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "database.json"
    result = cli(IMPORTER, source, output, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "interface variants" in result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["_count"] == 1
    imported = payload["tablets"]["1386,884"]
    assert (imported["width_mm"], imported["height_mm"]) == (152, 95)
    assert imported["input_report_length"] == 8
    assert imported["verified"] is False
    before = output.read_bytes()
    data["Specifications"]["Digitizer"]["Width"] = 300
    (source / "conflict.json").write_text(json.dumps(data), encoding="utf-8")
    result = cli(IMPORTER, source, output, cwd=tmp_path)
    assert result.returncode != 0 and "conflicting" in result.stderr
    assert output.read_bytes() == before


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_failed_atomic_publish_retains_database(tmp_path, monkeypatch):
    importer = load_tool("import_otd_configs")
    output = tmp_path / "database.json"
    output.write_text('{"tablets":{"previous":{}}}', encoding="utf-8")
    before = output.read_bytes()
    def locked(*args):
        raise PermissionError("locked output")
    monkeypatch.setattr(importer.os, "replace", locked)
    with pytest.raises(PermissionError):
        importer.write_db(output, {"tablets": {"new": {}}})
    assert output.read_bytes() == before
    assert not list(tmp_path.glob(".database.json.*.tmp"))


@pytest.mark.parametrize("args", [["--vid", "0x056A"], ["--pid", "884"], ["--raw", "-1"],
                                  ["--raw", "10001"], ["--timeout", "nan"], ["--timeout", "0"],
                                  ["--timeout", "inf"], ["--vid", "65536", "--pid", "1"]])
def test_dump_rejects_bad_arguments_before_accessing_hardware(args):
    result = cli(ROOT / "tools" / "dump_tablet.py", *args)
    assert result.returncode == 2
    assert "error:" in result.stderr


def test_dump_read_only_failure_redacts_device_details_and_closes(monkeypatch, capsys):
    dumper = load_tool("dump_tablet")
    device = {"vendor_id": 1386, "product_id": 884, "usage_page": 13,
              "path": b"private/device/path", "serial_number": "SECRET_SERIAL"}
    class Handle:
        closed = False
        def open_path(self, path):
            pass
        def get_report_descriptor(self):
            raise OSError("SECRET_SERIAL private/device/path")
        def send_feature_report(self, data):
            pytest.fail("default diagnostics must not change device mode")
        def close(self):
            self.closed = True
    handle = Handle()
    monkeypatch.setitem(sys.modules, "hid", types.SimpleNamespace(enumerate=lambda *args: [device], device=lambda: handle))
    assert dumper.main([]) == 1
    text = capsys.readouterr()
    assert "SECRET_SERIAL" not in text.out + text.err
    assert "private/device/path" not in text.out + text.err
    assert handle.closed


def test_dump_raw_capture_timeout_is_failure(monkeypatch, capsys):
    dumper = load_tool("dump_tablet")
    class Handle:
        def open_path(self, path):
            pass
        def get_report_descriptor(self):
            return [0x05, 0x0D]
        def set_nonblocking(self, flag):
            pass
        def read(self, count):
            return []
        def close(self):
            pass
    monkeypatch.setitem(sys.modules, "hid", types.SimpleNamespace(
        enumerate=lambda *args: [{"vendor_id": 1386, "product_id": 884, "path": b"test"}], device=Handle))
    assert dumper.main(["--raw", "1", "--timeout", "0.01"]) == 1
    assert "0/1 reports" in capsys.readouterr().err


def build_fixture(tmp_path, fail=False, incomplete=False, mutate_source=False):
    project = tmp_path / "project"
    (project / "tools").mkdir(parents=True)
    shutil.copy(ROOT / "tools" / "build_app.py", project / "tools")
    shutil.copy(ROOT / "build_exe.bat", project)
    shutil.copy(ROOT / "requirements.txt", project)
    shutil.copy(ROOT / "requirements-dev.txt", project)
    shutil.copy(ROOT / "tablet_db.json", project)
    (project / "otd_area_calculator.py").write_text("# disposable app", encoding="utf-8")
    for name in ("tablet_input.py", "area_analysis.py", "area_canvas.py"):
        (project / name).write_text("# disposable product module", encoding="utf-8")
    (project / "dist").mkdir()
    release = project / "dist" / "OTD_Area_Calculator.exe"
    release.write_bytes(b"MZprevious release")
    (project / "dist" / "other.exe").write_bytes(b"other product")
    (project / "keep.spec").write_text("keep", encoding="utf-8")
    external = tmp_path / "external-builder"
    (external / "PyInstaller").mkdir(parents=True)
    (external / "PyInstaller" / "__init__.py").write_text("", encoding="utf-8")
    (external / "PyInstaller" / "__main__.py").write_text(
        "import pathlib,sys\n"
        f"if {fail!r}: sys.exit(7)\n"
        "out=pathlib.Path(sys.argv[sys.argv.index('--distpath')+1]); out.mkdir(parents=True)\n"
        f"if {mutate_source!r}: pathlib.Path('tablet_input.py').write_text('# changed during build')\n"
        f"(out/'OTD_Area_Calculator.exe').write_bytes({b'bad output' if incomplete else b'MZnew external build'!r})\n", encoding="utf-8")
    (external / "PyInstaller" / "archive").mkdir()
    (external / "PyInstaller" / "archive" / "__init__.py").write_text("", encoding="utf-8")
    (external / "PyInstaller" / "archive" / "readers.py").write_text(
        "import pathlib,types\n"
        "class CArchiveReader:\n"
        " def __init__(self,path): self.root=pathlib.Path(path).parents[4]; self.toc={'tablet_db.json':None, 'hid.test.pyd':None, 'win32gui.test.pyd':None, 'win32con.test.pyd':None}\n"
        " def extract(self,name): return (self.root/name).read_bytes()\n"
        " def open_embedded_archive(self,name): return types.SimpleNamespace(toc={'tablet_input':None, 'area_analysis':None, 'area_canvas':None, 'numpy':None, 'win32con':None})\n", encoding="utf-8")
    # Stand in only for installed external dependencies and the external builder.
    # The production manifests remain unchanged; real packaging is checked separately.
    for package in ("PySide6", "hidapi", "pywin32", "numpy", "pyinstaller", "pytest"):
        info = external / f"{package}-999.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Name: {package}\nVersion: 999.0\n", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(external))
    caller = tmp_path / "unrelated caller"
    (caller / "build").mkdir(parents=True)
    (caller / "dist").mkdir()
    (caller / "build" / "keep").write_text("caller build", encoding="utf-8")
    (caller / "dist" / "keep.exe").write_text("caller exe", encoding="utf-8")
    (caller / "keep.spec").write_text("caller spec", encoding="utf-8")
    return project, release, caller, env


@pytest.mark.parametrize("fail", [False, True])
def test_build_batch_from_arbitrary_cwd_preserves_other_files(tmp_path, fail):
    project, release, caller, env = build_fixture(tmp_path, fail)
    if os.name == "nt":
        # Exercise the public .bat entrypoint; select this test's Python environment.
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
        result = subprocess.run(["cmd.exe", "/d", "/c", str(project / "build_exe.bat")],
                                cwd=caller, env=env, capture_output=True, text=True)
    else:
        result = cli(project / "tools" / "build_app.py", cwd=caller, env=env)
    assert (result.returncode != 0) == fail, result.stdout + result.stderr
    assert release.read_bytes() == (b"MZprevious release" if fail else b"MZnew external build")
    assert (project / "dist" / "other.exe").read_bytes() == b"other product"
    assert (project / "keep.spec").read_text() == "keep"
    assert (caller / "build" / "keep").read_text() == "caller build"
    assert (caller / "dist" / "keep.exe").read_text() == "caller exe"
    assert (caller / "keep.spec").read_text() == "caller spec"


def directory_link(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if result.returncode:
            pytest.skip("environment cannot create a test link/junction")


def test_build_rejects_output_symlink_before_builder_runs(tmp_path):
    project, release, caller, env = build_fixture(tmp_path)
    assert (project / "dist").resolve().is_relative_to(tmp_path.resolve())
    shutil.rmtree(project / "dist")
    directory_link(project / "dist", caller / "dist")
    result = cli(project / "tools" / "build_app.py", cwd=caller, env=env)
    assert result.returncode != 0
    assert "linked output" in result.stderr
    assert (caller / "dist" / "keep.exe").read_text() == "caller exe"
    assert not (project / "build").exists()


def test_import_refuses_partial_scan_of_linked_source(tmp_path):
    source = tmp_path / "configs"
    source.mkdir()
    (source / "valid.json").write_text(json.dumps(config()), encoding="utf-8")
    extra = tmp_path / "additional-configs"
    extra.mkdir()
    (extra / "other.json").write_text(json.dumps(config()), encoding="utf-8")
    directory_link(source / "linked-configs", extra)
    output = tmp_path / "database.json"
    old = b'{"tablets":{"previous":{}}}'
    output.write_bytes(old)
    result = cli(IMPORTER, source, output, cwd=tmp_path)
    assert result.returncode != 0
    assert "partial configuration scan" in result.stderr
    assert output.read_bytes() == old


def test_incomplete_builder_output_keeps_existing_release(tmp_path):
    project, release, caller, env = build_fixture(tmp_path, incomplete=True)
    result = cli(project / "tools" / "build_app.py", cwd=caller, env=env)
    assert result.returncode != 0
    assert "not a Windows executable" in result.stderr
    assert release.read_bytes() == b"MZprevious release"


def test_source_change_during_build_keeps_existing_release(tmp_path):
    project, release, caller, env = build_fixture(tmp_path, mutate_source=True)
    result = cli(project / "tools" / "build_app.py", cwd=caller, env=env)
    assert result.returncode != 0
    assert "inputs changed during the build" in result.stderr
    assert release.read_bytes() == b"MZprevious release"
