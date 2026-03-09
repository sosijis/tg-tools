@echo off
chcp 65001 >nul
title TG Tools

REM Проверяем Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Python не найден. Скачай с https://python.org
    echo Не забудь поставить галочку "Add Python to PATH"
    pause
    start https://www.python.org/downloads/
    exit /b 1
)

REM Устанавливаем зависимости если нужно
pip show customtkinter >nul 2>&1
if errorlevel 1 (
    echo Первый запуск — устанавливаю пакеты...
    pip install telethon customtkinter pillow qrcode --quiet
)

REM Запускаем приложение
python app.py
