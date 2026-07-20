"""
oar_kapi_analiz.py — MARKET KAPISI: per-sembol mü, iki-kapı mı? (ANAYASA #8 GÜVENLİ)
════════════════════════════════════════════════════════════════════════════════════
Kullanıcı sorusu: "BTC ayrı, ETH ayrı değerlendirilsin. BTC Asia %1 aşarsa
şampiyonlar BTC'de nasıl sonuç alır; ETH %1 aşarsa ETH'de nasıl?" → kapıyı öyle
değiştirmeden ÖNCE backtest.

BULGU (koddan doğrulandı): oar_local_backtest.aday_sinyaller_uret ZATEN per-sembol
kapı uygular (asia_gecerli her sembolü KENDİ Asia range'ine göre süzer). "BTC VE ETH
ikisi de ≥%1" çapraz kapısı SADECE canlı yolda (oar_session_agent._market_fade_gunu:198)
vardır — backtest'te HİÇ YOK. Yani şampiyonların kanıtlı istatistikleri (serap DSR 1.0)
zaten PER-SEMBOL kapı altında üretildi; canlı iki-kapı ondan DAHA KISITLAYICI.

Bu araç iki kapıyı YAN YANA ölçer (şampiyon başına, sembol başına):
  A) PER-SEMBOL KAPI (önerilen)  = o sembolün TÜM şampiyon işlemleri (kendi Asia≥%1)
  B) İKİ-KAPI (mevcut canlı)     = yalnız BTC VE ETH aynı gün ≥%1 olan günlerdeki işlemler
Metrikler: n · WR · PF · beklenti · maxDD · OOS beklenti + $1000 compound (1x/3x/5x) +
likidasyon. B kapısı A'ya göre KAÇ işlem atıyor ve PF/beklenti nasıl değişiyor → net karar.

ŞAMPİYONA DOKUNMAZ: yalnız oar_kesif._filtre + oar_sampiyon_confirm._senaryo_metrik/
_equity_sim çağrılır; şampiyon blokları oar_sampiyon_portfoy.json'dan okunur.

Havuz: .serap_cache/sampiyon_havuz_* (serap koşulduysa VAR → aggTrades TEKRAR İŞLENMEZ,
dakikalar). Cache yoksa oar_sampiyon_confirm._analiz ile üretir (aggTrades, ağır).

KULLANIM:
  python oar_kapi_analiz.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 [--telegram] [--taze]
KALICI ÇIKTI: kapi_analiz_sonuc.json (repo kökü, git-senkron → commit+push → lider+Claude okur).
"""
import os
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

from oar_local_backtest import (_klines_oku, _ms_olcekle, GUN_MS, SAAT_MS,
                                ASIA_BAS_UTC, ASIA_BIT_UTC)
from oar_sampiyon_confirm import _senaryo_metrik, _equity_sim

SERAP_CACHE = Path(__file__).resolve().parent / ".serap_cache"
PORTFOY = Path(__file__).resolve().parent / "oar_sampiyon_portfoy.json"
SONUC = Path(__file__).resolve().parent / "kapi_analiz_sonuc.json"
KAPI_ESIK = 1.0   # Asia range ≥ %1 (canlı kapı ile birebir)


# ── Şampiyon havuzu: serap cache'ten (hızlı) ya da confirm._analiz'ten (ağır) ──
def _havuz_yukle(semboller, bas, bit, taze=False):
    """Şampiyon aday havuzu (her aday _sembol/_gun/ts/pct + blok feature'ları taşır)."""
    import pickle
    anahtar = "sampiyon_havuz_" + "_".join(semboller) + f"_{bas}_{bit}"
    yol = SERAP_CACHE / f"{anahtar}.pkl"
    if not taze and yol.exists():
        try:
            with open(yol, "rb") as f:
                havuz = pickle.load(f)
            print(f"[Kapı] serap cache'ten {len(havuz)} aday yüklendi "
                  f"(aggTrades YENİDEN işlenmedi)", flush=True)
            return havuz
        except Exception as e:
            print(f"[Kapı] serap cache okunamadı ({e}) → yeniden üretilecek", flush=True)
    print("[Kapı] serap cache yok → aday havuzu aggTrades'ten üretiliyor (AĞIR, ay ay)…",
          flush=True)
    from oar_sampiyon_confirm import _analiz
    havuz = []
    for s in semboller:
        adaylar, _ = _analiz(s, bas, bit)
        havuz.extend(adaylar)
    return havuz


# ── Per-sembol Asia≥%1 gün seti (klines-only, aggTrades'siz → hızlı) ──
def _asia_1pct_gunler(sembol, bas, bit):
    """O sembolün Asia genliği ≥ %1 olan gün-index seti (ts // GUN_MS)."""
    k = _klines_oku(sembol, bas, bit)
    if k is None or k.empty:
        return set()
    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    OT_MIN, OT_MAX = 1_400_000_000_000, 2_000_000_000_000
    k = k[(k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)]
    k["gun"] = (k["open_time"] // GUN_MS).astype("int64")
    k["saat"] = (k["open_time"] % GUN_MS) / SAAT_MS
    asia = k[(k["saat"] >= ASIA_BAS_UTC) & (k["saat"] < ASIA_BIT_UTC)]
    gunler = set()
    for gun, ag in asia.groupby("gun"):
        a_h = float(ag["high"].max()); a_l = float(ag["low"].min())
        if a_l > 0 and (a_h - a_l) / a_l * 100.0 >= KAPI_ESIK:
            gunler.add(int(gun))
    return gunler


def _kayitlar(trades):
    """_filtre çıktısından (ts, pct) listesi (metrik + equity için)."""
    return [(c["ts"], c["pct"]) for c in trades]


def _metrik_ozet(kayitlar):
    m = _senaryo_metrik(kayitlar)
    if not m:
        return None
    eq = {str(k): _equity_sim(kayitlar, 1000.0, float(k)) for k in (1, 3, 5)}
    return {"n": m["n"], "wr": m["wr"], "pf": m["pf"], "beklenti": m["beklenti"],
            "maxdd": m["maxdd"], "toplam": m["toplam"], "oos_beklenti": m["oos_beklenti"],
            "equity": eq}


def analiz(semboller, bas, bit, taze=False):
    from oar_kesif import _filtre
    havuz = _havuz_yukle(semboller, bas, bit, taze)
    if not havuz:
        print("⚠ havuz boş — parquet/aggTrades ya da serap cache eksik.", flush=True)
        return None

    # Sembol başına Asia≥%1 gün seti (klines) → iki-kapı için kesişim
    pct1 = {s: _asia_1pct_gunler(s, bas, bit) for s in semboller}
    for s in semboller:
        print(f"[Kapı] {s}: Asia≥%1 gün sayısı {len(pct1[s])}", flush=True)
    ortak_gun = set.intersection(*pct1.values()) if pct1 else set()
    print(f"[Kapı] İKİ-KAPI (hepsi ≥%1 aynı gün) ortak gün sayısı: {len(ortak_gun)}",
          flush=True)

    stiller = json.loads(PORTFOY.read_text(encoding="utf-8")).get("stiller", [])
    sonuc = {"tarih": datetime.now(timezone.utc).isoformat(), "aralik": f"{bas}..{bit}",
             "semboller": semboller, "esik_pct": KAPI_ESIK,
             "asia_1pct_gun": {s: len(pct1[s]) for s in semboller},
             "iki_kapi_ortak_gun": len(ortak_gun), "sampiyonlar": []}

    for st in stiller:
        bloklar = st.get("bloklar", [])
        trades = _filtre(havuz, bloklar)
        kayit = {"stil": st.get("stil"), "bloklar": bloklar, "semboller": {}}
        for s in semboller:
            s_trades = [c for c in trades if c.get("_sembol") == s]
            per = _metrik_ozet(_kayitlar(s_trades))                       # A) per-sembol
            iki = _metrik_ozet(_kayitlar([c for c in s_trades
                                          if c.get("_gun") in ortak_gun]))  # B) iki-kapı
            kayit["semboller"][s] = {"per_sembol_kapi": per, "iki_kapi": iki}
        sonuc["sampiyonlar"].append(kayit)

    return sonuc


# ── Rapor ──
def _satir(ad, m):
    if not m:
        return f"    {ad}: yetersiz veri"
    e = m["equity"]
    def eqs(k):
        d = e[k]
        return (f"💀({d['likide_tarih']})" if d["likide"]
                else f"${d['son']:,.0f}(mDD%{d['maxdd']})")
    return (f"    {ad}: n{m['n']} WR%{m['wr']} PF {m['pf']} bek {m['beklenti']:+.3f}% "
            f"maxDD%{m['maxdd']} OOS{('%+.3f' % m['oos_beklenti']) if m['oos_beklenti'] is not None else '—'}"
            f" | 1x {eqs('1')} 3x {eqs('3')} 5x {eqs('5')}")


def rapor_metni(sonuc):
    L = ["═══ MARKET KAPISI: PER-SEMBOL vs İKİ-KAPI (şampiyon backtest) ═══",
         f"Aralık {sonuc['aralik']} · eşik Asia ≥%{sonuc['esik_pct']} · "
         f"semboller {','.join(sonuc['semboller'])}",
         f"Asia≥%1 gün: " + " · ".join(f"{s} {n}" for s, n in sonuc["asia_1pct_gun"].items())
         + f" · İKİ-KAPI ortak gün {sonuc['iki_kapi_ortak_gun']}",
         "  A) PER-SEMBOL KAPI = önerilen (her coin kendi Asia≥%1)",
         "  B) İKİ-KAPI = mevcut canlı (BTC VE ETH aynı gün ≥%1)", ""]
    for sp in sonuc["sampiyonlar"]:
        L.append(f"▓ ŞAMPİYON [{sp['stil']}] = {'+'.join(sp['bloklar'])}")
        for s, m in sp["semboller"].items():
            L.append(f"  {s}:")
            L.append(_satir("A) per-sembol ", m["per_sembol_kapi"]))
            L.append(_satir("B) iki-kapı   ", m["iki_kapi"]))
            a, b = m["per_sembol_kapi"], m["iki_kapi"]
            if a and b:
                dn = a["n"] - b["n"]
                db = a["beklenti"] - b["beklenti"]
                L.append(f"    → İKİ-KAPI atılan işlem: {dn} ({dn/a['n']*100:.0f}%) · "
                         f"beklenti farkı A−B {db:+.3f}%")
        L.append("")
    L.append("NOT: backtest ZATEN per-sembol kapıyla üretildi (asia_gecerli her sembolü "
             "kendi range'iyle süzer). İki-kapı yalnız canlı yolda; A sütunu = kanıtlı "
             "şampiyon istatistiğinin ta kendisi. Karar için A vs B'yi kıyasla.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--taze", action="store_true", help="serap cache'i yok say, sıfırdan üret")
    args = ap.parse_args()
    semboller = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]

    sonuc = analiz(semboller, args.bas, args.bit, taze=args.taze)
    if sonuc is None:
        return
    metin = rapor_metni(sonuc)
    print("\n" + metin, flush=True)
    SONUC.write_text(json.dumps(sonuc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ Kalıcı sonuç yazıldı: {SONUC.name} (commit+push edilince lider+Claude okur)",
          flush=True)

    if args.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            asyncio.run(bildir("Kapı Analizi", "backtest",
                               "Market kapısı: per-sembol vs iki-kapı şampiyon backtest",
                               metin[:3500]))
            print("✓ Telegram'a raporlandı", flush=True)
        except Exception as e:
            print(f"⚠ Telegram gönderilemedi: {e}", flush=True)


if __name__ == "__main__":
    main()
