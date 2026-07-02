"""
Makro Ekonomi Motoru — OAR Premium v3
═══════════════════════════════════════════════════════════════════
Vercel macro.js'in Python'a taşınmış hali. TÜM kaynaklar ÜCRETSİZ:
  • BLS flat file (KEYSİZ) → CPI
  • FRED (env key opsiyonel) → çoğu seri
  • US Treasury Fiscal Data (KEYSİZ) → Fed faiz
  • Yahoo Finance (KEYSİZ) → carry trade (USD/JPY, VIX, Nikkei)
  • Kaynak çökerse HARDCODED fallback YOK → 'veri yok' işaretlenir (yanıltma önlenir)

9 gösterge + BTC etki yorumu + Fed SEP + carry trade.
Render'da 5 dk cache → RAM/disk yükü minimal.
"""
import os, httpx, asyncio, math
from datetime import datetime, timezone

HDR = {"Accept": "application/json", "User-Agent": "MacroDeskBot/9.0"}
TO = 12.0
FRED_KEY = os.environ.get("FRED_API_KEY", "")
BLS_KEY = os.environ.get("BLS_API_KEY", "")
FRED = "https://api.stlouisfed.org/fred/series/observations"

_cache = {"data": None, "ts": 0}

async def _gfetch(cl, url, hdr=None):
    try:
        r = await cl.get(url, headers=hdr or HDR, timeout=TO)
        if r.status_code != 200: return {"__err": f"HTTP {r.status_code}"}
        ct = r.headers.get("content-type", "")
        return r.json() if "json" in ct else r.text
    except Exception as e:
        return {"__err": str(e)[:40]}

async def _fred(cl, series, limit=24):
    if not FRED_KEY: return None
    url = f"{FRED}?series_id={series}&api_key={FRED_KEY}&file_type=json&sort_order=desc&limit={limit}"
    d = await _gfetch(cl, url)
    if not d or isinstance(d, str) or d.get("__err") or not d.get("observations"): return None
    rows = [{"tarih": o["date"], "deger": float(o["value"])}
            for o in d["observations"] if o["value"] != "."]
    return list(reversed(rows))

def _trend(arr):
    if not arr or len(arr) < 2: return "belirsiz"
    n = len(arr); ort = sum(arr)/n
    pay = sum((i-(n-1)/2)*(v-ort) for i, v in enumerate(arr))
    payda = sum((i-(n-1)/2)**2 for i in range(n))
    egim = pay/payda if payda else 0
    pct = abs(egim/(abs(ort) or 1))*100
    if pct < 0.05: return "sabit"
    return "yukari" if egim > 0 else "asagi"

def _veri_yok(kaynak="⚠ Kaynak erişilemedi"):
    """Canlı kaynak çöktüğünde ESKİ hardcoded değeri güncelmiş gibi gösterme —
    'veri yok' işaretle (yanıltmayı önle). guncel=None → yorum/trend atlar."""
    return {"guncel": None, "onceki": None, "degisim": None, "gecmis": [],
            "trend": "belirsiz", "kaynak": kaynak, "veri_yok": True, "fallback": True}


def _sonuc(rows, **ekstra):
    if not rows: return None
    s = rows[-6:]
    son = s[-1]; onc = s[-2] if len(s) >= 2 else None
    return {"guncel": son["deger"], "tarih": son["tarih"],
            "onceki": onc["deger"] if onc else None,
            "degisim": round(son["deger"]-onc["deger"], 3) if onc else None,
            "gecmis": s, "trend": _trend([d["deger"] for d in s]), **ekstra}

async def _cpi(cl):
    # BLS flat file (keysiz)
    try:
        r = await cl.get("https://download.bls.gov/pub/time.series/cu/cu.data.1.AllItems",
                         headers={**HDR, "Accept": "text/plain"}, timeout=TO)
        if r.status_code == 200:
            lines = [l for l in r.text.split("\n") if l.startswith("CUSR0000SA0")]
            parsed = []
            for l in lines:
                p = l.split()
                if len(p) >= 4 and p[2].startswith("M") and p[2] != "M13":
                    try: parsed.append({"tarih": f"{p[1]}-{p[2][1:].zfill(2)}", "deger": float(p[3])})
                    except Exception: pass
            parsed.sort(key=lambda x: x["tarih"]); parsed = parsed[-14:]
            if len(parsed) >= 2:
                son, onc = parsed[-1]["deger"], parsed[-2]["deger"]
                yil = parsed[-13]["deger"] if len(parsed) >= 13 else None
                return _sonuc(parsed, kaynak="BLS flat file",
                    degisim=round((son-onc)/onc*100, 2),
                    yillik=round((son-yil)/yil*100, 2) if yil else None)
    except Exception: pass
    rows = await _fred(cl, "CPIAUCSL", 14)
    if rows and len(rows) >= 13:
        son, onc, yil = rows[-1]["deger"], rows[-2]["deger"], rows[-13]["deger"]
        return _sonuc(rows, kaynak="FRED", degisim=round((son-onc)/onc*100, 2), yillik=round((son-yil)/yil*100, 2))
    return _veri_yok()

async def _basit_fred(cl, series, fb_key, **fb_extra):
    rows = await _fred(cl, series, 14)
    if rows: return _sonuc(rows, kaynak="FRED")
    return _veri_yok()

async def _nfp(cl):
    """
    NFP (tarım dışı istihdam) CANLI: FRED PAYEMS toplam istihdam SEVİYESİNİN
    aylık FARKI = manşet NFP değişimi (bin kişi). FRED, BLS açıklamasını ~1 gün
    içinde yansıtır. FRED yoksa fallback.
    """
    rows = await _fred(cl, "PAYEMS", 15)   # seviye (bin kişi)
    if rows and len(rows) >= 2:
        chg = [{"tarih": rows[i]["tarih"], "deger": round(rows[i]["deger"] - rows[i - 1]["deger"])}
               for i in range(1, len(rows))]
        return _sonuc(chg, kaynak="FRED (PAYEMS aylık değişim)")
    return _veri_yok()


async def _fedfaiz(cl):
    rows = await _fred(cl, "FEDFUNDS", 12)
    if rows: return _sonuc(rows, kaynak="FRED")
    # US Treasury (keysiz)
    try:
        url = "https://api.fiscaldata.treasury.gov/services/api/v1/accounting/od/avg_interest_rates?fields=record_date,security_desc,avg_interest_rate_amt&filter=security_desc:eq:Treasury%20Bills&sort=-record_date&limit=6"
        d = await _gfetch(cl, url)
        if d and not isinstance(d, str) and not d.get("__err") and d.get("data"):
            tr = [{"tarih": r["record_date"], "deger": round(float(r["avg_interest_rate_amt"]), 2)}
                  for r in d["data"] if r.get("avg_interest_rate_amt") not in (None, "null")]
            tr.sort(key=lambda x: x["tarih"])
            if tr: return _sonuc(tr, kaynak="US Treasury")
    except Exception: pass
    return _veri_yok()

async def _ppi(cl):
    rows = await _fred(cl, "PPIFIS", 14)
    if rows and len(rows) >= 13:
        ys = []
        for i in range(12, len(rows)):
            pct = (rows[i]["deger"]-rows[i-12]["deger"])/rows[i-12]["deger"]*100
            ys.append({"tarih": rows[i]["tarih"], "deger": round(pct, 2)})
        if ys:
            s, o = rows[-1]["deger"], rows[-2]["deger"]
            return _sonuc(ys, kaynak="FRED", degisim=round((s-o)/o*100, 2))
    return _veri_yok()

# ═══ BTC ETKİ YORUMU ═══
def _btc_yorum(g):
    h = {}
    ff = g.get("fedFaiz")
    if ff and ff.get("guncel") is not None:
        s, tr = ff["guncel"], ff["trend"]
        if tr == "asagi":
            h["fedFaiz"] = f"Fed Funds %{s:.2f} ve DÜŞÜŞ trendinde → faiz indirim döngüsü → DXY zayıflar (BTC ile −0.85 korelasyon) → global likidite genişler → BTC için GÜÇLÜ RALLİ ortamı. 2019-2020 örneği: ilk indirimden sonra +%120 (12 ay)."
        elif tr == "yukari":
            h["fedFaiz"] = f"Fed Funds %{s:.2f} ve YUKARI trendde → sıkılaşma → reel getiri pozitife döner → risk varlıkları satılır. 2022'de BTC −%75. Mevcut seviye {'kısıtlayıcı' if s>=4.5 else 'nötr-restrictive'}."
        else:
            h["fedFaiz"] = f"Fed Funds %{s:.2f} YATAY (pause). {'Higher for longer — ilk indirim sinyaliyle sert hareket beklenir.' if s>=4.5 else 'Nötr zemin — FOMC açıklamaları yön belirleyici.'}"
    cpi = g.get("cpi")
    if cpi and cpi.get("guncel") is not None:
        v, tr, y = cpi["guncel"], cpi["trend"], cpi.get("yillik")
        ys = f" (yıllık %{y:.1f})" if y is not None else ""
        if tr == "asagi":
            h["cpi"] = f"CPI {v:.1f}{ys} DÜŞÜŞTE → dezenflasyon → Fed pivot alanı → bond yield geriler → BTC için POZİTİF. {'Hedefe yakın.' if y and y<3 else 'Hala hedef üstü.'}"
        elif tr == "yukari":
            h["cpi"] = f"CPI {v:.1f}{ys} YUKARI → re-acceleration → 'higher for longer' → Fed indirim gecikir → dolar güçlenir → BTC için KISA VADELİ BASKI. {'Yıllık %3.5+ Fed için kırmızı çizgi.' if y and y>3.5 else ''}"
        else:
            h["cpi"] = f"CPI {v:.1f}{ys} yatay → Fed bekleme modu. Çekirdek enflasyon belirleyici."
    nfp = g.get("nfp")
    if nfp and nfp.get("guncel") is not None:
        v = nfp["guncel"]
        if v < 100:
            h["nfp"] = f"NFP +{v}K ZAYIF (sub-100K) → istihdam yavaşlaması → Fed gevşeme alanı → BTC POZİTİF. <100K 3 ay üst üste = resesyon (Sahm Rule)."
        elif v > 250:
            h["nfp"] = f"NFP +{v}K GÜÇLÜ → ücret enflasyonu sürer → Fed indirim gecikir → BTC için kısıtlayıcı. 'Good news is bad news'."
        else:
            h["nfp"] = f"NFP +{v}K dengeli → Fed için belirleyici değil, ücret büyümesiyle birlikte değerlendirilmeli."
    ppi = g.get("ppi")
    if ppi and ppi.get("guncel") is not None:
        v, tr = ppi["guncel"], ppi["trend"]
        if tr == "yukari":
            h["ppi"] = f"PPI %{v:.1f} YUKARI → üretici maliyet baskısı 2-3 ay sonra CPI'ya yansır → enflasyon ikinci dalga → BTC için OLUMSUZ leading indicator. {'PPI %5+ ciddi enflasyon sinyali.' if v>5 else ''}"
        elif tr == "asagi":
            h["ppi"] = f"PPI %{v:.1f} DÜŞÜŞTE → tedarik zinciri normalleşiyor → CPI'ya disinflasyonist baskı → BTC için POZİTİF leading indicator."
        else:
            h["ppi"] = f"PPI %{v:.1f} sabit → nötr sinyal."
    gs = g.get("gsyih")
    if gs and gs.get("guncel") is not None:
        v, tr = gs["guncel"], gs["trend"]
        if v < 1.5 and tr == "asagi":
            h["gsyih"] = f"Real GDP %{v:.1f} DÜŞÜYOR → resesyon riski → Fed agresif gevşemeye kayar → BTC orta vadede POZİTİF, ilk fazda risk-off."
        elif 1.5 <= v <= 2.5:
            h["gsyih"] = f"Real GDP %{v:.1f} trend büyüme → Goldilocks → risk varlıkları için NÖTR-POZİTİF."
        else:
            h["gsyih"] = f"Real GDP %{v:.1f} → {'güçlü büyüme, enflasyon riski, NET NÖTR.' if v>2.5 else 'trend altı, Fed müdahale kapısı açık.'}"
    pce = g.get("pce")
    if pce and pce.get("guncel") is not None:
        v, tr = pce["guncel"], pce["trend"]
        if tr == "asagi":
            h["pce"] = f"PCE %{v:.2f} DÜŞÜYOR → Fed'in TERCİH ettiği gösterge → faiz indirimi gerekçesi güçlenir → BTC için en önemli bullish sinyallerden."
        elif tr == "yukari":
            h["pce"] = f"PCE %{v:.2f} yükseliyor → Fed birincil göstergesinde re-acceleration → indirim gecikir → BTC için KISA VADELİ BASKI."
        else:
            h["pce"] = f"PCE %{v:.2f} sabit → Fed bekleme modu, ay sonu açıklaması volatilite tetikler."
    isr = g.get("isRate")
    if isr and isr.get("guncel") is not None:
        v, tr = isr["guncel"], isr["trend"]
        if tr == "yukari":
            h["isRate"] = f"İşsizlik %{v:.1f} YUKARI → Sahm Rule yaklaşıyor → Fed istihdam ayağına ağırlık → agresif gevşeme → BTC için güçlü bullish. {'%4.5+ tetik bölgesi.' if v>=4.5 else ''}"
        elif tr == "asagi":
            h["isRate"] = f"İşsizlik %{v:.1f} DÜŞÜYOR → iş piyasası ısınıyor → ücret enflasyonu → Fed aceleci olmaz → BTC NÖTR-OLUMSUZ."
        else:
            h["isRate"] = f"İşsizlik %{v:.1f} sabit → {'tam istihdam yakın, kısıtlayıcı.' if v<4 else 'eşik bölge, yukarı sıçrama Fed tetikler.' if v>=4.5 else 'normalleşme.'}"
    ism = g.get("ism")
    if ism and ism.get("guncel") is not None:
        v, tr = ism["guncel"], ism["trend"]
        if v > 50:
            h["ism"] = f"ISM PMI {v:.1f} GENİŞLEME (50 üstü) → reel ekonomi sağlıklı → risk-on → BTC POZİTİF. {'55+ aşırı ısınma, enflasyon riski.' if v>55 else ''}"
        else:
            h["ism"] = f"ISM PMI {v:.1f} DARALMA → manufacturing recession sinyali → {'toparlanma sinyalleri.' if tr=='yukari' else 'Fed pivot bullish orta vadede.'}"
    pk = g.get("perakende")
    if pk and pk.get("guncel") is not None:
        v, tr = pk["guncel"], pk["trend"]
        if tr == "asagi":
            h["perakende"] = f"Perakende ${v:.1f}Mr DÜŞÜYOR → tüketici talebi zayıf → enflasyon baskısı azalır → Fed gevşeme alanı → BTC için POZİTİF."
        elif tr == "yukari":
            h["perakende"] = f"Perakende ${v:.1f}Mr YUKARI → İKİLİ: güçlü tüketim (pozitif) ama enflasyonu canlı tutar (Fed gecikir, negatif). Reel vs nominal ayrımı kritik."
        else:
            h["perakende"] = f"Perakende ${v:.1f}Mr yatay → nötr."
    # Sentez
    yorumlar = list(h.values())
    olumlu = sum(1 for y in yorumlar if any(k in y for k in ["POZİTİF","RALLİ","bullish","gevşeme alanı"]))
    olumsuz = sum(1 for y in yorumlar if any(k in y for k in ["OLUMSUZ","BASKI","kısıtlayıcı","gecikir"]))
    if olumlu > olumsuz + 1:
        sentez = f"📗 Makro tablo BTC için OLUMLU. {olumlu} destekleyici, {olumsuz} baskı. Dezenflasyon/gevşeyen iş piyasası → Fed pivot tezi ağırlıkta."
        egilim = "POZİTİF"
    elif olumsuz > olumlu + 1:
        sentez = f"📕 Makro tablo BTC için OLUMSUZ. {olumsuz} baskı, {olumlu} destek. Yapışkan enflasyon/güçlü ekonomi → 'higher for longer' hakim."
        egilim = "NEGATİF"
    else:
        sentez = f"📙 Makro NÖTR — {olumlu} pozitif, {olumsuz} negatif. Yön: bir sonraki CPI/PCE, FOMC, NFP. Range-bound BTC, kırılım için katalist gerek."
        egilim = "NÖTR/NEGATİF"
    return {"harita": h, "sentez": sentez, "egilim": egilim, "olumlu": olumlu, "olumsuz": olumsuz}

def makro_3ay_ozet(veri: dict) -> dict:
    """
    Her gösterge için SON 3 AYLIK görünüm: son 3 nokta, 3-ay değişimi, trend, güncel.
    veri = makro_veri() çıktısı.
    """
    g = (veri or {}).get("gostergeler", {})
    out = {}
    for k, v in g.items():
        if not v:
            continue
        gecmis = (v.get("gecmis") or [])[-3:]
        if not gecmis:
            continue
        ilk, son = gecmis[0]["deger"], gecmis[-1]["deger"]
        out[k] = {
            "son_3ay": gecmis,
            "guncel": v.get("guncel"),
            "degisim_3ay": round(son - ilk, 3),
            "trend": _trend([d["deger"] for d in gecmis]),  # 3-ay penceresiyle TUTARLI
            "kaynak": v.get("kaynak"),
            "canli": not v.get("fallback"),
        }
    return out


def _makro_disk_yol():
    from data_ingest import hist_dir
    return hist_dir() / "makro_son.json"


def _makro_disk_yukle():
    try:
        import json
        p = _makro_disk_yol()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _makro_disk_kaydet(out):
    try:
        import json
        p = _makro_disk_yol()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _tarih_yeni_mi(tarih_str, gun=95):
    """tarih 'YYYY-MM'/'YYYY-MM-DD' son `gun` gün içinde mi (3 ay = ~95)."""
    from datetime import datetime
    try:
        s = (tarih_str or "")[:10]
        d = None
        for uzun, fmt in ((10, "%Y-%m-%d"), (7, "%Y-%m")):
            try:
                d = datetime.strptime(s[:uzun], fmt); break
            except Exception:
                continue
        return bool(d) and (datetime.utcnow() - d).days <= gun
    except Exception:
        return False


async def makro_veri(refresh=False):
    import time
    if not refresh and _cache["data"] and (time.time()-_cache["ts"]) < 300:
        return _cache["data"]
    async with httpx.AsyncClient(timeout=20) as cl:
        sonuclar = await asyncio.gather(
            _fedfaiz(cl), _cpi(cl),
            _nfp(cl),  # NFP CANLI (PAYEMS aylık değişim)
            _ppi(cl),
            _basit_fred(cl, "UNRATE", "isRate"),
            _basit_fred(cl, "A191RL1Q225SBEA", "gsyih"),
            _basit_fred(cl, "PCEPI", "pce"),
            _basit_fred(cl, "NAPM", "ism"),
            _basit_fred(cl, "RSAFS", "perakende"),
            return_exceptions=True)
    keys = ["fedFaiz","cpi","nfp","ppi","isRate","gsyih","pce","ism","perakende"]
    g = {}
    for k, r in zip(keys, sonuclar):
        g[k] = r if not isinstance(r, Exception) else None
    # KALICI HAFIZA: canlı kaynak çökerse (veri_yok) diskteki SON 3 AY içindeki
    # GERÇEK veriyi kullan; 3 aydan eskiyse kullanma (eskiyi silmiş oluruz).
    disk = _makro_disk_yukle()
    dg = (disk or {}).get("gostergeler", {})
    for k in keys:
        v = g.get(k)
        if (not v or v.get("veri_yok")):
            dv = dg.get(k)
            if dv and not dv.get("veri_yok") and _tarih_yeni_mi(dv.get("tarih", "")):
                g[k] = dv          # diskteki taze (≤3 ay) gerçek veri
    yorum = _btc_yorum(g)
    fb = sum(1 for v in g.values() if not v or v.get("veri_yok"))
    out = {"guncellendi": datetime.now(timezone.utc).isoformat(),
           "gostergeler": g, "btcYorum": yorum,
           "kaynak_ozet": f"{9-fb}/9 veri var, {fb}/9 veri yok"}
    _cache["data"] = out; _cache["ts"] = time.time()
    _makro_disk_kaydet(out)         # sonraki çöküşte hafızadan servis + 3-ay tut
    return out


# ═══════════════════════════════════════════════════════════════════
#  CARRY TRADE RİSK MONİTÖRÜ (görsel 8 — Japonya carry trade)
# ═══════════════════════════════════════════════════════════════════
# USD/JPY, JGB 10Y, ABD 10Y, Nikkei, VIX, BoJ faizi → Yahoo Finance (ücretsiz)
async def _yahoo(cl, sym):
    try:
        r = await cl.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
            params={"interval":"1d","range":"5d"}, headers={"User-Agent":"Mozilla/5.0"})
        m = r.json()["chart"]["result"][0]["meta"]
        fiyat = m.get("regularMarketPrice")
        onc = m.get("chartPreviousClose") or m.get("previousClose")
        chg = round((fiyat-onc)/onc*100, 2) if (fiyat and onc) else 0
        return {"fiyat": fiyat, "chg": chg}
    except Exception:
        return None

async def _fred_son(cl, series):
    """FRED'den son değer + aylık % değişim (JGB/BoJ için canlı kaynak)."""
    rows = await _fred(cl, series, 3)
    if rows and len(rows) >= 1:
        son = rows[-1]["deger"]
        onc = rows[-2]["deger"] if len(rows) >= 2 else son
        return {"fiyat": round(son, 2), "chg": round((son - onc) / onc * 100, 2) if onc else 0.0}
    return {"fiyat": None, "chg": None, "veri_yok": True, "ad_kaynak": "⚠ Kaynak erişilemedi"}


def _carry_yoksa(sonuc_i):
    return sonuc_i if (not isinstance(sonuc_i, Exception) and sonuc_i) else \
        {"fiyat": None, "chg": None, "veri_yok": True}


async def carry_trade():
    """Japonya carry trade risk monitörü — canlı; kaynak çökerse 'veri yok' (fake yok)."""
    async with httpx.AsyncClient(timeout=15) as cl:
        sonuc = await asyncio.gather(
            _yahoo(cl, "JPY=X"),       # USD/JPY
            _yahoo(cl, "^TNX"),        # ABD 10Y (x10)
            _yahoo(cl, "^N225"),       # Nikkei 225
            _yahoo(cl, "^VIX"),        # VIX
            _fred_son(cl, "IRLTLT01JPM156N"),  # Japonya 10Y (JGB) — FRED CANLI
            _fred_son(cl, "INTDSRJPM193N"),    # BoJ iskonto/politika — FRED CANLI
            return_exceptions=True)
    usdjpy = _carry_yoksa(sonuc[0])
    us10y_raw = sonuc[1] if not isinstance(sonuc[1], Exception) and sonuc[1] else None
    us10y = ({"fiyat": round(us10y_raw["fiyat"] / 10, 2) if us10y_raw["fiyat"] > 20 else us10y_raw["fiyat"],
              "chg": us10y_raw["chg"]} if us10y_raw else {"fiyat": None, "chg": None, "veri_yok": True})
    nikkei = _carry_yoksa(sonuc[2])
    vix = _carry_yoksa(sonuc[3])
    jgb = _carry_yoksa(sonuc[4])
    boj = _carry_yoksa(sonuc[5])

    def _g(d, k):   # None-güvenli değer
        return d.get(k) if d and d.get(k) is not None else None

    # Spread hesapları (veri yoksa None)
    politika_spread = (round(us10y["fiyat"] - boj["fiyat"], 2)
                       if _g(us10y, "fiyat") is not None and _g(boj, "fiyat") is not None else None)
    piyasa_spread = (round(us10y["fiyat"] - jgb["fiyat"], 2)
                     if _g(us10y, "fiyat") is not None and _g(jgb, "fiyat") is not None else None)

    # Risk: yalnız VERİSİ OLAN göstergelerden say (fake veri sinyale girmez)
    unwind_sinyalleri = 0
    if _g(usdjpy, "chg") is not None and usdjpy["chg"] < -0.5: unwind_sinyalleri += 1
    if _g(jgb, "chg") is not None and jgb["chg"] > 1: unwind_sinyalleri += 1
    if _g(nikkei, "chg") is not None and nikkei["chg"] < -1: unwind_sinyalleri += 1
    if _g(vix, "fiyat") is not None and vix["fiyat"] > 25: unwind_sinyalleri += 1
    if _g(boj, "chg") is not None and boj["chg"] > 0: unwind_sinyalleri += 1

    risk = "YÜKSEK" if unwind_sinyalleri >= 4 else "ORTA" if unwind_sinyalleri >= 2 else "DÜŞÜK"

    VY = "⚠ Kaynak erişilemedi — veri yok"
    gostergeler = {
        "usdjpy": {**usdjpy, "ad": "USD/JPY", "alt": "Yen paritesi · carry termometresi",
            "btc": VY if _g(usdjpy, "chg") is None else ("JPY zayıf/sabit → carry pozisyonları korunuyor → BTC için baskı yok → NÖTR-POZİTİF zemin." if usdjpy["chg"]>=-0.5 else "JPY güçleniyor → carry unwind riski → risk varlıkları (BTC dahil) satış baskısı.")},
        "jgb10y": {**jgb, "ad": "JGB 10Y", "alt": "Japon 10Y getirisi · fonlama maliyeti",
            "btc": VY if _g(jgb, "fiyat") is None else "JGB yükseliyor → Japon sermayesi yurda dönüyor (repatriasyon) → küresel likidite daralır → BTC OLUMSUZ. BoJ faiz artışı bu trendi hızlandırır."},
        "us10y": {**us10y, "ad": "ABD 10Y", "alt": "ABD 10Y getirisi · spread ayağı",
            "btc": VY if _g(us10y, "fiyat") is None else f"Carry spread {'geniş' if (piyasa_spread or 0)>1 else 'daralıyor'} ({piyasa_spread}p) → JPY borçlanıp ABD/risk varlığı almak {'hâlâ kârlı → carry akışı sürüyor → BTC DESTEK' if (piyasa_spread or 0)>1 else 'cazibesi azalıyor → carry akışı zayıflar'}."},
        "nikkei": {**nikkei, "ad": "Nikkei 225", "alt": "Japon borsası · unwind barometresi",
            "btc": VY if _g(nikkei, "chg") is None else ("Nikkei güçlü → risk iştahı korunuyor → carry pozisyonları stabil → BTC için POZİTİF teyit." if nikkei["chg"]>=-1 else "Nikkei düşüyor → carry unwind sinyali → küresel risk-off → BTC baskı.")},
        "vix": {**vix, "ad": "VIX", "alt": "Korku endeksi · risk-off tetikleyici",
            "btc": VY if _g(vix, "fiyat") is None else ("VIX düşük (<20) → piyasa sakin → carry pozisyonları güvende → BTC için POZİTİF zemin." if vix["fiyat"]<20 else "VIX yüksek → carry trade'in en çok volatil pozisyonları çözülür → BTC risk-off.")},
        "boj": {**boj, "ad": "BoJ Politika Faizi", "alt": "Merkez bankası · carry fonlama maliyeti",
            "btc": VY if _g(boj, "fiyat") is None else "BoJ faiz artırırsa → JPY güçlenir + carry maliyeti artar → ani unwind riski → BTC için YÜKSEK DİKKAT. Artırım olasılığı izleniyor."},
    }
    if unwind_sinyalleri <= 1:
        degerlendirme = f"Carry trade istikrarlı — {unwind_sinyalleri}/5 unwind sinyali. Pozisyonlar korunuyor, BTC için sistemik risk yok."
    elif unwind_sinyalleri <= 3:
        degerlendirme = f"Hafif uyarı sinyali. {unwind_sinyalleri}/5 gösterge unwind yönünde kıpırdıyor ama henüz sistemik değil. Pozisyon izlenmeli, acil tehdit yok."
    else:
        degerlendirme = f"⚠ Carry unwind riski YÜKSEK — {unwind_sinyalleri}/5 sinyal aktif. 2024 Ağustos benzeri ani çözülme riski. BTC dahil risk varlıkları için kritik."

    return {"risk": risk, "unwind_sinyalleri": unwind_sinyalleri,
            "politika_spread": politika_spread, "piyasa_spread": piyasa_spread,
            "gostergeler": gostergeler, "degerlendirme": degerlendirme,
            "guncellendi": datetime.now(timezone.utc).isoformat()}
