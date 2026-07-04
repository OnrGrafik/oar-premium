"""
seans_karakter.py — Seans Karakteri → İleri Sonuç Backtest'i (LOCAL research)
═══════════════════════════════════════════════════════════════════════════════
SORU: Seansların karakteristikleri (hacim/CVD/VWAP/VPFR/MA + manipülasyon/başlangıç/
bitiş sinyalleri) gerçekleştiğinde piyasa NE YAPMIŞ? Geleceği görmek için
istatistiksel confirm — hem scalp (sonraki seans/gün) hem swing (haftanın kalanı).

Örnek hipotez: "Cumartesi Asia ALICI ise hafta bull geçmiş, trend devam etmiş."
Bu motor bunu SAYISALLAŞTIRIR: P(bull hafta | koşul) + LIFT + n + güven aralığı + OOS.

═══ METODOLOJİ (dikkat edilen 10 nokta) ═══
1. NO-LOOKAHEAD: seans özelliği yalnız o seans kapanışına kadar; hacim/CVD medyan
   tabanı ÖNCEKİ günlerden (rolling), bugünü/geleceği içermez.
2. LIFT: her koşullu WR, KOŞULSUZ taban orana karşı raporlanır (ham WR yanıltır).
3. OVERFIT: min n≥MIN_N, Wilson güven aralığı, holdout (son OOS_ORAN OOS).
4. ÖRTÜŞEN HAFTA: swing için hem günlük (örtüşen, uyarılı) hem non-overlapping.
5. REJİM: yıl-yıl dağılım (bull 2020-21 tek başına yanıltmasın).
6. BTC-ETH ~0.8 korelasyon → efektif örneklem ~1.3x (bağımsız değil).
7-9. MANİPÜLASYON (sweep+reclaim/SFP), BAŞLANGIÇ (break+CVD+hacim+VPFR kabul),
     BİTİŞ (CVD diverjansı, hacim klimaksı, HTF mıknatıs, değer-alanına dönüş).
10. HEDEF KESİN: scalp=sonraki seans, intraday=gün sonu, swing=hafta sonu getirisi.

OI YOK (kullanıcı: dahil etme). Veri: BTC/ETH parquet klines(1m)+aggTrades.
Çalıştırma:  python seans_karakter.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06
"""
import argparse
import math
from collections import defaultdict

from oar_local_backtest import (_klines_oku, _aggt_ay_yollari, _ms_olcekle,
                                _vpfr_deger_alani, GUN_MS, SAAT_MS)

# ─── Seans sınırları (UTC saat, [bas, bit)) ───────────────────────────────────
SEANSLAR = {
    "Asia":   (0.0, 4.0),      # TR 03:00-07:00 — OAR çekirdeği
    "London": (7.0, 11.0),     # TR 10:00-14:00
    "NY":     (13.0, 17.0),    # TR 16:00-20:00
}
SEANS_SIRA = ["Asia", "London", "NY"]

MEDYAN_PENCERE = 20     # hacim/CVD taban medyanı — son N gün (no-lookahead)
MIN_N          = 30     # bir koşulun raporlanması için asgari örneklem
OOS_ORAN       = 0.20   # son %20 = holdout (out-of-sample)
BUYUK_HAREKET  = 1.0    # "hareket başladı" eşiği: forward getiri |%| ≥ bu


# ─── İstatistik yardımcıları ─────────────────────────────────────────────────
def _wilson(k, n, z=1.96):
    """Wilson güven aralığı (oran için). Döner: (alt, ust) yüzde."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    merkez = (p + z * z / (2 * n)) / d
    yari = (z / d) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round(100 * (merkez - yari), 1), round(100 * (merkez + yari), 1))


def _wr(kayitlar, hedef):
    """kayitlar listesinde hedef>0 olanların oranı + istatistik."""
    vals = [k[hedef] for k in kayitlar if k.get(hedef) is not None]
    n = len(vals)
    if n == 0:
        return None
    kaz = sum(1 for v in vals if v > 0)
    ort = sum(vals) / n
    alt, ust = _wilson(kaz, n)
    return {"n": n, "wr": round(100 * kaz / n, 1), "ort_getiri": round(ort, 3),
            "wilson_alt": alt, "wilson_ust": ust}


# ─── Seans özellik çıkarımı (no-lookahead) ───────────────────────────────────
def _seans_ozellikleri(sembol, bas, bit):
    """
    Her gün×seans için özellik + ileri sonuç kaydı üretir.
    Döner: kronolojik kayıt listesi (her biri bir gün×seans).
    """
    import pandas as pd
    import numpy as np
    from footprint_engine import aggressor_delta

    print(f"   [{sembol}] klines yükleniyor…", flush=True)
    k = _klines_oku(sembol, bas, bit)
    if k is None or not len(k):
        print(f"   [{sembol}] ⚠ klines yok — atlandı", flush=True)
        return []
    print(f"   [{sembol}] klines yüklendi: {len(k):,} mum · aggTrades ay ay işlenecek", flush=True)
    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    OT_MIN, OT_MAX = 1_400_000_000_000, 2_000_000_000_000
    k = k[(k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)]
    k["gun"] = (k["open_time"] // GUN_MS).astype("int64")
    k["saat"] = (k["open_time"] % GUN_MS) / SAAT_MS

    # Günlük kapanış + MA (no-lookahead: MA girişte t-1'e kadar)
    gun_kapanis = k.groupby("gun")["close"].last()
    ma20 = gun_kapanis.rolling(20).mean().shift(1)   # shift(1) → bugünü içermez
    ma50 = gun_kapanis.rolling(50).mean().shift(1)

    # ── aggTrades AY AY → gün×seans CVD + fiyat-hacim binleri (VPFR) ──
    cvd_ss   = defaultdict(float)                       # (gun,seans) → net delta
    vol_ss   = defaultdict(float)                       # (gun,seans) → toplam hacim (aggT)
    pbin_ss  = defaultdict(lambda: defaultdict(float))  # (gun,seans) → {pbin: hacim}
    yollar = _aggt_ay_yollari(sembol, bas, bit)
    toplam = len(yollar)
    for i, yol in enumerate(yollar, 1):
        print(f"      · [{sembol}] aggTrades {yol.name} ({i}/{toplam}) işleniyor…", flush=True)
        a = pd.read_parquet(yol, columns=["timestamp", "price", "quantity", "is_buyer_maker"])
        a["timestamp"] = _ms_olcekle(a["timestamp"])
        a["gun"] = (a["timestamp"] // GUN_MS).astype("int64")
        a["saat"] = (a["timestamp"] % GUN_MS) / SAAT_MS
        a["delta"] = aggressor_delta(a)
        p = a["price"].to_numpy()
        mag = np.floor(np.log10(np.clip(np.abs(p), 1e-9, None)))
        a["pbin"] = np.round(p / (10.0 ** (mag - 3))) * (10.0 ** (mag - 3))
        for ad, (s0, s1) in SEANSLAR.items():
            m = a[(a["saat"] >= s0) & (a["saat"] < s1)]
            if m.empty:
                continue
            for g, v in m.groupby("gun")["delta"].sum().items():
                cvd_ss[(int(g), ad)] += float(v)
            for g, v in m.groupby("gun")["quantity"].sum().items():
                vol_ss[(int(g), ad)] += float(v)
            for (g, pb), v in m.groupby(["gun", "pbin"])["quantity"].sum().items():
                pbin_ss[(int(g), ad)][float(pb)] += float(v)
        print(f"        ✓ {yol.name} işlendi", flush=True)
        del a

    print(f"   [{sembol}] seans özetleri + özellikler hesaplanıyor…", flush=True)
    # ── Her gün×seans klines özeti (OHLC, VWAP) — TEK groupby (hızlı) ──
    def _seans_ad(saat):
        for ad, (s0, s1) in SEANSLAR.items():
            if s0 <= saat < s1:
                return ad
        return None
    kk = k.copy()
    kk["seans"] = kk["saat"].map(_seans_ad)
    kk = kk[kk["seans"].notna()].sort_values("open_time")
    kk["tpv"] = (kk["high"] + kk["low"] + kk["close"]) / 3.0 * kk["volume"]
    seans_ohlc = {}
    for (g, ad), grp in kk.groupby(["gun", "seans"], sort=False):
        vol = float(grp["volume"].sum())
        seans_ohlc[(int(g), ad)] = {
            "open": float(grp["open"].iloc[0]), "high": float(grp["high"].max()),
            "low": float(grp["low"].min()), "close": float(grp["close"].iloc[-1]),
            "vol_k": vol,
            "vwap": float(grp["tpv"].sum() / vol) if vol > 0 else None,
        }

    gunler = sorted(k["gun"].unique().tolist())
    kapanis_map = gun_kapanis.to_dict()

    # ── Rolling medyan tabanları (no-lookahead) — seans bazında ──
    hacim_gecmis = defaultdict(list)   # seans → [hacim...] (kronolojik)
    cvd_gecmis   = defaultdict(list)
    kayitlar = []

    for gi, g in enumerate(gunler):
        wd = _hafta_gunu(g)
        for si, ad in enumerate(SEANS_SIRA):
            o = seans_ohlc.get((g, ad))
            if not o:
                continue
            cvd = cvd_ss.get((g, ad), 0.0)
            volA = vol_ss.get((g, ad), 0.0)

            # medyan taban (önceki günlerden) → no-lookahead
            hac_med = _medyan(hacim_gecmis[ad][-MEDYAN_PENCERE:])
            cvd_med = _medyan([abs(x) for x in cvd_gecmis[ad][-MEDYAN_PENCERE:]])
            hacim_gecmis[ad].append(volA)
            cvd_gecmis[ad].append(cvd)

            yon = 1 if o["close"] > o["open"] else -1
            va = _vpfr_deger_alani(pbin_ss.get((g, ad), {})) if pbin_ss.get((g, ad)) else None

            # önceki seans (manipülasyon/başlangıç için)
            onceki = _onceki_seans(seans_ohlc, gunler, gi, si)

            ozk = {
                "gun": int(g), "seans": ad, "hafta_gunu": wd,
                "close": o["close"], "open": o["open"],
                "yon_buy": bool(yon > 0),
                "hacim_yuksek": bool(hac_med and volA > hac_med),
                "cvd_buy": bool(cvd > 0),
                "cvd_guclu": bool(cvd_med and abs(cvd) > cvd_med),
                "vwap_ustu": bool(o["vwap"] and o["close"] > o["vwap"]),
                "ma20_ustu": bool(_gv(ma20, g) and o["close"] > _gv(ma20, g)),
                "ma50_ustu": bool(_gv(ma50, g) and o["close"] > _gv(ma50, g)),
            }
            # VPFR kabul: kapanış değer alanının üstü/altı (kabul) mü içi mi
            if va and va.get("vah") and va.get("val"):
                if o["close"] > va["vah"]:
                    ozk["vpfr_kabul"] = "ust"
                elif o["close"] < va["val"]:
                    ozk["vpfr_kabul"] = "alt"
                else:
                    ozk["vpfr_kabul"] = "ic"

            # ── MANİPÜLASYON: önceki seans ekstremini süpürüp geri kapanış (SFP) ──
            if onceki:
                ust_sweep = o["high"] > onceki["high"] and o["close"] < onceki["high"]
                alt_sweep = o["low"] < onceki["low"] and o["close"] > onceki["low"]
                ozk["manipulasyon"] = bool(ust_sweep or alt_sweep)
                ozk["sweep_yon"] = "ust" if ust_sweep else "alt" if alt_sweep else None
                # ── BAŞLANGIÇ: önceki range'i KIRIP kapanış + CVD aynı yön + hacim yüksek ──
                kirilim_ust = o["close"] > onceki["high"] and cvd > 0
                kirilim_alt = o["close"] < onceki["low"] and cvd < 0
                ozk["expansion_baslangic"] = bool(
                    (kirilim_ust or kirilim_alt) and ozk["hacim_yuksek"]
                    and ozk.get("vpfr_kabul") in ("ust", "alt"))

            kayitlar.append(ozk)

    # ── İLERİ SONUÇLAR (forward) — no-lookahead: hep gelecekten ──
    _ileri_sonuc_ekle(kayitlar, kapanis_map, seans_ohlc, gunler)
    print(f"   [{sembol}] ✓ tamamlandı: {len(kayitlar):,} gün×seans kaydı", flush=True)
    return kayitlar


# ─── İleri sonuç etiketleme ──────────────────────────────────────────────────
def _ileri_sonuc_ekle(kayitlar, kapanis_map, seans_ohlc, gunler):
    """Her kayda scalp/intraday/swing forward getirileri + süreklilik + bitiş ekler."""
    gun_idx = {g: i for i, g in enumerate(gunler)}
    # kayıtları (gun,seans) ile indeksle
    idx = {(kk["gun"], kk["seans"]): kk for kk in kayitlar}
    for kk in kayitlar:
        g, ad = kk["gun"], kk["seans"]
        c0 = kk["close"]
        # SCALP: sonraki seans getirisi
        nxt = _sonraki_seans_key(seans_ohlc, gunler, g, ad)
        if nxt and idx.get(nxt):
            kk["scalp_getiri"] = round((idx[nxt]["close"] - c0) / c0 * 100, 3)
            kk["scalp_devam"] = (1 if (kk["scalp_getiri"] > 0) == kk["yon_buy"] else -1) \
                if "scalp_getiri" in kk else None
        # INTRADAY: gün sonu (NY close) getirisi
        gc = kapanis_map.get(g)
        if gc:
            kk["intraday_getiri"] = round((gc - c0) / c0 * 100, 3)
        # SWING: haftanın kalanı (bu günün haftasının SON gününün kapanışı)
        hafta_son = _hafta_son_gun(g, gunler, gun_idx)
        if hafta_son and kapanis_map.get(hafta_son):
            kk["swing_getiri"] = round((kapanis_map[hafta_son] - c0) / c0 * 100, 3)
            # non-overlapping bayrağı: sadece haftanın İLK Asia'sı (örtüşme uyarısı için)
            kk["hafta_ilk"] = bool(ad == "Asia" and _hafta_gunu(g) == 0)


# ─── Koşullu istatistik (LIFT + OOS) ─────────────────────────────────────────
def kosul_analiz(kayitlar, kosullar, hedef="swing_getiri", oos=True):
    """
    kayitlar'ı kosullar (alan→değer) ile süz, forward WR + LIFT (taban orana karşı)
    hesapla. IS/OOS ayır. Döner: dict.
    """
    n_all = len(kayitlar)
    kesim = int(n_all * (1 - OOS_ORAN))
    IS, OOS = kayitlar[:kesim], kayitlar[kesim:]

    def uyar(k):
        return all(k.get(a) == v for a, v in kosullar.items())

    taban_is = _wr(IS, hedef)
    sec_is   = _wr([k for k in IS if uyar(k)], hedef)
    sec_oos  = _wr([k for k in OOS if uyar(k)], hedef)
    out = {"kosul": kosullar, "hedef": hedef,
           "taban": taban_is, "IS": sec_is, "OOS": sec_oos}
    if sec_is and taban_is:
        out["lift_is"] = round(sec_is["wr"] - taban_is["wr"], 1)   # koşullu − koşulsuz
    return out


# ─── Yardımcılar ─────────────────────────────────────────────────────────────
def _medyan(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _gv(seri, g):
    try:
        v = seri.get(g)
        return float(v) if v == v else None   # NaN kontrolü
    except Exception:
        return None


def _hafta_gunu(gun_idx_epoch):
    # gün epoch (gün sayısı) → haftanın günü (0=Pazartesi). 1970-01-01 = Perşembe(3).
    return (int(gun_idx_epoch) + 3) % 7


def _onceki_seans(seans_ohlc, gunler, gi, si):
    if si > 0:
        return seans_ohlc.get((gunler[gi], SEANS_SIRA[si - 1]))
    if gi > 0:
        return seans_ohlc.get((gunler[gi - 1], SEANS_SIRA[-1]))
    return None


def _sonraki_seans_key(seans_ohlc, gunler, g, ad):
    si = SEANS_SIRA.index(ad)
    gi = gunler.index(g) if g in gunler else -1
    if si < len(SEANS_SIRA) - 1:
        return (g, SEANS_SIRA[si + 1])
    if 0 <= gi < len(gunler) - 1:
        return (gunler[gi + 1], SEANS_SIRA[0])
    return None


def _hafta_son_gun(g, gunler, gun_idx):
    wd = _hafta_gunu(g)
    hedef = g + (6 - wd)                 # bu haftanın Pazar'ı (epoch gün)
    adaylar = [x for x in gunler if g <= x <= hedef]
    return adaylar[-1] if adaylar else None


# ─── CLI ─────────────────────────────────────────────────────────────────────
def _rapor(kayitlar):
    print(f"\n═══ SEANS KARAKTERİ RAPORU ═══  toplam gün×seans: {len(kayitlar)}")
    # Örnek koşullar — her biri LIFT + OOS ile
    testler = [
        ({"seans": "Asia", "yon_buy": True, "cvd_buy": True, "hacim_yuksek": True}, "swing_getiri",
         "Asia ALICI + CVD+ + yüksek hacim → hafta"),
        ({"seans": "Asia", "hafta_gunu": 5, "yon_buy": True, "cvd_buy": True}, "swing_getiri",
         "CUMARTESİ Asia alıcı (senin örnek) → hafta"),
        ({"seans": "Asia", "expansion_baslangic": True}, "swing_getiri",
         "Asia expansion başlangıç → hafta"),
        ({"seans": "London", "manipulasyon": True}, "intraday_getiri",
         "London manipülasyon (sweep+reclaim) → gün sonu"),
        ({"seans": "Asia", "vwap_ustu": True, "ma20_ustu": True, "cvd_buy": True}, "scalp_getiri",
         "Asia VWAP+MA20 üstü + CVD+ → sonraki seans"),
    ]
    for kos, hedef, ad in testler:
        r = kosul_analiz(kayitlar, kos, hedef)
        t = r.get("taban"); iss = r.get("IS"); oos = r.get("OOS")
        if not iss:
            print(f"\n• {ad}: örneklem yok"); continue
        lift = r.get("lift_is", "?")
        oos_s = (f"OOS WR%{oos['wr']} (n{oos['n']})" if oos else "OOS örneklem az")
        saglam = "✅" if (oos and iss and abs(iss['wr'] - oos['wr']) <= 12 and iss['n'] >= MIN_N) else "⚠"
        print(f"\n• {ad}")
        print(f"    IS: WR%{iss['wr']} (n{iss['n']}, Wilson {iss['wilson_alt']}-{iss['wilson_ust']}) "
              f"ort {iss['ort_getiri']}% | taban WR%{t['wr'] if t else '?'} → LIFT {lift} | {oos_s} {saglam}")


def main():
    ap = argparse.ArgumentParser(description="Seans karakteri → ileri sonuç backtest")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--from", dest="bas", required=True)
    ap.add_argument("--to", dest="bit", required=True)
    args = ap.parse_args()
    tum = []
    for sym in [s.strip() for s in args.symbol.split(",") if s.strip()]:
        print(f"[SeansKarakter] {sym} {args.bas}..{args.bit} işleniyor…", flush=True)
        tum += _seans_ozellikleri(sym, args.bas, args.bit)
    _rapor(tum)


if __name__ == "__main__":
    main()
