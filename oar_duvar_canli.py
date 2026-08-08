"""
oar_duvar_canli.py — ZORUNLU AKIŞ DUVARLARI, CANLI (site Komuta Merkezi paneli)
════════════════════════════════════════════════════════════════════════════════════
GEX duvar tablosunun PERP karşılığı, canlı veriyle:
    satır = fiyat kovası (strike muadili)
    hücre = o fiyata gelinirse ZORLA kapanacak notional
    ÜST duvar = short likidasyonları → zorunlu ALIŞ  (Call Wall muadili)
    ALT duvar = long  likidasyonları → zorunlu SATIŞ (Put Wall muadili)

MATEMATİK `oar_likidasyon_haritasi.py` ile BİREBİR AYNI (orası backtest/doğrulama
sürümü, burası canlı sürüm). Denetimde bulunan üç kusur burada BAŞTAN doğru:
  ① Long/short PAYLAŞTIRILMAZ — perp'te tanım gereği long notional = short
     notional = OI. ΔOI X ise X yeni LONG **ve** X yeni SHORT doğar (bölünmez).
  ② OI AZALIŞI orantılı sönüm uygular (kapanan pozisyon haritadan düşer).
  ③ Likidasyona DEĞMİŞ pozisyon haritadan düşer (geçmiş fiyat yoluyla test).

⚠️ MODELDİR, ÖLÇÜM DEĞİL. Kamuya açık veride pozisyon başına giriş fiyatı ve
   kaldıraç YOK; OI değişimi + kaldıraç dağılımı varsayımıyla TAHMİN edilir
   (sektör standardı yaklaşım). Varsayımlar:
     • OI artışı = yeni pozisyon, o barın kapanışında açıldı
     • kaldıraç dağılımı KALDIRAC_DAGILIM (sabit varsayım — veri yok)
     • bakım teminatı sabit (gerçekte kademeli)
     • yeni OI'nin bir kısmı hedge'li olabilir → zorunlu akış bir miktar YUKARI tahmin
   Bu yüzden MUTLAK büyüklükten çok DUVARIN YERİ okunmalı.

Veri: OI geçmişi + mum verisi + güncel funding (hepsi canlı uçlardan, anahtarsız).
"""
import time

KALDIRAC_DAGILIM = {5: 0.15, 10: 0.30, 20: 0.25, 50: 0.20, 100: 0.10}
BAKIM_TEMINAT = 0.004      # %0.4 sabit varsayım
PENCERE_GUN = 7            # pozisyon ömrü penceresi
PERIYOT = "30m"            # 7 gün = 336 bar (tek istekte alınabilir)
PERIYOT_MS = 1_800_000
BIN_PCT = 0.25             # fiyat kovası genişliği (%)
BANT_PCT = 20.0            # ± bant
_CACHE = {}
_CACHE_TTL_S = 120


def _sonum(oi):
    """OI azalışından gelen 'bugüne kalan pay' çarpanı (bar hizasında, 0..1]."""
    kum, out = 1.0, []
    carpanlar = []
    for i, v in enumerate(oi):
        if i == 0:
            carpanlar.append(1.0)
            continue
        onceki = oi[i - 1]
        c = (v / onceki) if (v < onceki and onceki > 0) else 1.0
        carpanlar.append(min(max(c, 0.0), 1.0))
    for c in carpanlar:
        kum *= c
        out.append(kum)
    son = out[-1] if out else 1.0
    return [min(max(son / (x if x > 0 else 1e-12), 0.0), 1.0) for x in out]


def harita_kur(girisler, spot):
    """
    girisler: [{"px": giriş fiyatı, "notional": USD, "pay": hayatta kalan oran,
                "min": girişten şimdiye en düşük, "max": en yüksek}, ...]
    Döner: (alt[(fiyat, notional)], ust[(fiyat, notional)])
    """
    alt, ust = [], []
    for g in girisler:
        taban = g["notional"] * g["pay"]
        if taban <= 0:
            continue
        for L, w in KALDIRAC_DAGILIM.items():
            n = taban * w
            # LONG → aşağıda likide (zorunlu SATIŞ). Fiyat oraya DEĞDİYSE pozisyon öldü.
            lq = g["px"] * (1.0 - 1.0 / L + BAKIM_TEMINAT)
            if lq < spot and g["min"] > lq:
                alt.append((lq, n))
            # SHORT → yukarıda likide (zorunlu ALIŞ)
            lq = g["px"] * (1.0 + 1.0 / L - BAKIM_TEMINAT)
            if lq > spot and g["max"] < lq:
                ust.append((lq, n))
    return alt, ust


def kovala(alt, ust, spot, bant=BANT_PCT, bin_pct=BIN_PCT, satir=None):
    """Seviyeleri fiyat kovalarına topla + duvarları işaretle."""
    adim = spot * bin_pct / 100.0
    kova = {}
    for sev, yon in ((ust, "alis"), (alt, "satis")):
        for p, n in sev:
            if abs((p - spot) / spot * 100.0) > bant:
                continue
            b = round(round(p / adim) * adim, 2)
            h = kova.setdefault(b, {"fiyat": b, "alis": 0.0, "satis": 0.0})
            h[yon] += n
    satirlar = list(kova.values())
    for r in satirlar:
        r["toplam"] = r["alis"] + r["satis"]
        r["mesafe_pct"] = round((r["fiyat"] - spot) / spot * 100.0, 2)
        r["yon"] = "alis" if r["fiyat"] > spot else "satis"
        r["deger"] = round(r["alis"] if r["fiyat"] > spot else r["satis"])
    if satir:
        satirlar = sorted(satirlar, key=lambda r: -r["toplam"])[:satir]
    satirlar.sort(key=lambda r: -r["fiyat"])

    ustler = [r for r in satirlar if r["fiyat"] > spot]
    altlar = [r for r in satirlar if r["fiyat"] < spot]
    ud = max(ustler, key=lambda r: r["alis"], default=None)
    ad = max(altlar, key=lambda r: r["satis"], default=None)
    for r in satirlar:
        r["duvar"] = "UST" if r is ud else ("ALT" if r is ad else "")
    return satirlar, ud, ad


async def duvar(sembol: str = "BTCUSDT", satir: int = 24):
    """Canlı zorunlu-akış duvar tablosu. 120s cache."""
    ck = (sembol, satir)
    c = _CACHE.get(ck)
    if c and (time.time() - c["ts"]) < _CACHE_TTL_S:
        return c["veri"]

    from exchange_client import klines, open_interest
    bar = int(PENCERE_GUN * 24 * 60 * 60 * 1000 / PERIYOT_MS)   # 336
    try:
        oi_ham = await open_interest(sembol, PERIYOT, min(bar, 500))
        kl = await klines(sembol, PERIYOT, min(bar, 500), futures=True)
    except Exception as e:
        return {"durum": "veri_yok", "not": str(e)[:90]}
    if not oi_ham or not kl or len(kl) < 10:
        return {"durum": "veri_yok", "not": "OI/mum verisi gelmedi"}

    # mumları ts→(close, high, low) sözlüğüne al, OI barlarıyla eşle
    mum = {int(r[0]): (float(r[4]), float(r[2]), float(r[3])) for r in kl}
    ts_sirali = sorted(mum)
    eslesen = [(int(o["timestamp"]), float(o["oi"])) for o in oi_ham
               if int(o["timestamp"]) in mum]
    eslesen.sort()
    if len(eslesen) < 10:
        return {"durum": "veri_yok", "not": "OI ile mum zaman damgaları eşleşmedi"}

    spot = float(mum[ts_sirali[-1]][0])
    oi_seri = [v for _, v in eslesen]
    paylar = _sonum(oi_seri)

    # her bardan SONRAKİ en düşük/en yüksek (girişten şimdiye) — likidasyon değdi mi
    n = len(eslesen)
    sfx_min = [0.0] * n
    sfx_max = [0.0] * n
    mn, mx = float("inf"), 0.0
    for i in range(n - 1, -1, -1):
        _, hi, lo = mum[eslesen[i][0]]
        mn = min(mn, lo); mx = max(mx, hi)
        sfx_min[i], sfx_max[i] = mn, mx

    girisler = []
    for i in range(1, n):
        d = oi_seri[i] - oi_seri[i - 1]
        if d <= 0:                       # yalnız ARTIŞ yeni pozisyondur
            continue
        px = float(mum[eslesen[i][0]][0])
        girisler.append({"px": px, "notional": d * px, "pay": paylar[i],
                         "min": sfx_min[i], "max": sfx_max[i]})
    if not girisler:
        return {"durum": "veri_yok", "not": "pencerede OI artışı yok"}

    alt, ust = harita_kur(girisler, spot)
    satirlar, ud, ad = kovala(alt, ust, spot, satir=satir)

    funding = None
    try:
        from exchange_client import funding_rate
        fr = await funding_rate(sembol)
        if fr is not None:
            funding = {"oran_pct": round(float(fr) * 100, 5),
                       "yillik_pct": round(float(fr) * 3 * 365 * 100, 2),
                       "kalabalik": "LONG" if fr > 0 else ("SHORT" if fr < 0 else "NÖTR")}
    except Exception:
        funding = None

    veri = {
        "durum": "ok", "sembol": sembol, "spot": spot,
        "ts": int(ts_sirali[-1]), "pencere_gun": PENCERE_GUN,
        "satirlar": satirlar, "ust_duvar": ud, "alt_duvar": ad, "funding": funding,
        "toplam_alis": round(sum(r["alis"] for r in satirlar)),
        "toplam_satis": round(sum(r["satis"] for r in satirlar)),
        "not": "Model — pozisyon giriş fiyatı/kaldıracı kamuya açık değil; "
               "açık pozisyon değişiminden tahmin edilir. Duvarın YERİ, mutlak "
               "büyüklüğünden daha güvenilirdir.",
    }
    _CACHE[ck] = {"ts": time.time(), "veri": veri}
    return veri


# ── kendi kendini sınama (ağ gerekmez) ────────────────────────────────────────
def _kendi_test():
    # ① sönüm: OI yarıya inince o ana kadarki pay 0.5
    p = _sonum([100.0, 100.0, 50.0, 50.0])
    assert abs(p[0] - 0.5) < 1e-9 and abs(p[-1] - 1.0) < 1e-9, p
    assert all(abs(x - 1.0) < 1e-9 for x in _sonum([10.0, 10.0, 10.0]))
    # ② aynı ΔOI hem long hem short üretir (paylaştırma YOK).
    #    Hiçbir seviyeye değilmemiş dar aralık seçilir → tüm kaldıraç kovaları yaşar,
    #    iki taraf da TAM notional gösterir (bölünmediğinin kanıtı).
    g = [{"px": 100.0, "notional": 1000.0, "pay": 1.0, "min": 99.9, "max": 100.1}]
    alt, ust = harita_kur(g, spot=100.0)
    assert abs(sum(n for _, n in alt) - 1000.0) < 1e-6, alt
    assert abs(sum(n for _, n in ust) - 1000.0) < 1e-6, ust
    # ③ likidasyona DEĞMİŞ kova düşer: dip 95 → 20x/50x/100x long ölür, 5x+10x kalır
    g2 = [{"px": 100.0, "notional": 1000.0, "pay": 1.0, "min": 95.0, "max": 100.1}]
    alt2, _ = harita_kur(g2, spot=100.0)
    kalan = sum(n for _, n in alt2)
    assert abs(kalan - 450.0) < 1e-6, f"beklenen 450 (5x+10x), çıkan {kalan}"
    assert all(lq < 95.0 for lq, _ in alt2), alt2
    # ④ sönüm notional'a uygulanır
    g3 = [{"px": 100.0, "notional": 1000.0, "pay": 0.5, "min": 99.9, "max": 100.1}]
    a3, _ = harita_kur(g3, spot=100.0)
    assert abs(sum(n for _, n in a3) - 500.0) < 1e-6, a3
    # ⑤ duvarlar doğru tarafta
    sat, ud, ad = kovala(alt, ust, 100.0)
    assert ud and ud["fiyat"] > 100.0 and ad and ad["fiyat"] < 100.0
    print("[oar_duvar_canli] ✓ sönüm · paylaştırma YOK (iki taraf tam) ·"
          " likidasyona değen kova düşüyor · duvarlar doğru tarafta")
    return True


if __name__ == "__main__":
    _kendi_test()
