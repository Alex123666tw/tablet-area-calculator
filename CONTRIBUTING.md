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

- `otd_area_calculator_v5.py` 是目前主要入口。
- 舊版檔案保留作為硬體除錯和歷史參考，修改主要功能時請優先更新 v5。
- 不要提交 `build/`、`dist/`、`__pycache__/`、`.atlas/` 或本機硬體診斷輸出。
- 新增手寫板型號時，請附上 VID/PID、實體尺寸與驗證方式。

## 本機檢查

```powershell
python -m py_compile debug_hid.py detect_tablet.py hid_diagnostic.py hid_dumper.py otd_area_calculator.py otd_area_calculator_raw.py otd_area_calculator_v1.py otd_area_calculator_v2.py otd_area_calculator_v3.py otd_area_calculator_v4.py otd_area_calculator_v5.py raw_input_diagnostic.py
```

若有可用硬體，請同時人工驗證：

- 手寫板是否可被偵測。
- 自動/手動錄製是否能收集筆尖座標。
- 計算結果是否可套用到 OpenTabletDriver。
