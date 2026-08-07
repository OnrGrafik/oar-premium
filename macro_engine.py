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
from datetime import datetime, timezone, timedelta

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
            "gecmis": s,
            # 12 aylık pencere: sentez "geçmiş+güncel harmanı" için gerekli
            # (6 nokta yıllık kıyas/3-6-12 ay yön analizine yetmiyordu).
            "gecmis12": rows[-13:],
            "trend": _trend([d["deger"] for d in s]), **ekstra}

# ═══════════════════════════════════════════════════════════════════
#  ÇOK KAYNAKLI GÖSTERGE ÇEKİMİ (FRED → BLS keysiz → Hazine keysiz)
#  Kullanıcı sitemi: "site güncel değil, otomatik çekmiyor."
#  Kök neden: 9 göstergenin 8'i YALNIZ FRED'e bağlıydı; FRED_API_KEY yoksa
#  hepsi 'veri yok' oluyordu. Artık her gösterge için ANAHTARSIZ yedek var.
# ═══════════════════════════════════════════════════════════════════
async def _bls(cl, anahtarlar, taze_saat=12.0):
    try:
        import makro_kaynak
        return await makro_kaynak.bls_getir(cl, anahtarlar, taze_saat=taze_saat)
    except Exception:
        return {}


async def _cpi(cl, bls=None):
    # 1) FRED (anahtar varsa en hızlı/temiz)
    rows = await _fred(cl, "CPIAUCSL", 14)
    kaynak = "FRED"
    # 2) BLS API (KEYSİZ yedek — asıl açık buydu)
    if not rows or len(rows) < 13:
        rows = (bls or {}).get("cpi") or []
        kaynak = _bls_ad()
    # 3) BLS flat file (son çare — büyük indirme)
    if not rows or len(rows) < 13:
        rows, kaynak = await _cpi_flat(cl), "BLS flat file"
    if not rows or len(rows) < 2:
        return _veri_yok()
    son, onc = rows[-1]["deger"], rows[-2]["deger"]
    yil = rows[-13]["deger"] if len(rows) >= 13 else None
    return _sonuc(rows, kaynak=kaynak,
                  degisim=round((son - onc) / onc * 100, 2) if onc else None,
                  yillik=round((son - yil) / yil * 100, 2) if yil else None)


async def _cpi_flat(cl):
    try:
        r = await cl.get("https://download.bls.gov/pub/time.series/cu/cu.data.1.AllItems",
                         headers={**HDR, "Accept": "text/plain"}, timeout=TO)
        if r.status_code != 200:
            return []
        parsed = []
        for l in r.text.split("\n"):
            if not l.startswith("CUSR0000SA0"):
                continue
            p = l.split()
            if len(p) >= 4 and p[2].startswith("M") and p[2] != "M13":
                try:
                    parsed.append({"tarih": f"{p[1]}-{p[2][1:].zfill(2)}", "deger": float(p[3])})
                except Exception:
                    pass
        parsed.sort(key=lambda x: x["tarih"])
        return parsed[-14:]
    except Exception:
        return []


def _bls_ad():
    try:
        import makro_kaynak
        return makro_kaynak.bls_kaynak_adi()
    except Exception:
        return "BLS API"


async def _basit_fred(cl, series, fb_key=None, **fb_extra):
    rows = await _fred(cl, series, 14)
    if rows: return _sonuc(rows, kaynak="FRED", **fb_extra)
    return _veri_yok()


async def _yillik_fred(cl, series, ad="FRED"):
    """Endeks serisini YILLIK % değişime çevirir (PCE/perakende/sanayi gibi
    seviye serileri sitede '%' diye gösteriliyordu — yanıltıcıydı)."""
    rows = await _fred(cl, series, 26)
    if not rows or len(rows) < 13:
        return _veri_yok()
    ys = [{"tarih": rows[i]["tarih"],
           "deger": round((rows[i]["deger"] - rows[i - 12]["deger"]) / rows[i - 12]["deger"] * 100, 2)}
          for i in range(12, len(rows)) if rows[i - 12]["deger"]]
    if not ys:
        return _veri_yok()
    return _sonuc(ys, kaynak=ad, seviye=rows[-1]["deger"])


async def _nfp(cl, bls=None):
    """
    NFP (tarım dışı istihdam) = toplam istihdam SEVİYESİNİN aylık FARKI (bin kişi).
    FRED PAYEMS → yoksa BLS CES0000000001 (KEYSİZ). BLS manşeti kendisi yayınlar,
    yani anahtarsız yolda da açıklama günü verisi gelir.
    """
    rows = await _fred(cl, "PAYEMS", 26)
    kaynak = "FRED (PAYEMS aylık değişim)"
    if not rows or len(rows) < 2:
        rows = (bls or {}).get("nfp") or []
        kaynak = f"{_bls_ad()} (CES aylık değişim)"
    if not rows or len(rows) < 2:
        return _veri_yok()
    chg = [{"tarih": rows[i]["tarih"], "deger": round(rows[i]["deger"] - rows[i - 1]["deger"])}
           for i in range(1, len(rows))]
    s3 = [d["deger"] for d in chg[-3:]]
    return _sonuc(chg, kaynak=kaynak,
                  ort_3ay=round(sum(s3) / len(s3)) if s3 else None)


async def _issizlik(cl, bls=None):
    rows = await _fred(cl, "UNRATE", 26)
    kaynak = "FRED"
    if not rows:
        rows = (bls or {}).get("isRate") or []
        kaynak = _bls_ad()
    if not rows:
        return _veri_yok()
    # Sahm benzeri: son 12 ayın DİBİNE göre yükseliş (resesyon erken uyarısı)
    son12 = [d["deger"] for d in rows[-12:]]
    return _sonuc(rows, kaynak=kaynak,
                  dipten_artis=round(rows[-1]["deger"] - min(son12), 2) if son12 else None)


async def _hazine(cl):
    """Getiri eğrisi — ÖNCE keysiz Hazine CSV, sonra FRED. Günlük tazelik."""
    try:
        import makro_kaynak
        h = await makro_kaynak.hazine_egrisi(cl)
    except Exception:
        h = None
    if h and h.get("y10") is not None:
        return h
    # FRED yedeği
    r10 = await _fred(cl, "DGS10", 40)
    r2 = await _fred(cl, "DGS2", 40)
    if not r10:
        return None
    return {"tarih": r10[-1]["tarih"], "y10": r10[-1]["deger"],
            "y2": r2[-1]["deger"] if r2 else None, "m3": None, "y30": None,
            "egri_10_2": (round(r10[-1]["deger"] - r2[-1]["deger"], 2) if r2 else None),
            "seri_y10": r10[-40:], "seri_y2": (r2 or [])[-40:], "kaynak": "FRED"}


def _hazine_gosterge(h, alan, ad_seri):
    if not h or h.get(alan) is None:
        return _veri_yok()
    seri = h.get(ad_seri) or [{"tarih": h.get("tarih"), "deger": h.get(alan)}]
    s = _sonuc(seri, kaynak=h.get("kaynak", "US Treasury"))
    # Seri ile başlık tarihi ayrışırsa gösterge yanlışlıkla "bayat" görünüyordu.
    s["tarih"] = h.get("tarih") or s.get("tarih")
    s["guncel"] = h.get(alan)
    return s


async def _effr_nyfed(cl):
    """
    Politika faizi — ANAHTARSIZ yedek: NY Fed'in yayınladığı efektif gecelik faiz.
    (Fed politika bandının fiilen gerçekleşen orta noktası; FRED anahtarı yokken
    para politikası bloğunun boş kalmaması için.)
    """
    try:
        r = await cl.get("https://markets.newyorkfed.org/api/rates/unsecured/effr/last/20.json",
                         headers=HDR, timeout=TO)
        if r.status_code != 200:
            return None
        d = r.json()
        rows = []
        for o in (d.get("refRates") or []):
            oran = o.get("percentRate", o.get("rate"))
            tar = o.get("effectiveDate") or o.get("date")
            if oran is None or not tar:
                continue
            try:
                rows.append({"tarih": str(tar)[:10], "deger": round(float(oran), 2)})
            except Exception:
                continue
        rows.sort(key=lambda x: x["tarih"])
        return rows or None
    except Exception:
        return None


async def _fedfaiz(cl):
    rows = await _fred(cl, "FEDFUNDS", 12)
    if rows: return _sonuc(rows, kaynak="FRED")
    rows = await _effr_nyfed(cl)                 # keysiz, GÜNLÜK (aylık FEDFUNDS'tan taze)
    if rows: return _sonuc(rows, kaynak="NY Fed efektif gecelik faiz")
    # US Treasury ortalama bono faizi (keysiz, kaba vekil)
    try:
        url = "https://api.fiscaldata.treasury.gov/services/api/v1/accounting/od/avg_interest_rates?fields=record_date,security_desc,avg_interest_rate_amt&filter=security_desc:eq:Treasury%20Bills&sort=-record_date&limit=6"
        d = await _gfetch(cl, url)
        if d and not isinstance(d, str) and not d.get("__err") and d.get("data"):
            tr = [{"tarih": r["record_date"], "deger": round(float(r["avg_interest_rate_amt"]), 2)}
                  for r in d["data"] if r.get("avg_interest_rate_amt") not in (None, "null")]
            tr.sort(key=lambda x: x["tarih"])
            if tr: return _sonuc(tr, kaynak="US Treasury (bono ort. faizi — vekil)")
    except Exception: pass
    return _veri_yok()

async def _ppi(cl, bls=None):
    rows = await _fred(cl, "PPIFIS", 26)
    kaynak = "FRED"
    if not rows or len(rows) < 13:
        rows = (bls or {}).get("ppi") or []
        kaynak = _bls_ad()
    if not rows or len(rows) < 13:
        return _veri_yok()
    ys = [{"tarih": rows[i]["tarih"],
           "deger": round((rows[i]["deger"] - rows[i - 12]["deger"]) / rows[i - 12]["deger"] * 100, 2)}
          for i in range(12, len(rows)) if rows[i - 12]["deger"]]
    if not ys:
        return _veri_yok()
    s, o = rows[-1]["deger"], rows[-2]["deger"]
    return _sonuc(ys, kaynak=kaynak, degisim=round((s - o) / o * 100, 2) if o else None)

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


# Gösterge → (görünen ad, yayın sıklığı gün, bayatlık eşiği gün)
# Bayatlık eşiği: kaynak yayınlamayı BIRAKTIYSA (ör. FRED'de ISM/NAPM serisi
# 2016'da durduruldu) eski değer "güncel" gibi görünüyordu. Artık işaretlenir.
GOSTERGE_META = {
    "fedFaiz":   ("Fed Politika Faizi", 30, 70),
    "cpi":       ("TÜFE (CPI)", 30, 70),
    "cpi_cekirdek": ("Çekirdek TÜFE", 30, 70),
    "nfp":       ("Tarım Dışı İstihdam", 30, 70),
    "ppi":       ("ÜFE (PPI)", 30, 70),
    "isRate":    ("İşsizlik Oranı", 30, 70),
    "gsyih":     ("GSYİH Büyüme", 90, 200),
    "pce":       ("PCE Enflasyon", 30, 80),
    "ism":       ("ISM İmalat PMI", 30, 70),
    "perakende": ("Perakende Satışlar", 30, 80),
    "sanayi":    ("Sanayi Üretimi", 30, 80),
    "guven":     ("Tüketici Güveni", 30, 70),
    "claims":    ("Haftalık İşsizlik Başvurusu", 7, 21),
    "us2y":      ("ABD 2 Yıllık Getiri", 1, 8),
    "us10y":     ("ABD 10 Yıllık Getiri", 1, 8),
    "reel10y":   ("10Y Reel Getiri (TIPS)", 1, 12),
    "dxy":       ("Dolar Endeksi", 1, 12),
}

# Sitedeki 9 ana kart (frontend bu sırayı bekliyor) + yeni yüksek frekanslı set
ANA_GOSTERGELER = ["fedFaiz", "cpi", "nfp", "ppi", "gsyih", "pce", "isRate", "ism", "perakende"]
HIZLI_GOSTERGELER = ["claims", "us2y", "us10y", "reel10y", "dxy", "cpi_cekirdek", "sanayi", "guven"]


def _bayat_isaretle(g: dict):
    """Kaynak yayınlamayı bıraktıysa/veri gecikti ise işaretle (sessiz eskime yok)."""
    for k, v in (g or {}).items():
        if not isinstance(v, dict) or v.get("veri_yok") or not v.get("tarih"):
            continue
        esik = (GOSTERGE_META.get(k) or (None, 30, 70))[2]
        v["bayat"] = not _tarih_yeni_mi(v.get("tarih"), gun=esik)
        v["bayatlik_esigi_gun"] = esik


def _ttl() -> float:
    """
    Cache ömrü DİNAMİK: açıklama penceresinde 60 sn, normalde 300 sn.
    Sabit 5 dk cache yüzünden 08:30 ET verisi siteye geç düşüyordu.
    """
    try:
        from makro_takvim import aktif_olay_penceresi
        if aktif_olay_penceresi():
            return 60.0
    except Exception:
        pass
    return 300.0


def _yeni_veri_tespit(yeni: dict, eski: dict) -> list[dict]:
    """
    Bir göstergenin EN YENİ gözlem dönemi ilerlediyse = YENİ VERİ AÇIKLANDI.
    Takvim tahminine değil, gerçekten gelen veriye bakar (dürüst tespit).
    """
    out = []
    eg = (eski or {}).get("gostergeler") or {}
    for k, v in (yeni or {}).items():
        if not isinstance(v, dict) or v.get("veri_yok") or not v.get("tarih"):
            continue
        onceki = eg.get(k) or {}
        eski_t = onceki.get("tarih")
        if eski_t and str(v["tarih"]) > str(eski_t):
            out.append({
                "anahtar": k,
                "ad": (GOSTERGE_META.get(k) or (k,))[0],
                "donem": v["tarih"], "onceki_donem": eski_t,
                "deger": v.get("guncel"), "onceki_deger": onceki.get("guncel"),
                "degisim": v.get("degisim"),
                "algilandi": datetime.now(timezone.utc).isoformat(),
            })
    return out


def _yenilik_gunlugu_guncelle(yenilikler: list[dict]) -> list[dict]:
    """Son 30 günün 'yeni açıklanan veri' günlüğü (site + Telegram için)."""
    kayit = _makro_disk_yukle_ad("makro_yenilikler") or []
    if yenilikler:
        kayit = (yenilikler + kayit)[:60]
        _makro_disk_kaydet_ad("makro_yenilikler", kayit)
    sinir = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    return [y for y in kayit if str(y.get("algilandi", "")) >= sinir]


async def makro_veri(refresh=False):
    import time
    if not refresh and _cache["data"] and (time.time() - _cache["ts"]) < _ttl():
        return _cache["data"]

    async with httpx.AsyncClient(timeout=20) as cl:
        # BLS (keysiz yedek) tek seferde çekilir; açıklama gününde daha sık tazelenir
        try:
            from makro_takvim import bugunku_aciklamalar
            bugun_var = any(o.get("gecti") for o in bugunku_aciklamalar())
        except Exception:
            bugun_var = False
        bls = await _bls(cl, ["cpi", "cpi_cekirdek", "ppi", "nfp", "isRate"],
                         taze_saat=1.0 if bugun_var else 12.0)
        hazine = await _hazine(cl)

        sonuclar = await asyncio.gather(
            _fedfaiz(cl), _cpi(cl, bls), _nfp(cl, bls), _ppi(cl, bls),
            _basit_fred(cl, "A191RL1Q225SBEA"),           # GSYİH (çeyreklik, %)
            _yillik_fred(cl, "PCEPI"),                    # PCE — YILLIK % (endeks değil)
            _basit_fred(cl, "NAPM"),                      # ISM (kaynak durmuş olabilir → bayat)
            _yillik_fred(cl, "RSAFS"),                    # Perakende — YILLIK %
            _issizlik(cl, bls),
            _basit_fred(cl, "ICSA"),                      # haftalık işsizlik başvurusu
            _yillik_fred(cl, "INDPRO"),                   # sanayi üretimi — YILLIK %
            _basit_fred(cl, "UMCSENT"),                   # tüketici güveni
            _basit_fred(cl, "DFII10"),                    # 10Y reel getiri (TIPS)
            _basit_fred(cl, "DTWEXBGS"),                  # geniş dolar endeksi
            return_exceptions=True)

    keys = ["fedFaiz", "cpi", "nfp", "ppi", "gsyih", "pce", "ism", "perakende",
            "isRate", "claims", "sanayi", "guven", "reel10y", "dxy"]
    g = {}
    for k, r in zip(keys, sonuclar):
        g[k] = r if not isinstance(r, Exception) else None

    # BLS'ten gelen çekirdek TÜFE (FRED'siz de çalışsın)
    if (bls or {}).get("cpi_cekirdek"):
        cr = bls["cpi_cekirdek"]
        yv = None
        if len(cr) >= 13 and cr[-13]["deger"]:
            yv = round((cr[-1]["deger"] - cr[-13]["deger"]) / cr[-13]["deger"] * 100, 2)
        g["cpi_cekirdek"] = _sonuc(cr, kaynak=_bls_ad(), yillik=yv)

    # Getiri eğrisi (keysiz Hazine) — piyasa beklentisinin ölçüldüğü yer
    if hazine:
        g["us2y"] = _hazine_gosterge(hazine, "y2", "seri_y2")
        g["us10y"] = _hazine_gosterge(hazine, "y10", "seri_y10")
        g["egri"] = {"guncel": hazine.get("egri_10_2"), "tarih": hazine.get("tarih"),
                     "kaynak": hazine.get("kaynak"), "gecmis": [], "trend": "belirsiz",
                     "m3": hazine.get("m3"), "y30": hazine.get("y30")}

    # Dolar endeksi FRED'siz kaldıysa piyasa kaynağından dene (keysiz)
    if (not g.get("dxy") or g["dxy"].get("veri_yok")):
        y = await _dxy_piyasa()
        if y:
            g["dxy"] = y

    # KALICI HAFIZA: canlı kaynak çökerse diskteki SON 3 AY içindeki GERÇEK veriyi
    # kullan; 3 aydan eskiyse kullanma (bayat veriyi güncel gibi gösterme).
    disk = _makro_disk_yukle()
    dg = (disk or {}).get("gostergeler", {})
    for k in list(GOSTERGE_META) + ["egri"]:
        v = g.get(k)
        if (not v or v.get("veri_yok")):
            dv = dg.get(k)
            if dv and not dv.get("veri_yok") and _tarih_yeni_mi(dv.get("tarih", "")):
                g[k] = {**dv, "hafizadan": True}

    _bayat_isaretle(g)
    yenilikler = _yeni_veri_tespit(g, disk)
    gunluk = _yenilik_gunlugu_guncelle(yenilikler)

    yorum = _btc_yorum(g)
    ana = [k for k in ANA_GOSTERGELER]
    var = sum(1 for k in ana if g.get(k) and not g[k].get("veri_yok"))
    hizli_var = sum(1 for k in HIZLI_GOSTERGELER if g.get(k) and not g[k].get("veri_yok"))

    out = {"guncellendi": datetime.now(timezone.utc).isoformat(),
           "gostergeler": g, "btcYorum": yorum,
           "yeni_veriler": yenilikler, "son_30gun_yenilik": gunluk[:20],
           "kaynak_ozet": (f"{var}/{len(ana)} ana gösterge · "
                           f"{hizli_var}/{len(HIZLI_GOSTERGELER)} hızlı gösterge canlı"),
           "kaynak_teshis": _kaynak_teshis()}
    _cache["data"] = out; _cache["ts"] = time.time()
    _makro_disk_kaydet(out)
    return out


async def _dxy_piyasa():
    """Dolar endeksi — FRED anahtarı yoksa piyasa kotasyonundan (keysiz)."""
    try:
        async with httpx.AsyncClient(timeout=12) as cl:
            d = await _yahoo(cl, "DX-Y.NYB")
        if d and d.get("fiyat"):
            return {"guncel": round(d["fiyat"], 2),
                    "tarih": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "onceki": None, "degisim": d.get("chg"),
                    "gecmis": [], "gecmis12": [], "trend": "belirsiz",
                    "kaynak": "piyasa kotasyonu"}
    except Exception:
        pass
    return None


def _kaynak_teshis() -> dict:
    """Hangi kaynak elde/kırık — 'neden güncellenmiyor' sorusunun cevabı."""
    d = {"fred_anahtar": bool(FRED_KEY), "bls_anahtar": bool(BLS_KEY)}
    try:
        import makro_kaynak
        d.update(makro_kaynak.durum())
    except Exception:
        pass
    if not d.get("fred_anahtar"):
        d["not"] = ("FRED_API_KEY tanımsız — GSYİH/PCE/perakende/sanayi/güven yalnız "
                    "FRED'de var. Enflasyon/istihdam/faiz anahtarsız kaynaklardan gelir.")
    return d


def _makro_disk_yukle_ad(ad):
    try:
        import json
        p = _makro_disk_yol().parent / f"{ad}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _makro_disk_kaydet_ad(ad, obj):
    try:
        import json
        p = _makro_disk_yol().parent / f"{ad}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


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


_carry_cache = {"data": None, "ts": 0}


async def carry_trade():
    """Japonya carry trade risk monitörü — canlı; kaynak çökerse 'veri yok' (fake yok)."""
    import time as _t
    # 5 dk cache: sentez + site paneli + lider bağlamı aynı turda çağırıyordu
    # (6 dış istek × her çağrı). Veri günlük çözünürlükte, cache kayıp yaratmaz.
    if _carry_cache["data"] and (_t.time() - _carry_cache["ts"]) < 300:
        return _carry_cache["data"]
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

    sonuc_d = {"risk": risk, "unwind_sinyalleri": unwind_sinyalleri,
               "politika_spread": politika_spread, "piyasa_spread": piyasa_spread,
               "gostergeler": gostergeler, "degerlendirme": degerlendirme,
               "guncellendi": datetime.now(timezone.utc).isoformat()}
    _carry_cache["data"] = sonuc_d
    _carry_cache["ts"] = _t.time()
    return sonuc_d


# ═══════════════════════════════════════════════════════════════════════════════
#  MAKRO SENTEZ — geçmiş + güncel harmanı, blok blok, piyasa beklentisiyle
# ═══════════════════════════════════════════════════════════════════════════════
# Kullanıcı sitemi: "üstteki özet, makro verilerin tamamını geçmiş ve günceli
# harmanlayıp piyasa beklentilerini özetlemesi gerekirken çok sığ kalmış."
# ÖNCE: /api/makro/ozet YALNIZ _btc_yorum'un tek cümlelik `sentez` satırını
# dönüyordu ("📙 Makro NÖTR — 3 pozitif, 2 negatif") — ne seviye, ne yön, ne
# beklenti, ne katalist. ŞİMDİ: 5 analiz bloğu (enflasyon · istihdam · büyüme ·
# para politikası+piyasa fiyatlaması · dolar/likidite), her biri
#   GEÇMİŞ (3-6-12 ay) → GÜNCEL → ÇIKARIM
# zinciriyle; üstüne ağırlıklı net skor, sıradaki katalistler ve tezi BOZACAK
# koşul (geçersizlik). Tamamı KURAL-TABANLI (§0LLM — dış LLM yok).

def _kirp(x, alt=-1.0, ust=1.0):
    return max(alt, min(ust, x))


def _dg(g, k, alan="guncel"):
    v = (g or {}).get(k)
    if not isinstance(v, dict) or v.get("veri_yok"):
        return None
    return v.get(alan)


def _seri_deger(g, k, n=6):
    v = (g or {}).get(k) or {}
    seri = v.get("gecmis12") or v.get("gecmis") or []
    return [d.get("deger") for d in seri[-n:] if isinstance(d, dict) and d.get("deger") is not None]


def _yillik_hiz(g, k, ay=3):
    """
    Endeks serisinden SON `ay` ayın YILLIKLANDIRILMIŞ hızı.
    Enflasyonda 'yıllık %X' geçmişi, '3-ay yıllıklandırılmış' BUGÜNÜ anlatır;
    ikisinin farkı momentumun yönüdür (geçmiş+güncel harmanının çekirdeği).
    """
    s = _seri_deger(g, k, ay + 1)
    if len(s) < ay + 1 or not s[0]:
        return None
    try:
        return round(((s[-1] / s[0]) ** (12.0 / ay) - 1) * 100, 2)
    except Exception:
        return None


def _yon_sozu(fark, esik=0.2, artan="hızlanıyor", azalan="yavaşlıyor", ayni="yatay"):
    if fark is None:
        return ayni
    if fark > esik:
        return artan
    if fark < -esik:
        return azalan
    return ayni


def _blok_enflasyon(g):
    cpi_y = _dg(g, "cpi", "yillik")
    cek_y = _dg(g, "cpi_cekirdek", "yillik")
    ppi_y = _dg(g, "ppi")
    pce_y = _dg(g, "pce")
    hiz3 = _yillik_hiz(g, "cpi", 3)
    hiz6 = _yillik_hiz(g, "cpi", 6)
    if cpi_y is None and pce_y is None and ppi_y is None:
        return None
    ana = cpi_y if cpi_y is not None else pce_y
    momentum = (hiz3 - cpi_y) if (hiz3 is not None and cpi_y is not None) else None

    if ana is None:
        durum, skor = "BELİRSİZ", 0.0
    elif ana <= 2.5 and (momentum or 0) <= 0.2:
        durum, skor = "DEZENFLASYON", 0.7
    elif ana <= 3.2 and (momentum or 0) <= 0.3:
        durum, skor = "HEDEFE YAKINSIYOR", 0.35
    elif (momentum or 0) > 0.5:
        durum, skor = "RE-AKSELERASYON", -0.75
    elif ana > 3.5:
        durum, skor = "YAPIŞKAN / HEDEF ÜSTÜ", -0.45
    else:
        durum, skor = "YAPIŞKAN", -0.15

    p = []
    if cpi_y is not None:
        p.append(f"Manşet TÜFE yıllık %{cpi_y:.1f}")
        if hiz3 is not None:
            mom_soz = _yon_sozu(momentum, 0.2,
                                "yıllığın ÜSTÜNDE — baskı geri geliyor",
                                "yıllığın ALTINDA — soğuma sürüyor",
                                "yıllıkla aynı — yön yok")
            p.append(f"son 3 ayın yıllıklandırılmış hızı %{hiz3:.1f} ({mom_soz})")
        if hiz6 is not None:
            p.append(f"6 aylık hız %{hiz6:.1f}")
    if cek_y is not None:
        p.append(f"çekirdek %{cek_y:.1f} (Fed'in asıl baktığı yapışkan kısım)")
    if pce_y is not None:
        p.append(f"PCE yıllık %{pce_y:.1f} — Fed'in resmî hedef göstergesi (%2 hedef)")
    if ppi_y is not None:
        p.append(f"ÜFE yıllık %{ppi_y:.1f}; üretici maliyeti tüketiciye 2-3 ay gecikmeyle geçer "
                 f"→ {'önümüzdeki aylara YUKARI risk' if ppi_y > 2.5 else 'öncü baskı yok'}")
    metin = "; ".join(p) + "."
    if skor > 0.3:
        metin += (" Çıkarım: dezenflasyon Fed'e indirim alanı açar; reel getiri geriler, "
                  "risk varlıkları ve BTC için DESTEKLEYİCİ zemin.")
    elif skor < -0.3:
        metin += (" Çıkarım: enflasyon Fed'in elini bağlar; 'higher for longer' fiyatlanır, "
                  "dolar güçlenir, BTC için BASKI.")
    else:
        metin += " Çıkarım: Fed'i tek başına hareket ettirecek sinyal yok — istihdam belirleyici."
    return {"ad": "ENFLASYON", "ikon": "🔥", "durum": durum, "skor": round(skor, 2),
            "metin": metin,
            "olcumler": {"tufe_yillik": cpi_y, "cekirdek_yillik": cek_y, "pce_yillik": pce_y,
                         "ufe_yillik": ppi_y, "hiz_3ay": hiz3, "hiz_6ay": hiz6}}


def _blok_istihdam(g):
    nfp = _dg(g, "nfp")
    nfp3 = _dg(g, "nfp", "ort_3ay")
    isr = _dg(g, "isRate")
    dipten = _dg(g, "isRate", "dipten_artis")
    claims = _dg(g, "claims")
    claims_seri = _seri_deger(g, "claims", 4)
    claims4 = round(sum(claims_seri) / len(claims_seri)) if claims_seri else None
    if nfp is None and isr is None and claims is None:
        return None

    skor = 0.0
    if nfp3 is not None:
        if nfp3 < 50:
            skor -= 0.5                     # sert bozulma: önce risk-off, sonra pivot
        elif nfp3 < 125:
            skor += 0.55                    # ılımlı soğuma = Fed'e gevşeme alanı
        elif nfp3 > 250:
            skor -= 0.5
    if dipten is not None:
        skor += 0.2 if 0.2 <= dipten < 0.5 else (-0.45 if dipten >= 0.5 else 0.0)
    skor = _kirp(skor)
    durum = ("SERT BOZULMA" if (dipten or 0) >= 0.5 or (nfp3 is not None and nfp3 < 50)
             else "SOĞUYOR" if (nfp3 is not None and nfp3 < 125)
             else "ISINIYOR" if (nfp3 is not None and nfp3 > 250) else "DENGELİ")

    p = []
    if nfp is not None:
        p.append(f"Son tarım dışı istihdam {nfp:+,.0f} bin".replace(",", "."))
        if nfp3 is not None:
            p.append(f"3 aylık ortalama {nfp3:+,.0f} bin (piyasa manşetten çok bu ortalamaya bakar)".replace(",", "."))
    if isr is not None:
        p.append(f"işsizlik %{isr:.1f}" +
                 (f", son 12 ayın dibine göre +{dipten:.1f} puan "
                  f"({'Sahm resesyon eşiği (+0.5) AŞILDI' if dipten >= 0.5 else 'Sahm eşiğine (+0.5) mesafe var'})"
                  if dipten is not None else ""))
    if claims4 is not None:
        p.append(f"haftalık işsizlik başvurusu 4-hafta ortalaması {claims4:,.0f}".replace(",", ".") +
                 (" — 260 binin üstü kalıcılaşırsa iş piyasası kırılıyor demektir"
                  if claims4 > 240000 else " — henüz kırılma yok"))
    metin = "; ".join(p) + "."
    if durum == "SOĞUYOR":
        metin += (" Çıkarım: ılımlı soğuma Fed'in indirim gerekçesi; 'kötü haber iyi haber' "
                  "rejimi — BTC için POZİTİF.")
    elif durum == "SERT BOZULMA":
        metin += (" Çıkarım: resesyon fiyatlaması başlar; ilk fazda tüm risk varlıkları satılır "
                  "(BTC dahil), agresif gevşeme ikinci fazda toparlar.")
    elif durum == "ISINIYOR":
        metin += " Çıkarım: güçlü istihdam indirimi öteler, reel getiri yüksek kalır — BTC için BASKI."
    else:
        metin += " Çıkarım: Fed'i zorlamayan denge; yön enflasyondan gelecek."
    return {"ad": "İSTİHDAM", "ikon": "👷", "durum": durum, "skor": round(skor, 2),
            "metin": metin,
            "olcumler": {"nfp": nfp, "nfp_3ay_ort": nfp3, "issizlik": isr,
                         "dipten_artis": dipten, "basvuru_4hafta": claims4}}


def _blok_buyume(g):
    gdp = _dg(g, "gsyih")
    sanayi = _dg(g, "sanayi")
    perakende = _dg(g, "perakende")
    ism = _dg(g, "ism")
    ism_bayat = ((g or {}).get("ism") or {}).get("bayat")
    guven = _dg(g, "guven")
    if all(v is None for v in (gdp, sanayi, perakende, guven)) and (ism is None or ism_bayat):
        return None
    skor = 0.0
    if gdp is not None:
        skor += 0.3 if 1.5 <= gdp <= 2.5 else (-0.2 if gdp < 1.0 else 0.1)
    if perakende is not None:
        skor += 0.15 if perakende > 0 else -0.25
    if ism is not None and not ism_bayat:
        skor += 0.25 if ism > 50 else -0.2
    skor = _kirp(skor)
    durum = "GENİŞLEME" if skor > 0.2 else "DARALMA" if skor < -0.2 else "YAVAŞLAMA"
    p = []
    if gdp is not None:
        p.append(f"GSYİH büyümesi %{gdp:.1f} " +
                 ("(trend büyüme bandı — Goldilocks)" if 1.5 <= gdp <= 2.5
                  else "(trend altı, Fed müdahale kapısı açık)" if gdp < 1.5
                  else "(trend üstü, enflasyon riski taşır)"))
    if perakende is not None:
        p.append(f"perakende satışlar yıllık %{perakende:.1f} — tüketici talebi "
                 f"{'canlı' if perakende > 2 else 'zayıflıyor' if perakende < 0 else 'ılımlı'}")
    if sanayi is not None:
        p.append(f"sanayi üretimi yıllık %{sanayi:.1f}")
    if guven is not None:
        p.append(f"tüketici güveni {guven:.1f}")
    if ism is not None and not ism_bayat:
        p.append(f"ISM imalat {ism:.1f} ({'genişleme' if ism > 50 else 'daralma'}, 50 eşik)")
    elif ism_bayat:
        p.append("ISM imalat verisi ücretsiz kaynakta güncellenmiyor — bu blokta ağırlığı yok")
    metin = "; ".join(p) + "."
    metin += (" Çıkarım: reel ekonomi risk iştahını taşıyor, BTC için nötr-pozitif zemin."
              if durum == "GENİŞLEME" else
              " Çıkarım: talep zayıflıyor; enflasyon baskısı azalır (Fed'e alan) ama kazanç/risk "
              "iştahı da düşer — iki yönlü." if durum == "YAVAŞLAMA" else
              " Çıkarım: daralma sinyali; kısa vadede risk-off, orta vadede Fed pivotu.")
    return {"ad": "BÜYÜME", "ikon": "🏭", "durum": durum, "skor": round(skor, 2),
            "metin": metin,
            "olcumler": {"gsyih": gdp, "perakende_yillik": perakende,
                         "sanayi_yillik": sanayi, "ism": None if ism_bayat else ism,
                         "guven": guven}}


def _blok_politika(g):
    """
    PİYASA BEKLENTİSİ bloğu — kullanıcının istediği 'beklentileri özetleyen' kısım.
    2 yıllık tahvil getirisi, önümüzdeki ~2 yılın ORTALAMA politika faizi beklentisidir.
    Politika faizi − 2Y makası, tahvil piyasasının ima ettiği indirim/artırım yönüdür.
    (İma; kesin fiyatlama değil — vade primi de içerir. Sitede böyle etiketleniyor.)
    """
    ff = _dg(g, "fedFaiz")
    y2 = _dg(g, "us2y")
    y10 = _dg(g, "us10y")
    egri = _dg(g, "egri")
    reel = _dg(g, "reel10y")
    # Politika faizi okunamadıysa 3 aylık bono getirisi VEKİL olarak kullanılır
    # (piyasanın kısa vadeli politika fiyatlaması) — vekil olduğu metinde yazar.
    ff_vekil = False
    if ff is None:
        m3 = ((g or {}).get("egri") or {}).get("m3")
        if m3 is not None:
            ff, ff_vekil = m3, True
    if ff is None and y2 is None and y10 is None:
        return None

    makas = round(ff - y2, 2) if (ff is not None and y2 is not None) else None
    ima_adim = int(round(makas / 0.25)) if makas is not None else None
    skor = 0.0
    if ima_adim is not None:
        skor += _kirp(ima_adim * 0.22, -0.8, 0.8)
    if reel is not None:
        skor += -0.35 if reel > 2.0 else (0.3 if reel < 1.0 else 0.0)
    if egri is not None:
        skor += -0.2 if egri < 0 else 0.1
    skor = _kirp(skor)
    durum = ("İNDİRİM FİYATLANIYOR" if (ima_adim or 0) >= 2 else
             "ILIMLI GEVŞEME İMASI" if (ima_adim or 0) == 1 else
             "SIKILAŞMA İMASI" if (ima_adim or 0) <= -1 else "BEKLE-GÖR")

    p = []
    if ff is not None:
        p.append(f"Politika faizi %{ff:.2f}" +
                 (" (politika faizi okunamadı — 3 aylık bono getirisi vekil alındı)" if ff_vekil else ""))
    if y2 is not None:
        p.append(f"2 yıllık tahvil %{y2:.2f}")
    if makas is not None:
        p.append(f"makas {makas:+.2f} puan → tahvil piyasası önümüzdeki ~2 yılda "
                 f"{'yaklaşık ' + str(abs(ima_adim)) + ' adet 25bp ' + ('İNDİRİM' if ima_adim > 0 else 'ARTIRIM') if ima_adim else 'kayda değer bir değişim yok'} "
                 f"ima ediyor (vade primi dahil ima; kesin fiyatlama değil)")
    if y10 is not None:
        p.append(f"10 yıllık %{y10:.2f}")
    if egri is not None:
        p.append(f"10Y−2Y eğrisi {egri:+.2f} puan " +
                 ("(TERS eğri — tarihsel resesyon öncüsü)" if egri < 0
                  else "(normalleşmiş/dikleşen eğri)"))
    if reel is not None:
        p.append(f"10Y reel getiri %{reel:.2f} " +
                 ("— KISITLAYICI bölge (>%2), getirisiz varlıkların fırsat maliyeti yüksek"
                  if reel > 2 else "— gevşek bölge (<%1), getirisiz varlıklar lehine"
                  if reel < 1 else "— nötr bant"))
    metin = "; ".join(p) + "."
    if skor > 0.25:
        metin += (" Çıkarım: gevşeme beklentisi likidite fiyatlamasını açar; dolar zayıflama "
                  "eğilimine girer, BTC gibi getirisiz/uzun-vadeli varlıklar en çok fayda görür.")
    elif skor < -0.25:
        metin += (" Çıkarım: yüksek reel getiri + gevşeme beklentisinin geri çekilmesi, "
                  "risk varlıklarından çıkışı besler — BTC için en sert baskı kanalı budur.")
    else:
        metin += " Çıkarım: politika beklentisi çıpalanmış; yön bir sonraki enflasyon/istihdam verisinde."
    return {"ad": "PARA POLİTİKASI & PİYASA BEKLENTİSİ", "ikon": "🏛", "durum": durum,
            "skor": round(skor, 2), "metin": metin,
            "olcumler": {"politika_faizi": ff, "tahvil_2y": y2, "tahvil_10y": y10,
                         "makas": makas, "ima_25bp_adim": ima_adim,
                         "egri_10_2": egri, "reel_10y": reel}}


def _blok_likidite(g, carry=None):
    dxy = _dg(g, "dxy")
    dxy_trend = ((g or {}).get("dxy") or {}).get("trend")
    y10 = _dg(g, "us10y")
    vix = (((carry or {}).get("gostergeler") or {}).get("vix") or {}).get("fiyat")
    carry_risk = (carry or {}).get("risk")
    if dxy is None and vix is None and carry_risk is None:
        return None
    skor = 0.0
    if dxy_trend == "asagi":
        skor += 0.45
    elif dxy_trend == "yukari":
        skor -= 0.45
    if vix is not None:
        skor += 0.25 if vix < 20 else (-0.4 if vix > 25 else 0.0)
    if carry_risk == "YÜKSEK":
        skor -= 0.4
    elif carry_risk == "DÜŞÜK":
        skor += 0.15
    skor = _kirp(skor)
    durum = "GENİŞ" if skor > 0.2 else "SIKIŞIK" if skor < -0.2 else "NÖTR"
    p = []
    if dxy is not None:
        p.append(f"Dolar endeksi {dxy:.2f} ({dxy_trend or 'yön belirsiz'}) — BTC ile tarihsel "
                 f"negatif korelasyon; dolar {'zayıflaması BTC lehine' if dxy_trend == 'asagi' else 'güçlenmesi BTC aleyhine' if dxy_trend == 'yukari' else 'yatay, baskı yok'}")
    if y10 is not None:
        p.append(f"10Y getiri %{y10:.2f} iskonto oranı çıpası")
    if vix is not None:
        p.append(f"volatilite endeksi {vix:.1f} ({'sakin' if vix < 20 else 'gergin' if vix > 25 else 'normal'})")
    if carry_risk:
        p.append(f"yen carry unwind riski {carry_risk} "
                 f"({(carry or {}).get('unwind_sinyalleri', 0)}/5 sinyal)")
    metin = "; ".join(p) + "."
    metin += (" Çıkarım: finansal koşullar gevşek — risk varlıklarına sermaye akışı için uygun zemin."
              if durum == "GENİŞ" else
              " Çıkarım: finansal koşullar sıkışıyor; kaldıraçlı pozisyonlar ilk kapananlar olur, "
              "BTC'de sert fitiller beklenmeli." if durum == "SIKIŞIK" else
              " Çıkarım: likidite tarafı yön vermiyor, makro veriler belirleyici.")
    return {"ad": "DOLAR & LİKİDİTE", "ikon": "💵", "durum": durum, "skor": round(skor, 2),
            "metin": metin,
            "olcumler": {"dolar_endeksi": dxy, "tahvil_10y": y10, "volatilite": vix,
                         "carry_riski": carry_risk}}


_BLOK_AGIRLIK = {"ENFLASYON": 0.28, "PARA POLİTİKASI & PİYASA BEKLENTİSİ": 0.27,
                 "İSTİHDAM": 0.20, "DOLAR & LİKİDİTE": 0.15, "BÜYÜME": 0.10}


def _katalist_yorum(olay: dict, g: dict) -> str:
    """Sıradaki açıklama gelince NE DEĞİŞİR — sayısal eşikle."""
    kod = olay.get("kod")
    cpi_y = _dg(g, "cpi", "yillik")
    pce_y = _dg(g, "pce")
    nfp3 = _dg(g, "nfp", "ort_3ay")
    if kod == "CPI":
        e = f"%{cpi_y:.1f}" if cpi_y is not None else "önceki yıllık"
        return (f"Yıllık {e} ÜSTÜ gelirse indirim beklentisi geriler → dolar güçlenir → BTC baskı. "
                f"ALTI gelirse dezenflasyon teyidi → BTC pozitif.")
    if kod == "PCE":
        e = f"%{pce_y:.1f}" if pce_y is not None else "önceki"
        return (f"Fed'in resmî göstergesi. {e} altı → indirim gerekçesi güçlenir (BTC pozitif); "
                f"üstü → 'higher for longer' (BTC baskı).")
    if kod == "NFP":
        e = f"{nfp3:+,.0f} bin".replace(",", ".") if nfp3 is not None else "3-ay ortalaması"
        return (f"3 aylık ortalama {e}. 100 bin ALTI → gevşeme beklentisi güçlenir (BTC pozitif); "
                f"250 bin ÜSTÜ → indirim ötelenir (BTC baskı).")
    if kod == "PPI":
        return "Üretici tarafı öncü gösterge; sürpriz yukarı → 2-3 ay sonra TÜFE'ye baskı."
    if kod == "CLAIMS":
        return "260 bin üstü kalıcı seri → iş piyasası kırılma sinyali, Fed gevşemesi hızlanır."
    if kod in ("ISM_IMALAT", "ISM_HIZMET"):
        return "50 eşiği: altı daralma (risk-off + pivot beklentisi), üstü genişleme (risk-on)."
    if kod == "FOMC":
        return "Faiz kararı + nokta grafiği; makro tablodaki tüm beklentiyi tek seferde yeniden fiyatlar."
    if kod == "GDP":
        return "Büyüme sürprizi: güçlü → indirim öteler, zayıf → resesyon/pivot fiyatlaması."
    return "Yüksek etkili açıklama — pencere boyunca volatilite artar."


def makro_sentez(veri: dict, carry: dict | None = None) -> dict:
    """
    Tüm makro tablosunu tek bir okunabilir sentezde toplar:
    bloklar (geçmiş→güncel→çıkarım) + ağırlıklı net skor + piyasa beklentisi +
    sıradaki katalistler + tezi bozacak koşul. Kural-tabanlı, dış LLM YOK.
    """
    g = (veri or {}).get("gostergeler", {}) or {}
    bloklar = [b for b in (_blok_enflasyon(g), _blok_istihdam(g), _blok_buyume(g),
                           _blok_politika(g), _blok_likidite(g, carry)) if b]

    toplam_agirlik = sum(_BLOK_AGIRLIK.get(b["ad"], 0.1) for b in bloklar) or 1.0
    net = sum(b["skor"] * _BLOK_AGIRLIK.get(b["ad"], 0.1) for b in bloklar) / toplam_agirlik
    skor100 = int(round(net * 100))
    if skor100 >= 25:
        egilim, baslik = "POZİTİF", "Makro rüzgâr BTC'nin ARKASINDAN esiyor"
    elif skor100 <= -25:
        egilim, baslik = "NEGATİF", "Makro rüzgâr BTC'nin KARŞISINDAN esiyor"
    elif abs(skor100) < 10:
        egilim, baslik = "NÖTR", "Makro tablo dengede — yön katalistten gelecek"
    else:
        egilim, baslik = ("NÖTR/POZİTİF" if skor100 > 0 else "NÖTR/NEGATİF"), \
                         "Makro tablo hafif " + ("destekleyici" if skor100 > 0 else "baskılayıcı")

    rejim = " + ".join(b["durum"] for b in bloklar[:3] if b.get("durum")) or "BELİRSİZ"

    # Sıradaki katalistler + bugün açıklananlar
    bugun, sonraki = [], []
    try:
        from makro_takvim import bugunku_aciklamalar, sonraki_olaylar
        bugun = bugunku_aciklamalar()
        sonraki = [{**o, "yorum": _katalist_yorum(o, g)}
                   for o in sonraki_olaylar(4, yalniz_yuksek=False)]
    except Exception:
        pass

    # Tezi BOZACAK koşul (falsification — sayısal, ölçülebilir)
    cpi_y, nfp3 = _dg(g, "cpi", "yillik"), _dg(g, "nfp", "ort_3ay")
    if egilim.startswith("POZİTİF") or egilim == "NÖTR/POZİTİF":
        gecersizlik = (f"TÜFE yıllığı {'%' + format(cpi_y + 0.3, '.1f') if cpi_y is not None else 'yukarı'} "
                       f"üstüne dönerse, ya da 3-aylık NFP ortalaması 250 bini aşarsa gevşeme tezi bozulur "
                       f"— o noktada makro destek NEGATİFE döner.")
    elif egilim.startswith("NEGATİF") or egilim == "NÖTR/NEGATİF":
        gecersizlik = ("Çekirdek enflasyonda üst üste 2 ay soğuma veya işsizlikte dipten +0.3 puan "
                       "artış görülürse baskı tezi bozulur — makro hızla destekleyiciye döner.")
    else:
        gecersizlik = ("Dengeyi bozacak eşikler: TÜFE yıllığında ±0.3 puanlık sürpriz, "
                       "3-aylık NFP ortalamasında 100 bin altı / 250 bin üstü kırılım.")

    # Düz metin özet (site kartı + Telegram + lider bağlamı aynı metni kullanır)
    satir = [f"{baslik} (net makro skor {skor100:+d}/100 · eğilim {egilim}).",
             f"Rejim: {rejim}."]
    if bugun:
        gecen = [o for o in bugun if o.get("gecti")]
        bekleyen = [o for o in bugun if not o.get("gecti")]
        if gecen:
            satir.append("BUGÜN AÇIKLANAN: " + ", ".join(f"{o['ad']} ({o['saat_utc']} UTC)" for o in gecen) + ".")
        if bekleyen:
            satir.append("BUGÜN BEKLENEN: " + ", ".join(f"{o['ad']} ({o['saat_utc']} UTC)" for o in bekleyen) + ".")
    for b in bloklar:
        satir.append(f"{b['ikon']} {b['ad']} — {b['durum']}: {b['metin']}")
    if sonraki:
        satir.append("SIRADAKİ KATALİSTLER: " + " | ".join(
            f"{o['tarih']} {o['saat_utc']} UTC {o['ad']}" + ("" if o.get("kesin") else " (tahmini tarih)")
            for o in sonraki))
        satir.append("Ne değiştirir: " + " ".join(
            f"[{o['kod']}] {o['yorum']}" for o in sonraki[:2]))
    satir.append(f"Tezi bozacak koşul: {gecersizlik}")

    eksik = [ (GOSTERGE_META.get(k) or (k,))[0]
              for k in ANA_GOSTERGELER + HIZLI_GOSTERGELER
              if not (g.get(k) and not g[k].get("veri_yok")) ]

    return {
        "baslik": baslik, "egilim": egilim, "skor": skor100, "rejim": rejim,
        "bloklar": bloklar, "bugun": bugun, "sonraki": sonraki,
        "gecersizlik": gecersizlik,
        "ozet": "\n".join(satir),
        "eksik_gostergeler": eksik,
        "guncellendi": (veri or {}).get("guncellendi"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  OTOMATİK TAZELEME + YENİ VERİ BİLDİRİMİ
# ═══════════════════════════════════════════════════════════════════════════════
# Kullanıcı: "site güncel değil, otomatik çekmiyor." ÖNCE: makro veri YALNIZ
# kullanıcı sayfayı açınca çekiliyordu (lazy). Açıklama saatinde kimse sayfada
# değilse veri hiç tazelenmiyor, üstelik hiçbir yere bildirilmiyordu.
async def makro_yenile_loop():
    """Arka planda makroyu tazeler; YENİ veri açıklandığında Telegram'a bildirir."""
    await asyncio.sleep(45)
    while True:
        bekle = 600
        try:
            from makro_takvim import aktif_olay_penceresi
            if aktif_olay_penceresi():
                bekle = 120          # açıklama penceresinde sık kontrol
        except Exception:
            pass
        try:
            veri = await makro_veri(refresh=True)
            await _yeni_veri_bildir(veri)
        except Exception as e:
            print(f"[Makro] yenileme hatası: {str(e)[:80]}", flush=True)
        await asyncio.sleep(bekle)


async def _yeni_veri_bildir(veri: dict):
    """
    Yeni açıklanan makro verisini merkezi ajan kanalına bildirir.
    Dedup: (gösterge, dönem) çifti bir kez bildirilir (disk kalıcı) — §6b gürültü
    yasağına uyar: yalnız GERÇEKTEN yeni veri geldiğinde konuşur, aksi hâlde sessiz.
    """
    yeni = (veri or {}).get("yeni_veriler") or []
    if not yeni:
        return
    bildirilen = set(_makro_disk_yukle_ad("makro_bildirilen") or [])
    gonderilecek = [y for y in yeni if f"{y['anahtar']}|{y['donem']}" not in bildirilen]
    if not gonderilecek:
        return
    try:
        from ajan_merkez import bildir
    except Exception:
        return
    sentez = makro_sentez(veri)
    for y in gonderilecek:
        deger = y.get("deger")
        onc = y.get("onceki_deger")
        satir = f"{y['ad']} — {y['donem']}: {deger}"
        if onc is not None:
            satir += f" (önceki {onc})"
        await bildir("Makro Takip", "durum",
                     f"YENİ VERİ: {satir}",
                     f"Makro eğilim {sentez['egilim']} (skor {sentez['skor']:+d}). "
                     f"{sentez['rejim']}.")
        bildirilen.add(f"{y['anahtar']}|{y['donem']}")
    _makro_disk_kaydet_ad("makro_bildirilen", sorted(bildirilen)[-400:])
