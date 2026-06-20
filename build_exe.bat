@echo off
REM OTD Area Calculator - PyInstaller 打包腳本
REM 此腳本會將 Python 程式打包成單一執行檔

echo ========================================
echo OTD Area Calculator - 打包工具
echo ========================================
echo.

REM 檢查是否已安裝 PyInstaller
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [錯誤] 未安裝 PyInstaller
    echo 正在安裝 PyInstaller...
    python -m pip install -r requirements-dev.txt
    if errorlevel 1 (
        echo [錯誤] PyInstaller 安裝失敗
        pause
        exit /b 1
    )
)

echo [1/3] 清理舊的建置檔案...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "*.spec" del /q "*.spec"

echo [2/3] 開始打包 (otd_area_calculator.py)...
python -m PyInstaller --onefile ^
    --noconsole ^
    --name "OTD_Area_Calculator" ^
    --icon=NONE ^
    --add-data "requirements.txt;." ^
    --add-data "tablet_db.json;." ^
    --hidden-import PySide6.QtCore ^
    --hidden-import PySide6.QtGui ^
    --hidden-import PySide6.QtWidgets ^
    --hidden-import hid ^
    --hidden-import win32gui ^
    --hidden-import win32con ^
    --exclude-module PySide6.QtWebEngine ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtQuick ^
    --exclude-module PySide6.Qt3D ^
    --exclude-module PySide6.QtCharts ^
    --exclude-module PySide6.QtDataVisualization ^
    --exclude-module PySide6.QtNetwork ^
    --exclude-module PySide6.QtSql ^
    --exclude-module PySide6.QtTest ^
    --exclude-module matplotlib ^
    --collect-all hid ^
    otd_area_calculator.py

if errorlevel 1 (
    echo.
    echo [錯誤] 打包失敗！
    pause
    exit /b 1
)

echo [3/3] 打包完成！
echo.
echo ========================================
echo 執行檔位置: dist\OTD_Area_Calculator.exe
echo ========================================
echo.
echo 您現在可以將 dist\OTD_Area_Calculator.exe 複製到任何地方使用
echo （不需要安裝 Python）
echo.

pause
