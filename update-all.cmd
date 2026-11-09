@echo off
rem Thin launcher for Windows - the logic lives in agentwork\pipeline.py (update-all).
setlocal
call "%~dp0agentwork\scripts\_python.cmd" || exit /b 127
"%AW_PY%" %AW_PY_ARGS% "%~dp0agentwork\pipeline.py" update-all %*
exit /b %ERRORLEVEL%
