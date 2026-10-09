@echo off
REM Noninteractive entrypoint: all paths and release publication belong to build_app.py.
REM Use the desired Python environment (for example activate .venv-audit first).
python "%~dp0tools\build_app.py" %*
exit /b %errorlevel%
