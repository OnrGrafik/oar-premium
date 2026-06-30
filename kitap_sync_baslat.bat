@echo off
REM ════════════════════════════════════════════════════════════════
REM  OAR Kitap Sync — bilgisayar acilisinda kitaplari canli siteye yukler
REM  KULLANIM: Asagidaki 3 satiri KENDI bilginle doldur, sonra bu .bat'i
REM  Windows Gorev Zamanlayici'ya "oturum acildiginda" ekle. Tek seferlik.
REM ════════════════════════════════════════════════════════════════

REM --- 1) Bu .bat'in bulundugu klasore gec (oar-premium repo klasoru olmali) ---
cd /d "%~dp0"

REM --- 2) AYARLAR (URL ve klasor dolu; sadece API_KEY'i sen doldur) ---
set OAR_SITE_URL=https://oar-premium-production.up.railway.app
set OAR_API_KEY=
set KITAP_KAYNAK_DIR=C:\Users\ONURKLNC\Desktop\Data\kitaplar
REM NOT: Railway'de OAR_API_KEY tanimladiysan ayni degeri yukariya yaz.
REM      Tanimlamadiysan bos birak — yine calisir (yukleme ucu korumasiz).

REM --- 3) Calistir (5 sn bekle: ag hazir olsun) ---
timeout /t 5 /nobreak >nul
python kitap_sync.py

REM Hata olursa pencere kapanmadan gor (Gorev Zamanlayici'da gerekmez)
REM pause
