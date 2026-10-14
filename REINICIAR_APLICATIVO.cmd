@echo off
title Reiniciar Predios Remotos
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Solicitar-Reinicio.ps1"
if errorlevel 1 (
    echo.
    echo El aplicativo no pudo reiniciarse. Revise el mensaje anterior.
    pause
    exit /b 1
)
echo.
echo Puede cerrar esta ventana.
pause
