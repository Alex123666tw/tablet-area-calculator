import hid
import sys

print("正在掃描 HID 設備...")

try:
    devices = hid.enumerate()
    print(f"找到 {len(devices)} 個 HID 設備：")
    
    found_wacom = False
    
    for i, device in enumerate(devices):
        vid = device['vendor_id']
        pid = device['product_id']
        product = device['product_string']
        manufacturer = device['manufacturer_string']
        
        print(f"[{i+1}] VID: 0x{vid:04X} | PID: 0x{pid:04X} | {manufacturer} - {product}")
        
        if vid == 0x056a: # Wacom Vendor ID
            found_wacom = True
            print("   *** 發現 Wacom 設備！ ***")
            
    if not found_wacom:
        print("\n[警告] 未檢測到 Wacom 設備 (VID 0x056a)。")
        print("請確認：")
        print("1. 手寫板已插入電腦")
        print("2. 是否有其他驅動程式獨佔了設備？")
        
except Exception as e:
    print(f"\n[錯誤] HID 掃描失敗: {e}")

print("\n按 Enter 鍵退出...")
input()
