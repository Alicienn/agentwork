@echo off
rem Thin launcher for Windows - the logic lives in agentwork\pipeline.py (launch).
setlocal
call "%~dp0..\scripts\_python.cmd" || exit /b 127
"%AW_PY%" %AW_PY_ARGS% "%~dp0..\pipeline.py" launch %*
exit /b %ERRORLEVEL%
