#!/usr/bin/env python3
"""
Regenerate tablet_db.json from an OpenTabletDriver configuration tree.

Usage:
    python tools/import_otd_configs.py <path-to>/OpenTabletDriver.Configurations/Configurations [out.json]

Only factual fields are extracted (model name, USB VID/PID, physical size, max
coordinates, max pressure, HID input report length, report-parser family name,
init strings). The data is derived from OpenTabletDriver (LGPL-3.0); see NOTICE.

This is a maintainer tool, not part of the runtime. Re-run it to refresh
tablet_db.json when OpenTabletDriver adds or corrects tablet definitions.
"""
import sys
import os
import json
import glob


def build_db(configs_dir):
    db = {}
    files = glob.glob(os.path.join(configs_dir, "**", "*.json"), recursive=True)
    skipped = 0
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception:
            skipped += 1
            continue

        specs = cfg.get("Specifications") or {}
        dig = specs.get("Digitizer") or {}
        name = cfg.get("Name")
        max_x, max_y = dig.get("MaxX"), dig.get("MaxY")
        width, height = dig.get("Width"), dig.get("Height")

        # Need a usable digitizer area to compute mm; skip screen-only / partial defs.
        if not (name and max_x and max_y and width and height):
            skipped += 1
            continue

        pen = specs.get("Pen") or {}
        for ident in cfg.get("DigitizerIdentifiers") or []:
            vid, pid = ident.get("VendorID"), ident.get("ProductID")
            if vid is None or pid is None:
                continue
            key = f"{vid},{pid}"
            parser = (ident.get("ReportParser") or "").split(".")[-1]
            report_len = ident.get("InputReportLength")
            entry = {
                "name": name,
                "vendor_id": int(vid),
                "product_id": int(pid),
                "max_x": int(max_x),
                "max_y": int(max_y),
                "width_mm": float(width),
                "height_mm": float(height),
                "max_pressure": pen.get("MaxPressure"),
                "input_report_length": report_len,
                "report_parser": parser,
                "init_strings": ident.get("InitializationStrings")
                or ident.get("FeatureInitReport")
                or [],
                "verified": False,
            }
            # Same (vid,pid) can appear twice (driver vs driverless). Prefer the
            # shorter report (the driverless variant OTD reads natively).
            prev = db.get(key)
            if prev is None or (report_len or 10 ** 9) < (prev["input_report_length"] or 10 ** 9):
                db[key] = entry
    return db, len(files), skipped


def main():
    if len(sys.argv) < 2:
        print("usage: import_otd_configs.py <OTD Configurations dir> [out.json]")
        return 2
    src = sys.argv[1]
    default_out = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tablet_db.json"
    )
    out = sys.argv[2] if len(sys.argv) > 2 else default_out

    db, scanned, skipped = build_db(src)
    payload = {
        "_source": "Derived from OpenTabletDriver (LGPL-3.0) — "
                   "https://github.com/OpenTabletDriver/OpenTabletDriver",
        "_count": len(db),
        "tablets": db,
    }
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")

    print(f"configs scanned: {scanned}, skipped: {skipped}, tablet identifiers: {len(db)}")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
