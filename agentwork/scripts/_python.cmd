@echo off
rem Called by the agentwork .cmd launchers: picks a Python 3.9+ interpreter into AW_PY / AW_PY_ARGS.
rem Override with: set PYTHON_BIN=C:\path\to\python.exe
set "AW_PY_ARGS="
if defined PYTHON_BIN (
  set "AW_PY=%PYTHON_BIN%"
  exit /b 0
)
where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && (
  set "AW_PY=py"
  set "AW_PY_ARGS=-3"
  exit /b 0
)
for %%P in (python python3) do (
  where %%P >nul 2>nul && %%P -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && (
    set "AW_PY=%%P"
    exit /b 0
  )
)
echo ERROR: Python 3.9+ not found (tried py -3, python, python3). Install it from python.org or set PYTHON_BIN. 1>&2
exit /b 127
