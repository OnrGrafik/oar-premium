"""
Masaüstüne Crypto AI Agent kısayolu oluşturur (Windows)
"""
import os, sys
from pathlib import Path

def create_shortcut():
    desktop = Path(os.path.expanduser("~")) / "Desktop"
    agent_dir = Path(__file__).parent.resolve()
    python_exe = sys.executable

    # .bat dosyası oluştur (kısayol gibi çalışır)
    bat_path = desktop / "Crypto AI Agent.bat"
    bat_content = f'''@echo off
title Crypto AI Agent
cd /d "{agent_dir}"
echo ==========================================
echo   Crypto AI Agent baslatiliyor...
echo   Tarayici: http://localhost:8000
echo   Durdurmak icin: CTRL+C
echo ==========================================
"{python_exe}" -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
'''
    bat_path.write_text(bat_content, encoding="utf-8")
    print(f"✅ Kısayol oluşturuldu: {bat_path}")
    print("   Masaüstünden çift tıklayarak başlatabilirsiniz!")

if __name__ == "__main__":
    create_shortcut()
