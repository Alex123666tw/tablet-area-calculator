#!/usr/bin/env python3
"""Safely regenerate tablet_db.json from factual OpenTabletDriver configs.

Usage: python tools/import_otd_configs.py <Configurations directory> [out.json]
Imported definitions are unverified; hardware verification belongs to runtime
curated overrides. See NOTICE for the upstream LGPL-3.0 attribution.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import warnings


def number(value, field, *, integer=False, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{field} is out of range")
    if integer and int(value) != value:
        raise ValueError(f"{field} must be an integer")
    return int(value) if integer else float(value)


def object_field(obj, field):
    value = obj.get(field)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def build_db(configs_dir):
    source = Path(configs_dir)
    if not source.is_dir():
        raise ValueError("source must be an existing configuration directory")
    def scan_error(error):
        raise error
    files = []
    # pathlib glob may silently suppress inaccessible subdirectories. A partial
    # scan must never be mistaken for a complete replacement database.
    for directory, subdirs, names in os.walk(source, onerror=scan_error):
        for name in subdirs:
            child = Path(directory) / name
            if child.is_symlink() or child.is_junction():
                raise ValueError("source contains a linked subdirectory; refusing a partial configuration scan")
        files.extend(Path(directory) / name for name in names if name.lower().endswith(".json"))
    files.sort()
    if not files:
        raise ValueError("source contains no JSON configurations")
    db, skipped = {}, 0
    for fp in files:
        try:
            cfg = json.loads(fp.read_text(encoding="utf-8-sig"))
            if not isinstance(cfg, dict):
                raise ValueError("configuration must be an object")
            specs = object_field(cfg, "Specifications")
            dig = object_field(specs, "Digitizer")
            # OTD also includes button-only/non-digitizer definitions.
            if not dig or any(dig.get(k) is None for k in ("MaxX", "MaxY", "Width", "Height")):
                skipped += 1
                continue
            name = cfg.get("Name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Name must be a nonempty string")
            geometry = {
                "max_x": number(dig["MaxX"], "MaxX", integer=True, minimum=1),
                "max_y": number(dig["MaxY"], "MaxY", integer=True, minimum=1),
                "width_mm": number(dig["Width"], "Width", minimum=1e-12),
                "height_mm": number(dig["Height"], "Height", minimum=1e-12),
            }
            pen = object_field(specs, "Pen")
            pressure = pen.get("MaxPressure")
            if pressure is not None:
                pressure = number(pressure, "MaxPressure", integer=True, minimum=1)
            identifiers = cfg.get("DigitizerIdentifiers", [])
            if not isinstance(identifiers, list):
                raise ValueError("DigitizerIdentifiers must be a list")
            if not identifiers:
                skipped += 1
            for ident in identifiers:
                if not isinstance(ident, dict):
                    raise ValueError("digitizer identifier must be an object")
                vid = number(ident.get("VendorID"), "VendorID", integer=True, maximum=65535)
                pid = number(ident.get("ProductID"), "ProductID", integer=True, maximum=65535)
                parser = ident.get("ReportParser") or ""
                if not isinstance(parser, str):
                    raise ValueError("ReportParser must be a string")
                report_len = ident.get("InputReportLength")
                if report_len is not None:
                    report_len = number(report_len, "InputReportLength", integer=True, minimum=1, maximum=65535)
                init = ident.get("InitializationStrings") or ident.get("FeatureInitReport") or []
                if not isinstance(init, list):
                    raise ValueError("initialization data must be a list")
                entry = {
                    "name": name.strip(), "vendor_id": vid, "product_id": pid,
                    **geometry, "max_pressure": pressure,
                    "input_report_length": report_len, "report_parser": parser.split(".")[-1],
                    "init_strings": init, "verified": False,
                }
                key = f"{vid},{pid}"
                prev = db.get(key)
                if prev is not None:
                    if any(prev[k] != entry[k] for k in geometry):
                        raise ValueError(f"conflicting dimensions/coordinates for USB identifier {key}; resolve upstream variants before importing")
                    if prev != entry:
                        warnings.warn(f"USB identifier {key} has equivalent-area interface variants; selecting the shortest input report", stacklevel=2)
                    # Report length never decides which physical dimensions to use.
                    if (report_len or math.inf) >= (prev["input_report_length"] or math.inf):
                        continue
                db[key] = entry
        except (ValueError, TypeError, OSError) as exc:
            raise ValueError(f"invalid configuration {fp.name}: {exc}") from exc
    if not db:
        raise ValueError("source contains no usable digitizer definitions")
    return db, len(files), skipped


def write_db(out, payload):
    """Replace only after the complete valid JSON has reached the same filesystem."""
    out = Path(out)
    if out.is_symlink() or (out.exists() and not out.is_file()):
        raise ValueError("output must be a regular file, not a link or directory")
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=out.parent,
                                         prefix=f".{out.name}.", suffix=".tmp", delete=False) as fh:
            temp_name = fh.name
            json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_name, out)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source")
    ap.add_argument("output", nargs="?", default=Path(__file__).resolve().parents[1] / "tablet_db.json")
    args = ap.parse_args(argv)
    try:
        db, scanned, skipped = build_db(args.source)
        payload = {
            "_source": "Derived from OpenTabletDriver (LGPL-3.0) — https://github.com/OpenTabletDriver/OpenTabletDriver",
            "_count": len(db), "tablets": db,
        }
        write_db(args.output, payload)
    except (ValueError, OSError) as exc:
        print(f"[error] {exc}; output was not replaced", file=sys.stderr)
        return 1
    print(f"configs scanned: {scanned}, skipped: {skipped}, tablet identifiers: {len(db)}")
    print(f"written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
