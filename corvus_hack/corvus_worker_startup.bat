@echo off
REM Corvus worker startup - runs for ALL Windows users on login.
REM Skip if this is Mike's or Administrator's account.
if /I "%USERNAME%"=="Mike"          goto :EOF
if /I "%USERNAME%"=="Administrator"  goto :EOF

set "CORVUS_CAPTURE_PORT=8703"
set "CORVUS_ACCOUNT=%USERNAME%"
set "PATH=C:\Python314;%PATH%"

start "corvus_capture" /B C:\Python314\pythonw.exe E:\Corvus_Careebridge\corvus_hack\capture_server.py 8703
timeout /t 5 /nobreak >NUL
start "corvus_worker" /B C:\Python314\pythonw.exe E:\Corvus_Careebridge\corvus_hack\worker.py >> "E:\Corvus_Careebridge\logs\worker_%USERNAME%.log" 2>&1
