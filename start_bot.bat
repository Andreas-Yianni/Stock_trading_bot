@echo off
rem Διπλό κλικ για να ξεκινήσει το Telegram bot. Μην κλείσεις το παράθυρο όσο θέλεις ειδοποιήσεις.
chcp 65001 >nul
title Trading Bot (Telegram)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
.venv\Scripts\python telegram_bot.py
pause
