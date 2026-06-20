# Roadmap & Status

階段性進度報告與後續規劃。

## 現況（已完成）

- **單一主程式**：`otd_area_calculator.py`；移除舊版 v1–v5 與重複實作、診斷腳本。
- **穩定性修正**：HID 開啟移到 worker thread（消除 race）、即時軌跡節流到 ~120 Hz 並對軌跡設上限、比例鎖夾住板邊、收斂裸 `except`。
- **依賴與 CI**：`requirements.txt` 補齊 `numpy`；CI 在 Windows 上跑 `compile → import → pytest`，並升級 actions（消除 Node 20 警告）。
- **誠實的型號支援**：只有 **Wacom Intuos S (CTL-4100)** 經實機驗證（`verified: True`）；偵測到其他型號會在 UI 顯示未驗證警告、計算結果也附註提醒。
- **手寫板資料庫**：`tablet_db.json` 內含約 250 款手寫板的事實規格（型號、VID/PID、尺寸、最大座標），衍生自 OpenTabletDriver（見 `NOTICE`），開機載入並自動辨識型號與尺寸。

## 下一步：通用座標解析 (Phase A)

目前實際的**座標讀取**只對 Wacom raw 格式有效（report `0x10`、24-bit）。資料庫雖能辨識約 250 款手寫板的規格，但其他品牌還無法真正讀到座標。計畫：

1. **收集真實資料**（現在）：用 `tools/dump_tablet.py` dump 出各型號的 HID report descriptor 與原始報表。
2. **通用 digitizer 解析**：解析 report descriptor，自動取得 X/Y/壓力的位元位置與邏輯/實體範圍，對標準 HID digitizer 通用；Wacom raw 路徑保留為 fallback。
3. **逐一驗證**：每個型號需實機確認後才能標記 `verified: True`。

> 註：Wacom 在 raw mode 下，descriptor 描述的可能是標準 digitizer 報表而非 `0x10` raw 報表，因此 Phase A 一定要先有實機 dump 才能正確實作。

## 回報一個型號 / 貢獻資料

如果你的手寫板顯示「未驗證」，歡迎協助擴充：

```bash
python tools/dump_tablet.py --list                  # 先找出你的裝置
python tools/dump_tablet.py --raw 40 --wacom-mode   # Wacom
python tools/dump_tablet.py --raw 40                 # 其他品牌
```

把輸出（descriptor + 幾筆原始報表）貼到 issue。**請勿把 dump 直接 commit 進 repo**（見 `SECURITY.md`）。
