# OTD Area Calculator

**從實際遊玩軌跡估算你的 OpenTabletDriver 區域設定**
*Estimate your OpenTabletDriver area from how you actually play*

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.13-3776AB.svg?logo=python&logoColor=white)
![Qt](https://img.shields.io/badge/UI-PySide6%20%2F%20Qt-41CD52.svg?logo=qt&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6.svg?logo=windows&logoColor=white)
![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)

**English** | [繁體中文](#繁體中文)

## Overview

OTD Area Calculator is a Windows desktop tool that helps osu! players find a sensible
[OpenTabletDriver](https://github.com/OpenTabletDriver/OpenTabletDriver) (OTD) area
based on how they actually play, rather than guessing. It reads raw HID coordinates
from a supported Wacom tablet, records the range your pen sweeps while you play, and
rejects outliers with an interquartile-range (IQR) filter. It then outputs the width,
height and X/Y centre position (in millimetres) to enter in OTD, with an optional
screen aspect-ratio lock. Copied JSON contains an area fragment, not a complete OTD settings file. Coordinate reading is currently validated only on the Wacom
Intuos S (CTL-4100); around 250 other tablets are recognised from the OpenTabletDriver
database; live capture for those models is disabled until a suitable parser is implemented and validated. CTL-4100 capture is limited to its known native or Wacom-driver interfaces.

Start with the [installation commands](#快速開始-quick-start), then follow the
[recording workflow](#使用方式-usage). Hover recording is enabled by default, and automatic
recording retains samples across consecutive songs. Coordinates use fixed model specifications;
there is no full-board calibration sweep. Aspect-ratio locking is applied in millimetres,
and the app warns if an area must shrink to fit the tablet. Spatial sampling keeps representative
points and boundaries with bounded storage. Changing recording conditions or the ratio invalidates old results.

CTL-4100 hover/contact capture, calculation and stopping have been checked on real hardware.
Simultaneous osu! gameplay with OTD and full-settings import have not yet been validated.

---

<a name="繁體中文"></a>

## 專案簡介

OTD Area Calculator 是一個 Windows 桌面工具，用來協助 osu! 玩家依據**實際遊玩軌跡**估算
[OpenTabletDriver](https://github.com/OpenTabletDriver/OpenTabletDriver)（OTD）的手寫板區域設定，
取代憑感覺猜測的做法。

程式會從支援的 Wacom 手寫板讀取 HID 原始座標，記錄遊玩時筆尖實際掃過的範圍，
以 IQR（四分位距）統計過濾極端點後，輸出可填入 OTD 的寬度、高度與 X/Y 中心位置（毫米），
並可選擇鎖定螢幕長寬比。所有處理皆在本機完成，不需要網路連線。

> 目前實際的座標讀取僅在 **Wacom Intuos S (CTL-4100)** 上經過實機驗證；
> 錄製僅限 CTL-4100 已知的原生或 Wacom 驅動介面。資料庫雖能辨識約 250 款手寫板的規格，
> 但其他型號尚無可用錄製解析器，會提示不支援錄製，不套用猜測的封包布局。

## 功能特色 Features

- **約 250 款手寫板規格**：資料衍生自 OpenTabletDriver，自動辨識型號與實體尺寸，
  涵蓋 Wacom、Huion、XP-Pen、UGEE、Gaomon、VEIKK 等品牌。
- **HID Raw Mode 讀取**：直接讀取筆尖座標與壓力。
- **多執行緒背景擷取**：批次讀取 HID，使用型號規格的固定座標尺度，避免雜訊改變毫米換算。
- **osu! 自動偵測**：可自動偵測 osu! 視窗並開始／停止錄製，亦提供手動錄製模式。
- **即時軌跡預覽**：顯示懸空與觸板軌跡；預覽節流與畫布容量有界，錄製資料不因預覽節流而丟棄。
- **IQR 雜訊過濾**：依空間分布統計剔除離群點，降低長時間停留與微小抖動對建議區域的影響。
- **長寬比鎖定**：可依螢幕比例鎖定建議區域，並在毫米空間維持比例與板內邊界；無法完整涵蓋時顯示縮小提醒。
- **一鍵複製設定**：可將 OTD 區域 JSON 片段複製到剪貼簿（不是完整設定檔）。

## 預覽 Demo

> 目前尚未提供介面截圖。歡迎透過 PR 補上實機畫面。
>
> _Screenshots are not available yet. Contributions adding a UI capture are welcome._

<!--
補圖後可改成：
![OTD Area Calculator UI](docs/images/screenshot.png)
-->

## 技術棧 Built With

執行時相依套件（`requirements.txt`）：

| 依賴 Dependency | 用途 Role | 授權 License |
| --- | --- | --- |
| [PySide6](https://doc.qt.io/qtforpython/) | 桌面 GUI（Qt for Python） | LGPL-3.0 / 商業雙授權 |
| [hidapi](https://pypi.org/project/hidapi/) | HID 裝置通訊（讀取手寫板原始報表） | Python 綁定為 MIT；底層 hidapi 為多重授權（GPL-3.0 / BSD 風格 / 原始授權）— see upstream license |
| [pywin32](https://pypi.org/project/pywin32/) | Win32 API（偵測 osu! 視窗） | PSF License |
| [NumPy](https://numpy.org/) | 數值運算（IQR 統計過濾） | BSD-3-Clause |

開發／打包用依賴（`requirements-dev.txt`）：

| 依賴 Dependency | 用途 Role | 授權 License |
| --- | --- | --- |
| [PyInstaller](https://pyinstaller.org/) | 打包成單一 exe | GPL-2.0-or-later（含 bootloader 例外，產物可採任意授權） |
| [pytest](https://pytest.org/) | 單元測試 | MIT |

完整的開源專案清單與「在本專案的使用地方」請見下方
[本專案使用以下開源專案](#open-source-projects-本專案使用以下開源專案)。

## 快速開始 Quick Start

需求：Windows 10/11、Python 3.13（CI 使用版本）。建議使用虛擬環境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

啟動程式：

```powershell
python otd_area_calculator.py
```

### 打包成 exe（選用）

```powershell
python -m pip install -r requirements-dev.txt
.\build_exe.bat
```

產物會輸出到 `dist/`。打包工具會先檢查依賴、資料庫及來源一致性，在暫存目錄完成後才替換執行檔；失敗時保留原有產物。`dist/` 與 `build/` 為本機建置產物，不應提交到 Git。

The build tool validates dependencies, database contents and source consistency, then replaces
the executable only after a successful staged build. A failed build preserves the previous artifact.

## 使用方式 Usage

1. 開啟程式，確認上方的硬體偵測結果。
2. 使用自動模式，或取消自動偵測後改用手動模式錄製。
3. 正常遊玩 osu! 約 1 到 2 首歌；預設包含懸空軌跡，不必掃過整塊板來校準。自動模式會累積連續歌曲的樣本。
4. 停止錄製後點選「計算」。
5. 將輸出的寬度、高度與 X/Y 中心位置填入 OpenTabletDriver，也可複製含 `Width`、`Height`、`XPosition`、`YPosition`、`Rotation` 的區域片段；它不是完整 OTD 設定檔。

錄製以空間分格保留代表點與邊界，點數顯示保留樣本數。修改錄製條件、比例或開始新錄製會使舊結果失效。
CTL-4100 的懸空、觸板、計算與停止已實機確認；同時執行 OTD 與實際 osu! 遊玩，以及完整設定匯入仍待驗收。

### 隱私與資料

程式在本機執行，不需要網路連線，也不會上傳任何資料。錄製內容為手寫板座標軌跡，
請勿將個人的診斷輸出或原始封包紀錄提交到公開 repository（詳見 [`SECURITY.md`](SECURITY.md)）。

## Roadmap 開發路線

- **通用座標解析（Phase A）**：目前僅啟用 CTL-4100 已知介面的座標解析（report `0x10`、24-bit）。
  下一步是解析 HID report descriptor，自動取得 X/Y／壓力的位元位置與範圍，
  通用解析完成前，其他型號不套用 CTL-4100 布局作為 fallback。
- **逐型號驗證**：每個型號需實機 dump 與確認後，才會標記為 `verified`。
- **貢獻資料**：若你的手寫板顯示「未驗證」，可用 `tools/dump_tablet.py` dump 出 descriptor
  與原始報表；先移除個人資訊，再開 issue 提供解析所需的最小樣本（見 [`CONTRIBUTING.md`](CONTRIBUTING.md)）。

完整路線圖與目前狀態請見 [`docs/ROADMAP.md`](docs/ROADMAP.md)。

## Contributing 參與貢獻

歡迎協助改進，包括回報新的手寫板型號、補上介面截圖或修正計算邏輯。
開發環境設定、本機檢查與「回報新型號」的流程請見 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

開發檢查：

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m py_compile otd_area_calculator.py tablet_input.py area_analysis.py area_canvas.py
python -m pytest -q
```

程式入口為 `otd_area_calculator.py`，錄製、分析與畫布分別由 `tablet_input.py`、`area_analysis.py`、`area_canvas.py` 負責。CI 在 Windows 執行編譯、回歸測試與打包檢查；硬體效果仍需實機驗證。

The GUI entry point is `otd_area_calculator.py`; recording, analysis and drawing live in
`tablet_input.py`, `area_analysis.py` and `area_canvas.py`. Windows CI compiles, runs regression
tests and checks packaging. Hardware behavior requires real-device verification.

## Security 安全性

本工具在本機執行、不需網路連線，亦不應收集或上傳使用者資料。
若你發現安全問題，請使用 GitHub 的私密漏洞回報（Security Advisory）功能私下回報；
一般問題與建議請開 GitHub issue。請勿在公開 issue 中貼出可被濫用的細節或硬體唯一識別資訊。
完整政策請見 [`SECURITY.md`](SECURITY.md)。

## License 授權

本專案以 [MIT License](LICENSE) 釋出，Copyright (c) 2026 Alex Wu。

`tablet_db.json` 內的手寫板參數衍生自 OpenTabletDriver（LGPL-3.0），
僅取用硬體事實參數（型號、VID/PID、尺寸、座標範圍），詳見 [`NOTICE`](NOTICE)。

<a name="open-source-projects-本專案使用以下開源專案"></a>

## 本專案使用以下開源專案 / This project uses the following open-source projects

| 專案 Project | 在本專案的使用地方 Where / how it is used in this project | 授權 License |
| --- | --- | --- |
| [PySide6](https://doc.qt.io/qtforpython/) | 整個桌面圖形介面：主視窗、畫布與即時軌跡預覽（`QApplication`、`QMainWindow`、`QGraphicsView` 等，見 `otd_area_calculator.py`、`area_canvas.py`）。<br>The entire desktop GUI: main window, canvas, and live trace preview. | LGPL-3.0 / 商業雙授權 commercial dual license |
| [hidapi](https://pypi.org/project/hidapi/) | 透過 `import hid` 列舉並開啟手寫板的 HID 介面，在背景執行緒讀取筆尖原始座標與壓力報表（`tablet_input.py` 中的 `HIDPollingThread`、`TabletDetector`）。<br>Enumerates and opens the tablet's HID interface and reads raw pen reports. | Python 綁定 MIT；底層 hidapi 為多重授權（GPL-3.0 / BSD 風格 / 原始授權）— see upstream license |
| [pywin32](https://pypi.org/project/pywin32/) | 以 `win32gui` 列舉視窗標題來自動偵測 osu! 是否正在遊玩（`tablet_input.py` 中的 `OsuDetector`）。<br>Uses `win32gui` to detect the osu! window for auto record start/stop. | PSF License |
| [NumPy](https://numpy.org/) | 計算引擎中的數值運算與 IQR（四分位距）離群點過濾（`area_analysis.py` 中的 `ModernCalculationEngine.calculate`）。<br>Numerical work and IQR outlier rejection in the calculation engine. | BSD-3-Clause |
| [PyInstaller](https://pyinstaller.org/) | 由 `build_exe.bat` 將程式打包成單一執行檔（開發／發佈用，`requirements-dev.txt`）。<br>Packages the app into a single executable via `build_exe.bat`. | GPL-2.0-or-later（含 bootloader 例外，產物可採任意授權 with bootloader exception） |
| [pytest](https://pytest.org/) | 執行 `tests/` 下錄製、計算、畫布、GUI 流程與工具的回歸測試（`requirements-dev.txt`）。<br>Runs recording, analysis, canvas, GUI-workflow and tool regression tests under `tests/`. | MIT |
| [OpenTabletDriver](https://github.com/OpenTabletDriver/OpenTabletDriver) | 資料來源：`tablet_db.json` 內約 250 款手寫板的事實規格（型號、VID/PID、尺寸、座標範圍）衍生自其設定資料庫，由 `tools/import_otd_configs.py` 產生；也是本工具產出設定的目標驅動。詳見 [`NOTICE`](NOTICE)。<br>Data source for `tablet_db.json`; also the target driver for the generated area. See [`NOTICE`](NOTICE). | LGPL-3.0 |

> 各依賴的詳細授權條款請以 upstream 專案為準（see upstream license）。
> Refer to each upstream project for the authoritative license terms.
