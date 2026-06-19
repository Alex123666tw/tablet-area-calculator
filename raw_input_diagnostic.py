"""
Windows Raw Input API 診斷工具 (Robust Version)
嘗試使用 WM_INPUT 讀取手寫板資料。
"""
import ctypes
from ctypes import wintypes
import time
import sys
import traceback

# Win32 Constants
WM_INPUT = 0x00FF
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
RIM_TYPEHID = 2
RIDEV_INPUTSINK = 0x00000100
RIDEV_DEVNOTIFY = 0x00002000
RID_INPUT = 0x10000003
RID_HEADER = 0x10000005
PM_REMOVE = 0x0001
WS_OVERLAPPEDWINDOW = 0x00CF0000
CW_USEDEFAULT = 0x80000000

# Usage Pages
HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_GENERIC_MOUSE = 0x02
HID_USAGE_PAGE_DIGITIZER = 0x0D
HID_USAGE_DIGITIZER_PEN = 0x02

class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]

class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

def wnd_proc(hwnd, msg, wparam, lparam):
    if msg == WM_INPUT:
        try:
            # 1. Get Size
            dwSize = wintypes.DWORD(0)
            res = user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(dwSize), ctypes.sizeof(RAWINPUTHEADER))
            
            if res == 0 and dwSize.value > 0:
                # 2. Get Data
                buffer = ctypes.create_string_buffer(dwSize.value)
                user32.GetRawInputData(lparam, RID_INPUT, buffer, ctypes.byref(dwSize), ctypes.sizeof(RAWINPUTHEADER))
                
                # Parse Header
                header = RAWINPUTHEADER.from_buffer(buffer)
                
                if header.dwType == RIM_TYPEHID:
                    header_size = ctypes.sizeof(RAWINPUTHEADER)
                    raw_bytes = buffer.raw[header_size:]
                    hex_str = raw_bytes.hex().upper()
                    print(f"[HID] Size={header.dwSize} Data={hex_str}")
                
        except Exception as e:
            print(f"Error in WM_INPUT: {e}")

    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

def main():
    try:
        print("Initializing Raw Input Test...")
        
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        
        class WNDCLASS(ctypes.Structure):
            _fields_ = [
                ('style', wintypes.UINT),
                ('lpfnWndProc', WNDPROC),
                ('cbClsExtra', ctypes.c_int),
                ('cbWndExtra', ctypes.c_int),
                ('hInstance', wintypes.HINSTANCE),
                ('hIcon', wintypes.HICON),
                ('hCursor', wintypes.HICON),
                ('hbrBackground', wintypes.HBRUSH),
                ('lpszMenuName', wintypes.LPCWSTR),
                ('lpszClassName', wintypes.LPCWSTR)
            ]
        
        wndclass = WNDCLASS()
        wndclass.lpszClassName = "RawInputTestClass"
        wndclass.lpfnWndProc = WNDPROC(wnd_proc)
        wndclass.hInstance = kernel32.GetModuleHandleW(None)
        
        atom = user32.RegisterClassW(ctypes.byref(wndclass))
        if not atom:
            print(f"RegisterClass failed: {kernel32.GetLastError()}")
            return

        hwnd = user32.CreateWindowExW(0, wndclass.lpszClassName, "RawInputTest", 
                                      0, 0, 0, 0, 0, 0, 0, wndclass.hInstance, 0)
        if not hwnd:
            print(f"CreateWindow failed: {kernel32.GetLastError()}")
            return
            
        print(f"Window HWND: {hwnd}")

        # Register Devices
        rid = (RAWINPUTDEVICE * 2)()
        
        # Digitizer (0x0D)
        rid[0].usUsagePage = HID_USAGE_PAGE_DIGITIZER
        rid[0].usUsage = HID_USAGE_DIGITIZER_PEN
        rid[0].dwFlags = RIDEV_INPUTSINK | RIDEV_DEVNOTIFY
        rid[0].hwndTarget = hwnd
        
        # Generic Desktop (0x01) - Mouse (0x02) - just in case
        rid[1].usUsagePage = HID_USAGE_PAGE_GENERIC
        rid[1].usUsage = HID_USAGE_GENERIC_MOUSE
        rid[1].dwFlags = RIDEV_INPUTSINK
        rid[1].hwndTarget = hwnd

        if not user32.RegisterRawInputDevices(rid, 2, ctypes.sizeof(RAWINPUTDEVICE)):
            print(f"RegisterRawInputDevices failed: {kernel32.GetLastError()}")
            return

        print("\n=== Pre-Check: OTD Running? ===")
        print("Please move your pen on the tablet now.")
        print("Listening for 15 seconds... (Press Ctrl+C to abort)")

        msg = wintypes.MSG()
        start_time = time.time()
        while time.time() - start_time < 15:
            if user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.001)

    except Exception as e:
        print(f"\nCRITICAL ERROR: {e}")
        traceback.print_exc()
    finally:
        print("\nTest Finished.")
        input("Press Enter to close window...")

if __name__ == "__main__":
    main()
