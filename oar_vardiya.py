"""
oar_vardiya.py — TEK KOMUT: bilgisayar açık kaldıkça ajanlar SÜREKLİ backtest yapar
═══════════════════════════════════════════════════════════════════════════════════════
KULLANIM (tek satır — başka bir şey gerekmez):
    python oar_vardiya.py

Ne yapar (sırayla, sonra uyur, sonra tekrar — günlerce çalışabilir):
  1. HİPOTEZ MOTORU  : 436+ hipotezi tarar (fib×hacim×gün×genlik, long+short).
                       YENİ kanıtlı kazanan bulursa → Telegram + kanitli_bulgular.json
  2. STİL BACKTEST   : Senin doküman stillerin (377 dönüşü, EQ kalıcılık) test edilir.
  3. ÇOKLU ŞAMPİYON  : (günde 1 kez, ağır iş) fade+trend stillerine ayrı kesfet →
                       stil şampiyonları portföyü.
  Sonuçlar: Telegram (thread 4129) + backtest_sonuclari.json (defter) + kanitli_bulgular.json.

Veri sabitken tekrar tarama aynı sonucu verir → hafıza sayesinde Telegram'a SPAM ATMAZ,
yalnız YENİ bulgu bildirir. Yeni veri ayı indirdiğinde veya motor genişlediğinde yeni
bulgular gelir. Ctrl+C ile güvenle durdurulur; cache sayesinde kaldığı yerden devam eder.
"""
import subprocess
import sys
import time
from datetime import datetime, timezone

SEMBOL = "BTCUSDT,ETHUSDT"
BAS, BIT = "2019-01", "2025-06"
ARALIK_SAAT = 6            # tur arası uyku
AGIR_IS_SAAT = 24          # çoklu-şampiyon (aggTrades ağır) en fazla günde 1 kez


def _calistir(ad, args):
    print(f"\n{'='*70}\n[VARDİYA] {ad} başlıyor — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC\n{'='*70}", flush=True)
    try:
        r = subprocess.run([sys.executable] + args, timeout=6 * 3600)
        print(f"[VARDİYA] {ad} bitti (kod {r.returncode})", flush=True)
    except subprocess.TimeoutExpired:
        print(f"[VARDİYA] ⚠ {ad} 6 saati aştı — kesildi, sonraki işe geçiliyor", flush=True)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"[VARDİYA] ⚠ {ad} hata: {str(e)[:100]}", flush=True)


def main():
    print("[VARDİYA] Gece vardiyası başladı — Ctrl+C ile durdurabilirsin. "
          "Yalnız YENİ kanıtlı bulgular Telegram'a gider.", flush=True)
    son_agir = 0.0
    tur = 0
    while True:
        tur += 1
        print(f"\n████ VARDİYA TUR {tur} ████", flush=True)
        # 1) Hipotez motoru (hızlı — feature cache'li)
        _calistir("HİPOTEZ MOTORU",
                  ["oar_hipotez_motoru.py", "--symbol", SEMBOL,
                   "--from", BAS, "--to", BIT, "--telegram"])
        # 2) Stil backtest (hızlı — yalnız klines)
        _calistir("STİL BACKTEST (377 dönüşü + EQ kalıcılık)",
                  ["oar_stil_backtest.py", "--symbol", SEMBOL,
                   "--from", BAS, "--to", BIT, "--telegram"])
        # 2b) Micro-scalp: Asia-dışı fib ilk temas (hızlı — yalnız klines)
        _calistir("MICRO-SCALP (fib ilk temas)",
                  ["oar_micro_scalp.py", "--symbol", SEMBOL,
                   "--from", BAS, "--to", BIT, "--telegram"])
        # 2c) Fib paslaşma koridorları + hacim-bazlı scalp (hızlı — yalnız klines)
        _calistir("FİB PASLAŞMA SCALP (koridor + hacim)",
                  ["oar_fib_paslasma.py", "--symbol", SEMBOL,
                   "--from", BAS, "--to", BIT, "--telegram"])
        # 3) Çoklu şampiyon (AĞIR — aggTrades; günde en fazla 1)
        if time.time() - son_agir >= AGIR_IS_SAAT * 3600:
            _calistir("ÇOKLU ŞAMPİYON KEŞFİ (ağır, günde 1)",
                      ["oar_coklu_sampiyon.py", "--symbol", SEMBOL,
                       "--from", BAS, "--to", BIT, "--telegram"])
            son_agir = time.time()
        print(f"\n[VARDİYA] Tur {tur} tamam. {ARALIK_SAAT} saat uyku… "
              f"(sonuçlar: Telegram 4129 + kanitli_bulgular.json + backtest_sonuclari.json)\n"
              f"[VARDİYA] Yeni bulguların siteye/lidere yansıması için ara sıra:\n"
              f"          git add -A && git commit -m 'bulgular' && git push", flush=True)
        try:
            time.sleep(ARALIK_SAAT * 3600)
        except KeyboardInterrupt:
            print("\n[VARDİYA] Durduruldu. Cache'ler diskte — tekrar başlatınca hızlı devam eder.", flush=True)
            return


if __name__ == "__main__":
    main()
