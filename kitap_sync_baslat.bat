@echo off
REM ════════════════════════════════════════════════════════════════
REM  OAR Kitap Sync — bilgisayar acilisinda kitaplari canli siteye yukler
REM  KULLANIM: Asagidaki 3 satiri KENDI bilginle doldur, sonra bu .bat'i
REM  Windows Gorev Zamanlayici'ya "oturum acildiginda" ekle. Tek seferlik.
REM ════════════════════════════════════════════════════════════════

REM --- 1) Bu .bat'in bulundugu klasore gec (oar-premium repo klasoru olmali) ---
cd /d "%~dp0"

REM --- 2) AYARLAR (KENDI bilginle degistir) ---
set OAR_SITE_URL=https://SENIN-SITEN.up.railway.app
set OAR_API_KEY=SENIN_API_KEY
set KITAP_KAYNAK_DIR=C:\Users\ONURKLNC\Desktop\Data\kitaplar

REM --- 3) Calistir (5 sn bekle: ag hazir olsun) ---
timeout /t 5 /nobreak >nul
python kitap_sync.py

REM Hata olursa pencere kapanmadan gor (Gorev Zamanlayici'da gerekmez)
REM pause
