"""
oar_coklu_sampiyon.py — Çoklu Asia-Range Şampiyon Keşfi (PORTFÖY)
═══════════════════════════════════════════════════════════════════════════════════════
AMAÇ (kullanıcı): Tek şampiyon değil — kullanıcının GERÇEK trade stilleri (birden fazla
senaryo) ayrı ayrı kanıtlansın → Asia≥%1 günlerinde ≥1 kârlı işlem için şampiyon PORTFÖYÜ.

Kullanıcının izlediği 4 senaryo (hepsi: Asia≥%1 + POC/VAL/VAH + VWAP + footprint + opsiyon
+ RSI + hacim):
  1. Ekstrem temas DÖNÜŞLERİ (fade)              ← bu modül (mod=fade)
  2. Asia altı/üstü ÇIKIŞ + kalıcılık (kırılım)   ← bu modül (mod=trend)
  3. 0.377-0.618 fiblerinden DÖNÜŞLER             ← Faz-2 (yeni tetikleyici, kurallar netleşince)
  4. Asia EQ (0.5) üstü/altı KALICILIK            ← Faz-2 (yeni tetikleyici, kurallar netleşince)

FAZ-1 (bu dosya): Mevcut aday_sinyaller_uret ekstremde HEM fade HEM trend adayı üretiyor.
Bunları STİL bazında ayırıp HER STİL için AYRI kesfet çalıştırır → stil başına EN SAĞLAM
blok kombinasyonu = o stilin şampiyonu. Şampiyon koduna DOKUNMAZ (kesfet/aday çağrılır).

Her stil şampiyonu OOS+holdout ile doğrulanır (kesfet). Yalnız SAĞLAM olanlar portföye girer.
Portföy repo-yolu JSON'a yazılır (git-senkron; site/lider okuyabilir).

Çalıştırma:  python oar_coklu_sampiyon.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
"""
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

from oar_local_backtest import (_klines_oku, _aggt_ay_yollari, _metrics_oku,
                                _gun_hazirla, aday_sinyaller_uret)
from oar_kesif import kesfet

PORTFOY = Path(__file__).resolve().parent / "oar_sampiyon_portfoy.json"

# Stil tanımları: (ad, aday-filtresi). Faz-1 = ekstrem fade + kırılım trend.
STILLER = [
    ("ekstrem_donus_fade",   lambda a: a.get("mod") == "fade"),
    ("kirilim_devam_trend",  lambda a: a.get("mod") == "trend"),
]


def _stil_sampiyonu(adaylar):
    """Bir stilin adaylarında kesfet → en sağlam kombinasyon (yoksa en iyi aday)."""
    if len(adaylar) < 40:
        return None, 0
    k = kesfet(adaylar)
    enler = k.get("en_iyiler") or []
    if not enler:
        return None, len(adaylar)
    sampiyon = next((a for a in enler if a.get("saglam")), enler[0])
    return sampiyon, len(adaylar)


def _portfoy_yaz(portfoy):
    try:
        PORTFOY.write_text(json.dumps(portfoy, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"[ÇokluŞampiyon] portföy yazılamadı: {str(e)[:60]}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to",   dest="bit", default="2025-06")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args()
    semboller = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]

    # 1) Tüm sembollerin adaylarını topla (aday_sinyaller_uret — şampiyonun kendi üreticisi)
    havuz = []
    for sym in semboller:
        print(f"[ÇokluŞampiyon] {sym} {args.bas}..{args.bit} — aday üretiliyor (ay ay)…", flush=True)
        klines = _klines_oku(sym, args.bas, args.bit)
        yollar = _aggt_ay_yollari(sym, args.bas, args.bit)
        if klines is None or not yollar:
            print(f"   [{sym}] ⚠ parquet yok — atlandı", flush=True)
            continue
        gunler = _gun_hazirla(klines, yollar, _metrics_oku(sym, args.bas, args.bit))
        adaylar = aday_sinyaller_uret(gunler)
        for a in adaylar:
            a["_sembol"] = sym
        havuz += adaylar
        print(f"   [{sym}] ✓ {len(adaylar)} aday", flush=True)

    if not havuz:
        print("⚠ aday yok — parquet/aggTrades eksik."); return

    # 2) HER STİL için ayrı kesfet → stil şampiyonu
    portfoy = {"tarih": datetime.now(timezone.utc).isoformat(),
               "aralik": f"{args.bas}..{args.bit}", "semboller": semboller, "stiller": []}
    rapor = ["═══ ÇOKLU ASIA-RANGE ŞAMPİYON PORTFÖYÜ ═══"]
    for stil_ad, filtre in STILLER:
        alt = [a for a in havuz if filtre(a)]
        print(f"[ÇokluŞampiyon] '{stil_ad}' kesfet ({len(alt)} aday)…", flush=True)
        sampiyon, n = _stil_sampiyonu(alt)
        if not sampiyon:
            rapor.append(f"\n▸ {stil_ad}: yeterli sinyalli kombinasyon yok (n{n})")
            continue
        bek = sampiyon.get("holdout_beklenti") or sampiyon.get("beklenti") or {}
        eq1 = sampiyon.get("equity_1x") or {}
        eq3 = sampiyon.get("equity_3x") or {}
        saglam = bool(sampiyon.get("saglam"))
        kayit = {
            "stil": stil_ad, "bloklar": sampiyon.get("bloklar"), "saglam": saglam,
            "oos_puan": sampiyon.get("oos_puan"), "oos_wr": sampiyon.get("oos_wr"),
            "holdout_puan": sampiyon.get("holdout_puan"), "holdout_wr": sampiyon.get("holdout_wr"),
            "holdout_trade": sampiyon.get("holdout_trade"),
            "beklenti": bek.get("beklenti"), "aday_n": n,
            "equity_1x_son": eq1.get("son"), "equity_3x_son": eq3.get("son"),
        }
        portfoy["stiller"].append(kayit)
        rapor.append(
            f"\n▸ {stil_ad} [{'+'.join(sampiyon.get('bloklar', []))}] {'✅ SAĞLAM' if saglam else '⚠ holdout zayıf'}"
            f"\n    OOS puan {kayit['oos_puan']} WR%{kayit['oos_wr']} · HOLDOUT puan {kayit['holdout_puan']} "
            f"WR%{kayit['holdout_wr']} (n{kayit['holdout_trade']}) · beklenti {kayit['beklenti']}"
            f"\n    $1000 equity → 1x {eq1.get('son')} · 3x {eq3.get('son')}")

    saglamlar = [s for s in portfoy["stiller"] if s["saglam"]]
    rapor.append(f"\n═══ PORTFÖY: {len(saglamlar)}/{len(portfoy['stiller'])} stil SAĞLAM şampiyon üretti ═══")
    rapor.append("NOT: Faz-1 = ekstrem-fade + kırılım-trend. Faz-2 (0.377-0.618 dönüş, EQ kalıcılık) "
                 "kullanıcı kuralları netleşince eklenecek. Her stil bağımsız OOS+holdout doğrulamalı.")

    _portfoy_yaz(portfoy)
    metin = "\n".join(rapor)
    print("\n" + metin)
    print(f"\n[ÇokluŞampiyon] portföy → {PORTFOY.name} (commit+push edersen site/lider okur)", flush=True)

    if args.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            asyncio.run(bildir("Çoklu Şampiyon Portföy", "backtest",
                               f"{len(saglamlar)} sağlam stil şampiyonu bulundu", detay=metin))
            print("[Telegram] thread 4129'a gönderildi ✓", flush=True)
        except Exception as e:
            print(f"[Telegram] gönderilemedi: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
