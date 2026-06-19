"""
測試腳本：偵測連接的手寫板設備
使用 HID API（與 OpenTabletDriver 相同的方式）
"""

import hid

print("=" * 60)
print("偵測連接的 HID 設備（手寫板）")
print("=" * 60)
print()

# 常見手寫板廠商的 Vendor ID
TABLET_VENDORS = {
    0x056a: "Wacom",
    0x28bd: "XP-Pen",
    0x256c: "Huion",
    0x0b57: "Gaomon",
    0x172f: "Parblo",
    0x5543: "UC-Logic",
}

# 列舉所有 HID 設備
devices = hid.enumerate()

tablet_devices = []

print(f"找到 {len(devices)} 個 HID 設備，正在篩選手寫板...")
print()

for device in devices:
    vid = device['vendor_id']
    pid = device['product_id']
    
    # 檢查是否為已知的手寫板廠商
    if vid in TABLET_VENDORS:
        manufacturer = device.get('manufacturer_string', 'Unknown')
        product = device.get('product_string', 'Unknown')
        
        tablet_devices.append({
            'vendor_id': vid,
            'product_id': pid,
            'vendor_name': TABLET_VENDORS[vid],
            'manufacturer': manufacturer,
            'product': product,
            'path': device['path']
        })
        
        print(f"[OK] 找到手寫板！")
        print(f"  廠商: {TABLET_VENDORS[vid]} (VID: 0x{vid:04X})")
        print(f"  產品: {product} (PID: 0x{pid:04X})")
        print(f"  製造商: {manufacturer}")
        print(f"  路徑: {device['path']}")
        print()

if not tablet_devices:
    print("[X] 未找到已知的手寫板設備")
    print()
    print("可能原因：")
    print("1. 手寫板未連接")
    print("2. 使用的是不在列表中的品牌")
    print()
    print("所有 HID 設備列表（前 20 個）：")
    print("-" * 60)
    for i, device in enumerate(devices[:20]):
        vid = device['vendor_id']
        pid = device['product_id']
        product = device.get('product_string', 'Unknown')
        print(f"{i+1}. VID: 0x{vid:04X}, PID: 0x{pid:04X}, 產品: {product}")
else:
    print("=" * 60)
    print(f"總共找到 {len(tablet_devices)} 個手寫板設備")
    print("=" * 60)
    
    # 保存結果到文件
    with open('detected_tablets.txt', 'w', encoding='utf-8') as f:
        for tablet in tablet_devices:
            f.write(f"廠商: {tablet['vendor_name']}\n")
            f.write(f"產品: {tablet['product']}\n")
            f.write(f"VID: 0x{tablet['vendor_id']:04X}\n")
            f.write(f"PID: 0x{tablet['product_id']:04X}\n")
            f.write(f"路徑: {tablet['path']}\n")
            f.write("-" * 60 + "\n")
    
    print("詳細資訊已保存到 detected_tablets.txt")

print()
input("按 Enter 鍵退出...")
