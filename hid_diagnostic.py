"""
Feature Report + Report ID parsing test
Check if hidapi includes/excludes report ID in read()
"""
import hid, time, sys

WACOM_VID = 0x056A
all_devs = hid.enumerate(0, 0)
wacom = [d for d in all_devs if d.get('vendor_id') == WACOM_VID]
if not wacom:
    print("No Wacom"); sys.exit(1)

pids = set(d['product_id'] for d in wacom)
pid = list(pids)[0]
ifaces = hid.enumerate(WACOM_VID, pid)
print(f"Wacom VID=0x{WACOM_VID:04X} PID=0x{pid:04X}")

dev = hid.device()
dev.open_path(ifaces[0]['path'])
dev.set_nonblocking(True)

# Send feature report
dev.send_feature_report([0x02, 0x02])
print("Feature Report sent OK")
time.sleep(0.1)

# Read packets continuously - user needs pen on tablet!
print("\nPut pen on tablet NOW! Reading for 8 seconds...")
print("(Move pen around to see coordinate changes)")
print()

start = time.time()
count = 0
prev_data = None
while time.time() - start < 8:
    data = dev.read(64)
    if not data:
        time.sleep(0.005)
        continue
    count += 1
    
    # Print raw bytes for first 10 packets, then every 50th
    if count <= 10 or count % 50 == 0:
        raw = bytes(data)
        length = len(raw)
        hex_str = raw[:16].hex().upper()
        
        # Try two interpretations:
        # A: data[0] = report ID (standard hidapi on Windows)
        if length >= 9:
            # Interpretation A: report ID at data[0]
            rid_a = data[0]
            x_a = data[2] | (data[3] << 8)
            y_a = data[4] | (data[5] << 8)
            p_a = data[6] | (data[7] << 8)
            
            # Interpretation B: no report ID, data starts at 0
            x_b = data[1] | (data[2] << 8)
            y_b = data[3] | (data[4] << 8)
            p_b = data[5] | (data[6] << 8)
            
            print(f"[{count:4d}] len={length} | {hex_str}")
            print(f"       InterpA(id@0): ID=0x{rid_a:02X} X={x_a:5d} Y={y_a:5d} P={p_a:5d}")
            print(f"       InterpB(no_id):          X={x_b:5d} Y={y_b:5d} P={p_b:5d}")
            print()

print(f"\nTotal: {count} packets")
dev.close()
print("Done!")
