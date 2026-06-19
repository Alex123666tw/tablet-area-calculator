import hid
import time

WACOM_VID = 0x056A

def main():
    print("Listing devices...")
    for d in hid.enumerate(0, 0):
        if d['vendor_id'] == WACOM_VID:
            print(f"Found Wacom: VID={pid_hex(d['vendor_id'])} PID={pid_hex(d['product_id'])} Path={d['path']}")

    try:
        # Open first Wacom
        devs = hid.enumerate(WACOM_VID, 0)
        target = None
        for d in devs:
             if d.get('usage_page') == 0x0D:
                 target = d['path']
                 break
        if not target and devs:
            target = devs[0]['path']

        if not target:
            print("No Wacom found.")
            return

        print(f"Opening {target}...")
        device = hid.device()
        device.open_path(target)
        device.set_nonblocking(True)
        
        # Init Wacom mode
        try:
            device.send_feature_report([0x02, 0x02])
            print("Sent feature report.")
        except Exception as e:
            print(f"Feature report failed: {e}")

        print("Move pen... (Press Ctrl+C to stop)")
        count = 0
        while True:
            data = device.read(64)
            if data:
                if len(data) >= 8 and data[0] == 0x10:
                    x = data[2] | (data[3] << 8)
                    y = data[4] | (data[5] << 8)
                    p = data[6] | (data[7] << 8)
                    hex_str = ' '.join([f"{b:02X}" for b in data[:10]])
                    print(f"ID={data[0]:02X} | {hex_str} | X={x:5d} Y={y:5d} P={p:4d}")
                    count += 1
                    if count > 30:
                        break
            else:
                time.sleep(0.01)

    except Exception as e:
        print(f"Error: {e}")

def pid_hex(val):
    return f"0x{val:04X}"

if __name__ == "__main__":
    main()
