@echo off
rem Διπλό κλικ για να ανοίξει το dashboard στον browser (http://localhost:8501).
chcp 65001 >nul
title Trading Dashboard
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
.venv\Scripts\streamlit run dashboard.py
pause
