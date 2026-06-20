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
- 新增手寫板型號時，請附上 VID/PID、實體尺寸與驗證方式。

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
