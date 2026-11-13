@echo off
rem Sets PY to a Python that really runs. The "python" Microsoft Store alias does not count.
set "PY="
python -c "import sys" >nul 2>nul && set "PY=python" && goto :eof
py -3 -c "import sys" >nul 2>nul && set "PY=py -3" && goto :eof
for %%V in (313 312 311 310) do (
  if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
    set "PY=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
    goto :eof
  )
)
