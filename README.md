# OTD Area Calculator

OTD Area Calculator 是一個 Windows 桌面工具，用來協助 osu! 玩家根據實際遊玩軌跡估算 OpenTabletDriver (OTD) 的手寫板區域設定。

程式會從支援的 Wacom 手寫板讀取 HID 原始座標，記錄遊玩時的筆尖活動範圍，過濾極端點後輸出可套用到 OTD 的寬度、高度與 X/Y 偏移值。

## 目前狀態

- 主要入口：`otd_area_calculator.py`
- 平台：Windows 10/11
- UI：PySide6 / Qt
- 硬體通訊：hidapi
- 目標工具：OpenTabletDriver
- CI 會在 Windows 上自動執行語法編譯、模組匯入與純計算邏輯單元測試；硬體相關行為仍需實機驗證。


## 功能

- 內建約 250 款手寫板規格（資料來自 OpenTabletDriver），自動辨識型號與尺寸。
- 透過 HID Raw Mode 讀取筆尖座標與壓力。
- 多執行緒背景讀取，並自動校準原始座標邊界。
- 可自動偵測 osu! 視窗並開始/停止錄製。
- 提供手動錄製模式。
- 在介面中即時預覽筆跡軌跡。
- 以 IQR 統計過濾雜訊後計算 OTD 建議區域。
- 可將 OTD 設定 JSON 複製到剪貼簿。

## 支援與限制

**已實機驗證：**

- **Wacom Intuos S (CTL-4100)** — 唯一在真實硬體上測試過的型號，座標讀取與計算都驗證過。

**可偵測（規格正確，但未驗證）：**

- 內建約 250 款手寫板的規格（型號、VID/PID、尺寸、最大座標），資料來自 OpenTabletDriver，涵蓋 Wacom、Huion、XP-Pen、UGEE、Gaomon、VEIKK 等品牌。
- 這些型號偵測得到、實體尺寸正確，但**封包格式未經實機確認**：目前實際座標讀取僅對 Wacom raw 格式有效，其他品牌的通用解析尚在進行中。
- 偵測到非 CTL-4100 型號時，介面會顯示未驗證警告，計算結果也會附註提醒，歡迎回報實測結果。
- 想協助驗證你的型號？用 `tools/dump_tablet.py` dump 出封包格式並回報（見 `docs/ROADMAP.md`）。

限制：

- 目前僅支援 Windows。
- HID 裝置可能被 Wacom 驅動、OpenTabletDriver 或其他工具獨佔，導致程式無法連線。
- 未驗證型號的封包格式（report ID、位元組布局）可能與 CTL-4100 不同，導致座標解析錯誤。

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
python otd_area_calculator.py
```

使用流程：

1. 開啟程式並確認硬體偵測結果。
2. 使用自動模式，或取消自動偵測後用手動模式錄製。
3. 正常遊玩 osu! 1 到 2 首歌（過程中至少完整掃過整塊板一次，有助於自動校準）。
4. 停止錄製後點選計算。
5. 將輸出的寬度、高度與偏移值套用到 OpenTabletDriver，或直接複製 JSON 設定。

## 打包成 exe

開發環境可先安裝打包依賴：

```powershell
python -m pip install -r requirements-dev.txt
```

然後執行：

```powershell
.\build_exe.bat
```

產物會輸出到 `dist/`。

## 專案結構

```text
otd_area_calculator.py         # 主程式
tablet_db.json                 # 手寫板規格資料庫（由 OTD 設定產生）
tools/import_otd_configs.py    # 從 OTD 設定重新產生 tablet_db.json
tools/dump_tablet.py           # 診斷工具：dump HID descriptor 與原始報表
requirements.txt               # 執行期依賴
requirements-dev.txt           # 開發/打包依賴
build_exe.bat                  # PyInstaller 打包腳本
tests/                         # 單元測試
docs/ROADMAP.md                # 進度與通用解析路線圖
NOTICE                         # 第三方資料出處與授權
```

## 隱私與資料

程式在本機執行，不需要網路連線，也不會上傳資料。錄製內容是手寫板座標軌跡，請不要把個人的診斷輸出或原始封包紀錄提交到公開 repo。

## 開發檢查

安裝依賴後執行語法檢查與單元測試：

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m py_compile otd_area_calculator.py
python -m pytest -q
```

## 授權

本專案程式碼以 MIT License 釋出。公開前請確認 `LICENSE` 中的 copyright 名稱符合你要使用的作者或組織名稱。

`tablet_db.json` 內的手寫板參數衍生自 OpenTabletDriver（LGPL-3.0），僅取用硬體事實參數（型號、VID/PID、尺寸、座標範圍），詳見 `NOTICE`。
