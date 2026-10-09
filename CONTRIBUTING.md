# Contributing

**English** | [繁體中文](#繁體中文)

Thanks for your interest in improving **OTD Area Calculator**. Contributions are
welcome — reporting a new tablet model, adding a UI screenshot, improving the
calculation logic, or fixing bugs.

## Quick reference (English)

- `otd_area_calculator.py` is the GUI entry point. Recording, analysis and
  drawing belong to `tablet_input.py`, `area_analysis.py` and `area_canvas.py`.
  Keep changes connected to the normal entry point.
- Do **not** commit `build/`, `dist/`, `__pycache__/`, `.atlas/`, or any local
  hardware diagnostic output.
- `tablet_db.json` is generated from OpenTabletDriver configurations by
  `tools/import_otd_configs.py` — **do not hand-edit it**; re-run the script to
  update it.
- Model recognition does not imply capture support. Unsupported model/interface layouts
  must be refused rather than guessed. Fixed geometry, hover handling and shutdown should
  be checked with real hardware; passing tests does not prove osu!/OTD coexistence.
- Only hardware that has actually been validated may be marked `verified: True`
  (currently only the Wacom Intuos S / CTL-4100). When adding a verified model,
  include its VID/PID, physical dimensions, and how it was validated.

Use Windows 10/11 and Python 3.13 (the CI version). Local checks:

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m py_compile otd_area_calculator.py tablet_input.py area_analysis.py area_canvas.py
python -m pytest -q
```

---

<a name="繁體中文"></a>

## 繁體中文

感謝你願意協助改進 **OTD Area Calculator**。歡迎各種形式的貢獻：回報新的手寫板型號、
補上介面截圖、改進計算邏輯，或修正錯誤。

## 開發環境

使用 Windows 10/11 與 Python 3.13（CI 使用版本）。建議使用虛擬環境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

如果需要打包 exe：

```powershell
python -m pip install -r requirements-dev.txt
```

## 修改前請注意

- `otd_area_calculator.py` 是正常 GUI 入口；錄製、計算與畫布分別由 `tablet_input.py`、`area_analysis.py`、`area_canvas.py` 負責。請按職責修改並確認入口仍可使用。
- 不要提交 `build/`、`dist/`、`__pycache__/`、`.atlas/` 或本機硬體診斷輸出。
- `tablet_db.json` 由 `tools/import_otd_configs.py` 從 OpenTabletDriver 設定產生，
  **請勿手動編輯**；要更新請重跑該腳本。
- 規格辨識不代表支援錄製；不支援的型號／介面應拒絕猜測封包布局。
- 只有實機驗證過的型號才能標記 `verified: True`（目前僅 CTL-4100）。
  新增已驗證型號時請附上 VID/PID、實體尺寸與驗證方式。

## 本機檢查

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m py_compile otd_area_calculator.py tablet_input.py area_analysis.py area_canvas.py
python -m pytest -q
```

若有可用硬體，請同時人工驗證：

- 手寫板是否可被偵測。
- 自動／手動錄製是否能收集懸空與觸板座標、累積歌曲並正常停止。
- 固定尺度、毫米比例、板內邊界與縮小提醒是否符合實際硬體。
- 複製內容是否為 OTD 區域片段；不要將它當成完整設定檔。
- 是否實際與 osu!／OTD 同時使用；回報時分清測試通過與實際遊玩證據。

## 回報新手寫板型號

如果你有未列為「已驗證」的手寫板，歡迎協助擴充支援：

```powershell
python tools/dump_tablet.py --list                  # 找出你的裝置
python tools/dump_tablet.py --vid 0x056A --pid 0x0374 --raw 40  # 改為目標 VID/PID
```

`--wacom-mode` 會發送 feature report，僅在明確診斷模式切換時使用；正常錄製不需要它。

先移除序號、本機路徑與其他個人資訊，只在 issue 附上解析所需的最小樣本、型號和 VID/PID。請勿把原始 dump 直接 commit（見 [`SECURITY.md`](SECURITY.md)）。

To report a new model, run `--list`, then supply **both** `--vid` and `--pid` with
`--raw` as shown above. `--wacom-mode` sends a feature report and is only for an explicitly
requested diagnostic mode change; normal recording does not need it. Remove serial numbers,
local paths and other personal data before sharing the minimum sample needed for parsing.

## 工具與 Pull Request / Tools and pull requests

匯入工具要求有效的設定來源；空來源、衝突或失敗不應覆蓋現有資料庫。打包透過
`build_exe.bat`／`tools/build_app.py`，不要用刪除既有產物的方式處理失敗。
PR 請說明使用者會遇到的問題、修改後的行為與實際確認方式，並註明未驗證的硬體或遊玩情境。

The importer must preserve the existing database on empty, conflicting or failed input.
Use `build_exe.bat` / `tools/build_app.py` for packaging; preserve existing artifacts when a build fails.
In a PR, describe the user-visible problem, resulting behavior and verification, distinguishing
real-device or gameplay evidence from automated tests. Keep commits focused and follow existing code style.
