# OTD Area Calculator

OTD Area Calculator 是一個 Windows 桌面工具，用來協助 osu! 玩家根據實際遊玩軌跡估算 OpenTabletDriver (OTD) 的手寫板區域設定。

程式會從支援的 Wacom 手寫板讀取 HID 原始座標，記錄遊玩時的筆尖活動範圍，過濾極端點後輸出可套用到 OTD 的寬度、高度與 X/Y 偏移值。

## 目前狀態

- 主要入口：`otd_area_calculator_v5.py`
- 平台：Windows 10/11
- UI：PySide6 / Qt
- 硬體通訊：hidapi
- 目標工具：OpenTabletDriver
- 目前沒有自動化硬體測試；公開前已做語法、依賴匯入與純計算邏輯 smoke test。

舊版檔案 `otd_area_calculator.py`、`otd_area_calculator_raw.py`、`otd_area_calculator_v1.py` 到 `otd_area_calculator_v4.py` 暫時保留作為開發歷史與除錯參考。一般使用者請從 v5 開始。

## 功能

- 自動偵測已知 Wacom 手寫板型號。
- 透過 HID Raw Mode 讀取筆尖座標與壓力。
- 可自動偵測 osu! 視窗並開始/停止錄製。
- 提供手動錄製模式。
- 在介面中即時預覽筆跡軌跡。
- 根據記錄資料計算 OTD 建議區域。
- 可將 OTD 設定 JSON 複製到剪貼簿。

## 支援與限制

目前內建規格表包含：

- Wacom Intuos S CTL-4100
- Wacom Intuos M CTL-6100
- Wacom Intuos S BT CTL-4100WL
- Wacom One S CTL-472
- Wacom One M CTL-672

限制：

- 目前僅支援 Windows。
- HID 裝置可能被 Wacom 驅動、OpenTabletDriver 或其他工具獨佔，導致程式無法連線。
- 全域快捷鍵由 `keyboard` 套件提供，在某些環境可能需要系統權限。
- 不同手寫板韌體或驅動狀態可能回報不同封包格式，未知型號需要實機驗證。

## 安裝

建議使用虛擬環境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 執行

```powershell
python otd_area_calculator_v5.py
```

使用流程：

1. 開啟程式並確認硬體偵測結果。
2. 使用自動模式，或取消自動偵測後用手動模式錄製。
3. 正常遊玩 osu! 1 到 2 首歌。
4. 停止錄製後點選計算。
5. 將輸出的寬度、高度與偏移值套用到 OpenTabletDriver。

## 打包成 exe

開發環境可先安裝打包依賴：

```powershell
python -m pip install -r requirements-dev.txt
```

然後執行：

```powershell
.\build_exe.bat
```

產物會輸出到 `dist/`。`dist/` 與 `build/` 是本機建置產物，不應提交到 Git。

## 專案結構

```text
otd_area_calculator_v5.py      # 目前主程式
detect_tablet.py               # 手寫板偵測輔助工具
hid_diagnostic.py              # HID 診斷工具
hid_dumper.py                  # HID 封包 dump 工具
raw_input_diagnostic.py        # Windows Raw Input 診斷工具
requirements.txt               # 執行期依賴
requirements-dev.txt           # 開發/打包依賴
build_exe.bat                  # PyInstaller 打包腳本
```

## 隱私與資料

程式在本機執行，不需要網路連線，也不會上傳資料。錄製內容是手寫板座標軌跡，請不要把個人的診斷輸出或原始封包紀錄提交到公開 repo。

## 開發檢查

基本語法檢查：

```powershell
python -m py_compile debug_hid.py detect_tablet.py hid_diagnostic.py hid_dumper.py otd_area_calculator.py otd_area_calculator_raw.py otd_area_calculator_v1.py otd_area_calculator_v2.py otd_area_calculator_v3.py otd_area_calculator_v4.py otd_area_calculator_v5.py raw_input_diagnostic.py
```

## 授權

本專案以 MIT License 釋出。公開前請確認 `LICENSE` 中的 copyright 名稱符合你要使用的作者或組織名稱。
