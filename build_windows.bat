@echo off
echo === TGTools Builder ===
echo.
echo [1/2] Устанавливаю зависимости...
pip install telethon customtkinter pillow qrcode pyinstaller
echo.
echo [2/2] Собираю EXE...
pyinstaller --onefile --windowed --name "TGTools" --hidden-import telethon --hidden-import customtkinter --hidden-import PIL --hidden-import qrcode --collect-all customtkinter app.py
echo.
echo ===========================
echo Готово! EXE в папке dist\
echo ===========================
pause
