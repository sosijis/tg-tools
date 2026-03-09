@echo off
chcp 65001 >nul
title TG Tools

echo.
echo ╔══════════════════════════════════════════╗
echo ║          TG Tools — Запуск               ║
echo ╚══════════════════════════════════════════╝
echo.

:: Проверяем Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python не найден!
    echo.
    echo 1. Зайди на https://python.org/downloads
    echo 2. Скачай Python 3.11 или новее
    echo 3. При установке ОБЯЗАТЕЛЬНО поставь галочку:
    echo    "Add Python to PATH"
    echo 4. Перезапусти этот файл
    echo.
    start https://python.org/downloads
    pause
    exit /b 1
)

echo ✅ Python найден
echo.

:: Проверяем, установлены ли пакеты
python -c "import customtkinter" >nul 2>&1
if errorlevel 1 (
    echo 📦 Первый запуск — устанавливаю пакеты...
    echo    (займёт 1-2 минуты, только один раз)
    echo.
    pip install telethon customtkinter pillow qrcode --quiet
    if errorlevel 1 (
        echo ❌ Ошибка установки. Проверь интернет.
        pause
        exit /b 1
    )
    echo ✅ Пакеты установлены
    echo.
)

echo 🚀 Запускаю приложение...
echo.
python app.py

if errorlevel 1 (
    echo.
    echo ❌ Ошибка запуска. Лог ошибки:
    python app.py
    pause
)
