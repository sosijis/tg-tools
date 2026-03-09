@echo off
chcp 65001 >nul
title TG Tools — Сборка EXE

echo.
echo ╔══════════════════════════════════════════╗
echo ║     TG Tools — Автоматическая сборка     ║
echo ╚══════════════════════════════════════════╝
echo.

:: Проверяем Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python не найден!
    echo.
    echo Скачай и установи Python 3.11+ с https://python.org
    echo Обязательно поставь галочку "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

echo ✅ Python найден
echo.
echo 📦 Устанавливаю зависимости...
echo.

pip install telethon customtkinter pillow qrcode pyinstaller --quiet --upgrade
if errorlevel 1 (
    echo ❌ Ошибка установки пакетов
    pause
    exit /b 1
)

echo.
echo ✅ Зависимости установлены
echo.
echo 🔨 Собираю EXE (займёт 1-3 минуты)...
echo.

pyinstaller ^
    --onefile ^
    --windowed ^
    --name "TGTools" ^
    --icon NONE ^
    --hidden-import telethon ^
    --hidden-import customtkinter ^
    --hidden-import PIL ^
    --hidden-import qrcode ^
    --hidden-import asyncio ^
    --collect-all customtkinter ^
    app.py

if errorlevel 1 (
    echo.
    echo ❌ Ошибка сборки! Попробуй запустить install_and_run.bat вместо этого.
    pause
    exit /b 1
)

echo.
echo ╔══════════════════════════════════════════╗
echo ║           ✅ ГОТОВО!                      ║
echo ║                                          ║
echo ║  EXE находится в папке: dist\TGTools.exe ║
echo ╚══════════════════════════════════════════╝
echo.

:: Открываем папку с exe
explorer dist

pause
