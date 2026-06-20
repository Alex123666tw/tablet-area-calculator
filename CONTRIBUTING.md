# Contributing

感謝你願意協助改進 OTD Area Calculator。

## 開發環境

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

- `otd_area_calculator.py` 是唯一主程式。
- 早期版本與診斷腳本已移除，歷史保留在 Git commit 紀錄；請直接修改主程式。
- 不要提交 `build/`、`dist/`、`__pycache__/`、`.atlas/` 或本機硬體診斷輸出。
- `tablet_db.json` 由 `tools/import_otd_configs.py` 從 OpenTabletDriver 設定產生，**請勿手動編輯**；要更新請重跑該腳本。
- 只有實機驗證過的型號才能標記 `verified: True`（目前僅 CTL-4100）。新增已驗證型號時請附上 VID/PID、實體尺寸與驗證方式。

## 本機檢查

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m py_compile otd_area_calculator.py
python -m pytest -q
```

若有可用硬體，請同時人工驗證：

- 手寫板是否可被偵測。
- 自動/手動錄製是否能收集筆尖座標。
- 計算結果是否可套用到 OpenTabletDriver。

## 回報新手寫板型號

如果你有未列為「已驗證」的手寫板，歡迎協助擴充支援：

```powershell
python tools/dump_tablet.py --list                  # 找出你的裝置
python tools/dump_tablet.py --raw 40 --wacom-mode   # Wacom：dump descriptor + 原始報表
python tools/dump_tablet.py --raw 40                 # 其他品牌
```

把輸出開成 issue 附上。請勿把原始 dump 直接 commit（見 `SECURITY.md`）。
