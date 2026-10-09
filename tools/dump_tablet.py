#!/usr/bin/env python3
"""
Diagnostic dump for tablet HID data.

Captures a tablet's HID *report descriptor* and (optionally) a few raw input
reports, so its packet format can be reverse-engineered and added to the
generic parser. Works on any tablet, Wacom or otherwise.

Examples:
    python tools/dump_tablet.py                  # auto-pick a tablet, dump descriptor
    python tools/dump_tablet.py --list           # list every HID interface
    python tools/dump_tablet.py --vid 0x056A --pid 0x0374
    python tools/dump_tablet.py --raw 40         # also capture 40 raw reports (move the pen!)
    python tools/dump_tablet.py --raw 40 --wacom-mode   # send Wacom [0x02,0x02] first

NOTE: raw reports contain your pen coordinates. They are not sensitive, but per
SECURITY.md do NOT commit dump output into the repo — paste/share it directly.
"""
import argparse
import json
import math
import os
import sys
import time

WACOM_VENDOR_ID = 0x056A
DIGITIZER_USAGE_PAGE = 0x000D


def load_known():
    """{(vid, pid): name} from tablet_db.json, best-effort."""
    known = {}
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tablet_db.json"
    )
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for key, entry in (data.get("tablets") or {}).items():
            vid_s, pid_s = key.split(",")
            known[(int(vid_s), int(pid_s))] = entry.get("name", "")
    except Exception:
        pass
    return known


def hexbytes(b):
    return " ".join(f"{x:02X}" for x in bytes(b))


def describe(d, known):
    vid, pid = d.get("vendor_id", 0), d.get("product_id", 0)
    tag = known.get((vid, pid)) or ("Wacom (unlisted)" if vid == WACOM_VENDOR_ID else "")
    return vid, pid, d.get("usage_page", 0), d.get("usage", 0), d.get("product_string", "") or "", tag


def cmd_list(devices, known):
    print(f"{'VID':>7} {'PID':>7} {'UsagePg':>8} {'Usage':>6}  Product / Tag")
    print("-" * 72)
    for d in devices:
        vid, pid, up, us, prod, tag = describe(d, known)
        print(f"0x{vid:04X} 0x{pid:04X} {up:>8X} {us:>6X}  {prod} {('[' + tag + ']') if tag else ''}")


def pick_tablet(devices, known):
    cands = [d for d in devices
             if (d.get("vendor_id"), d.get("product_id")) in known
             or d.get("vendor_id") == WACOM_VENDOR_ID]
    if not cands:
        cands = [d for d in devices if d.get("usage_page") == DIGITIZER_USAGE_PAGE]
    if not cands:
        return None
    cands.sort(key=lambda d: 0 if d.get("usage_page") == DIGITIZER_USAGE_PAGE else 1)
    return cands[0]


def open_device(dev):
    import hid
    h = hid.device()
    try:
        if dev.get("path"):
            h.open_path(dev["path"])
        else:
            h.open(dev.get("vendor_id"), dev.get("product_id"))
    except Exception:
        h.close()
        raise
    return h


def main(argv=None):
    ap = argparse.ArgumentParser(description="Dump tablet HID descriptor and raw reports.")
    ap.add_argument("--list", action="store_true", help="list all HID interfaces and exit")
    ap.add_argument("--vid", type=lambda s: int(s, 0), help="vendor id, e.g. 0x056A")
    ap.add_argument("--pid", type=lambda s: int(s, 0), help="product id, e.g. 0x0374")
    ap.add_argument("--raw", type=int, default=0, metavar="N", help="capture N raw reports (move the pen)")
    ap.add_argument("--wacom-mode", action="store_true", help="send Wacom feature report [0x02,0x02] first")
    ap.add_argument("--timeout", type=float, default=15.0, help="seconds to wait for raw reports")
    args = ap.parse_args(argv)
    if (args.vid is None) != (args.pid is None):
        ap.error("--vid and --pid must be supplied together")
    if any(v is not None and not 0 <= v <= 65535 for v in (args.vid, args.pid)):
        ap.error("--vid and --pid must be between 0 and 65535")
    if not 0 <= args.raw <= 10000:
        ap.error("--raw must be between 0 and 10000")
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 300:
        ap.error("--timeout must be finite and between 0 and 300 seconds")
    if args.list and (args.raw or args.wacom_mode):
        ap.error("--list cannot be combined with capture or --wacom-mode")

    known = load_known()
    try:
        import hid
        devices = list(hid.enumerate(0, 0))
    except Exception as exc:
        print(f"[error] hid.enumerate failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    if not devices:
        print("[error] no HID devices found.")
        return 1

    if args.list:
        if args.vid is not None:
            devices = [d for d in devices if d.get("vendor_id") == args.vid and d.get("product_id") == args.pid]
            if not devices:
                print("[error] no matching HID interfaces found", file=sys.stderr)
                return 1
        cmd_list(devices, known)
        return 0

    if args.vid is not None and args.pid is not None:
        cands = [d for d in devices if d.get("vendor_id") == args.vid and d.get("product_id") == args.pid]
        cands.sort(key=lambda d: 0 if d.get("usage_page") == DIGITIZER_USAGE_PAGE else 1)
        dev = cands[0] if cands else None
    else:
        dev = pick_tablet(devices, known)

    if not dev:
        print("[error] no tablet found. Try --list, then pass --vid/--pid.")
        return 1

    vid, pid, up, us, prod, tag = describe(dev, known)
    if args.wacom_mode and vid != WACOM_VENDOR_ID:
        print("[error] --wacom-mode is supported only for Wacom devices", file=sys.stderr)
        return 1
    print("=" * 64)
    print(f"device : 0x{vid:04X}:0x{pid:04X}  {prod}  {('[' + tag + ']') if tag else ''}")
    print(f"interface: usage_page=0x{up:04X} usage=0x{us:04X}")
    print("=" * 64)

    try:
        h = open_device(dev)
    except Exception as exc:
        print(f"[error] could not open device ({type(exc).__name__})", file=sys.stderr)
        print("        (it may be held by the Wacom driver / OpenTabletDriver — close those first)")
        return 1

    failed = False
    try:
        try:
            desc = h.get_report_descriptor()
            if not desc:
                raise ValueError("empty report descriptor")
            print(f"\n--- REPORT DESCRIPTOR ({len(desc)} bytes) ---")
            print(hexbytes(desc))
        except Exception as exc:
            print(f"[error] get_report_descriptor failed ({type(exc).__name__})", file=sys.stderr)
            failed = True

        if args.wacom_mode:
            try:
                h.send_feature_report([0x02, 0x02])
                print("\n[info] sent Wacom feature report [0x02, 0x02]")
            except Exception as exc:
                print(f"[error] feature report failed ({type(exc).__name__})", file=sys.stderr)
                return 1

        if args.raw > 0:
            h.set_nonblocking(True)
            print(f"\n--- RAW REPORTS (move the pen; capturing up to {args.raw}) ---")
            got, start = 0, time.monotonic()
            while got < args.raw and (time.monotonic() - start) < args.timeout:
                data = h.read(64)
                if data:
                    print(f"[{len(data):3d}B] {hexbytes(data)}")
                    got += 1
                else:
                    time.sleep(0.002)
            if got < args.raw:
                print(f"[error] capture timed out: {got}/{args.raw} reports received", file=sys.stderr)
                failed = True

        if not failed:
            print("\n[done] review the output before sharing for analysis.")
    except Exception as exc:
        print(f"[error] capture failed ({type(exc).__name__})", file=sys.stderr)
        failed = True
    finally:
        try:
            h.close()
        except Exception:
            pass
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
