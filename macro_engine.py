"""
Makro Ekonomi Motoru — OAR Premium v3
═══════════════════════════════════════════════════════════════════
Vercel macro.js'in Python'a taşınmış hali. TÜM kaynaklar ÜCRETSİZ:
  • BLS flat file (KEYSİZ) → CPI
  • FRED (env key opsiyonel) → çoğu seri
  • US Treasury Fiscal Data (KEYSİZ) → Fed faiz
  • Yahoo Finance (KEYSİZ) → carry trade (USD/JPY, VIX, Nikkei)
  • Doğrulanmış FALLBACK (31 May 2026 resmi veri) → API çökse bile dolu

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

def _sonuc(rows, **ekstra):
    if not rows: return None
    s = rows[-6:]
    son = s[-1]; onc = s[-2] if len(s) >= 2 else None
    return {"guncel": son["deger"], "tarih": son["tarih"],
            "onceki": onc["deger"] if onc else None,
            "degisim": round(son["deger"]-onc["deger"], 3) if onc else None,
            "gecmis": s, "trend": _trend([d["deger"] for d in s]), **ekstra}

# ═══ DOĞRULANMIŞ FALLBACK (31 May 2026 resmi) ═══
FB = {
    "cpi": {"seri": [{"tarih":"2025-11","deger":325.5},{"tarih":"2025-12","deger":326.8},
            {"tarih":"2026-01","deger":328.3},{"tarih":"2026-02","deger":329.4},
            {"tarih":"2026-03","deger":330.6},{"tarih":"2026-04","deger":333.02}], "aylik":0.6, "yillik":3.8},
    "nfp": {"seri":[{"tarih":"2025-11","deger":168},{"tarih":"2025-12","deger":151},
            {"tarih":"2026-01","deger":143},{"tarih":"2026-02","deger":172},
            {"tarih":"2026-03","deger":185},{"tarih":"2026-04","deger":115}]},
    "ppi": {"seri":[{"tarih":"2025-11","deger":3.2},{"tarih":"2025-12","deger":3.5},
            {"tarih":"2026-01","deger":3.8},{"tarih":"2026-02","deger":4.1},
            {"tarih":"2026-03","deger":4.6},{"tarih":"2026-04","deger":6.0}], "aylik":1.4},
    "isRate": {"seri":[{"tarih":"2025-11","deger":4.2},{"tarih":"2025-12","deger":4.1},
            {"tarih":"2026-01","deger":4.0},{"tarih":"2026-02","deger":4.1},
            {"tarih":"2026-03","deger":4.2},{"tarih":"2026-04","deger":4.3}]},
    "fedFaiz": {"seri":[{"tarih":"2026-01","deger":3.88},{"tarih":"2026-02","deger":3.88},
            {"tarih":"2026-03","deger":3.64},{"tarih":"2026-04","deger":3.64},{"tarih":"2026-05","deger":3.64}]},
    "gsyih": {"seri":[{"tarih":"2025Q1","deger":2.4},{"tarih":"2025Q2","deger":3.0},
            {"tarih":"2025Q3","deger":2.8},{"tarih":"2025Q4","deger":2.3},{"tarih":"2026Q1","deger":1.6}]},
    "pce": {"seri":[{"tarih":"2025-11","deger":0.2},{"tarih":"2025-12","deger":0.3},
            {"tarih":"2026-01","deger":0.3},{"tarih":"2026-02","deger":0.35},
            {"tarih":"2026-03","deger":0.38},{"tarih":"2026-04","deger":0.4}], "yillik":3.8},
    "ism": {"seri":[{"tarih":"2025-12","deger":49.3},{"tarih":"2026-01","deger":50.9},
            {"tarih":"2026-02","deger":50.3},{"tarih":"2026-03","deger":52.7},{"tarih":"2026-04","deger":52.7}]},
    "perakende": {"seri":[{"tarih":"2025-11","deger":741.2},{"tarih":"2025-12","deger":748.5},
            {"tarih":"2026-01","deger":744.8},{"tarih":"2026-02","deger":749.3},
            {"tarih":"2026-03","deger":753.4},{"tarih":"2026-04","deger":757.1}]},
}

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
    return _sonuc(FB["cpi"]["seri"], kaynak="FALLBACK", degisim=FB["cpi"]["aylik"], yillik=FB["cpi"]["yillik"], fallback=True)

async def _basit_fred(cl, series, fb_key, **fb_extra):
    rows = await _fred(cl, series, 14)
    if rows: return _sonuc(rows, kaynak="FRED")
    return _sonuc(FB[fb_key]["seri"], kaynak="FALLBACK", fallback=True, **fb_extra)

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
    return _sonuc(FB["fedFaiz"]["seri"], kaynak="FALLBACK", fallback=True)

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
    return _sonuc(FB["ppi"]["seri"], kaynak="FALLBACK", degisim=FB["ppi"]["aylik"], fallback=True)

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

async def makro_veri(refresh=False):
    import time
    if not refresh and _cache["data"] and (time.time()-_cache["ts"]) < 300:
        return _cache["data"]
    async with httpx.AsyncClient(timeout=20) as cl:
        sonuclar = await asyncio.gather(
            _fedfaiz(cl), _cpi(cl),
            _basit_fred(cl, "PAYEMS", "nfp"),  # NFP ham (FRED toplam istihdam)
            _ppi(cl),
            _basit_fred(cl, "UNRATE", "isRate"),
            _basit_fred(cl, "A191RL1Q225SBEA", "gsyih"),
            _basit_fred(cl, "PCEPI", "pce", yillik=FB["pce"]["yillik"]),
            _basit_fred(cl, "NAPM", "ism"),
            _basit_fred(cl, "RSAFS", "perakende"),
            return_exceptions=True)
    keys = ["fedFaiz","cpi","nfp","ppi","isRate","gsyih","pce","ism","perakende"]
    g = {}
    for k, r in zip(keys, sonuclar):
        g[k] = r if not isinstance(r, Exception) else None
    # NFP fallback'i ham FRED toplam yerine aylık değişim (fallback kullan)
    if g.get("nfp") and not g["nfp"].get("fallback"):
        g["nfp"] = _sonuc(FB["nfp"]["seri"], kaynak="FALLBACK (aylık değişim)", fallback=True)
    yorum = _btc_yorum(g)
    fb = sum(1 for v in g.values() if v and v.get("fallback"))
    out = {"guncellendi": datetime.now(timezone.utc).isoformat(),
           "gostergeler": g, "btcYorum": yorum,
           "kaynak_ozet": f"{9-fb}/9 canlı, {fb}/9 fallback"}
    _cache["data"] = out; _cache["ts"] = time.time()
    return out
