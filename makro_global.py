"""
makro_global.py — KÜRESEL MAKRO TAKVİMİ (ABD + Euro Bölgesi + Japonya)
═══════════════════════════════════════════════════════════════════════════════
Kullanıcı isteği: "En aşağı ABD, JPY ve Euro bölgesi makro veri takvimi ve
beklentiler, ayrıca beklentilere paralel yorumlamalar, iyi-kötü senaryolar."

ÜÇ PARÇA:
  1) TAKVİM      → makro_takvim.takvim(bolgeler=[ABD,AB,JAPONYA]) (kronolojik, UTC)
  2) BEKLENTİ    → iki katman, ikisi de ETİKETLİ (uydurma sayı YOK):
       a. MANUEL KONSENSÜS — makro_takvim_override.json'daki `beklenti` alanı.
          Kullanıcı girerse bu kullanılır, "konsensüs" diye gösterilir.
       b. OAR PROJEKSİYONU — göstergenin KENDİ serisinden türetilir (3-ay
          yıllıklandırılmış hız, 3-ay ortalama, trend eğimi). Ekranda açıkça
          "piyasa konsensüsü DEĞİL" yazar.
     ⚠ Ücretsiz + anahtarsız gerçek konsensüs API'si YOK (ANAYASA #3) — bu yüzden
       konsensüs uydurulmuyor, kaynak her zaman etiketleniyor.
  3) SENARYO     → her olay için SICAK / PARALEL / SOĞUK dalı; "iyi/kötü" RİSK
     VARLIĞI açısından tanımlı (ekonomi açısından değil: sıcak enflasyon ekonomi
     için güç, BTC için baskıdır).

⭐ BÖLGE KANALI (bu modülün asıl katma değeri — üç bölge AYNI yönde çalışmaz):
  • ABD      → doğrudan dolar/faiz kanalı.  Sıcak ABD verisi = BTC'ye BASKI.
  • Euro     → TERS kanal. Sıcak AB verisi → ECB şahin → EUR güçlü → dolar
               endeksi ZAYIF → BTC'ye DESTEK. (Çoğu kişinin ters okuduğu yer.)
  • Japonya  → CARRY kanalı. Sıcak JP verisi → BoJ şahin → yen güçlü → carry
               unwind → küresel risk varlıklarında satış → BTC'ye BASKI.

VERİ SINIRI (dürüst): Euro Bölgesi gerçekleşen değerleri anahtarsız ECB veri
servisinden gelir. Japonya için anahtarsız güvenilir resmî kaynak YOK — TÜFE
ancak FRED anahtarıyla gelir; gelmezse "veri yok" yazar (uydurulmaz). Takvim ve
senaryolar her hâlükârda çalışır, çünkü onlar gerçekleşen değere bağlı değildir.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import time
from datetime import datetime, timezone

HDR = {"Accept": "application/json", "User-Agent": "OAR-MacroDesk/1.0"}
TO = 15.0

# ═══════════════════════════════════════════════════════════════════
#  EURO BÖLGESİ — ECB veri servisi (ANAHTARSIZ)
# ═══════════════════════════════════════════════════════════════════
_ECB_URL = "https://data-api.ecb.europa.eu/service/data/{anahtar}?format=csvdata&lastNObservations={n}"

# (seri anahtarı, görünen ad, birim). Mevduat faizi gelmezse ana refinansman
# faizine düşülür (ECB politika faizi tanımı zamanla değişti).
ECB_SERILER = {
    "ab_hicp":     ("ICP/M.U2.N.000000.4.ANR",            "Euro Bölgesi enflasyon (yıllık)", "%"),
    "ab_cekirdek": ("ICP/M.U2.N.XEF000.4.ANR",            "Çekirdek enflasyon (yıllık)",     "%"),
    "ab_faiz":     ("FM/D.U2.EUR.4F.KR.DFR.LEV",          "ECB politika faizi",              "%"),
    "ab_issizlik": ("LFSI/M.I9.S.UNEHRT.TOTAL0.15_74.T",  "Euro Bölgesi işsizlik",           "%"),
}
_ECB_YEDEK = {"ab_faiz": "FM/D.U2.EUR.4F.KR.MRR_FR.LEV"}

_cache = {"veri": None, "ts": 0}
_CACHE_SN = 900.0        # 15 dk — AB/JP verisi günlük-aylık çözünürlükte


def _ecb_csv_coz(metin: str) -> list[dict]:
    """ECB CSV → artan sıralı [{tarih, deger}]. Kolonlar ADIYLA bulunur."""
    try:
        rd = csv.DictReader(io.StringIO(metin))
        basliklar = [h for h in (rd.fieldnames or []) if h]
    except Exception:
        return []
    t_kol = next((h for h in basliklar if h.strip().upper() == "TIME_PERIOD"), None)
    v_kol = next((h for h in basliklar if h.strip().upper() == "OBS_VALUE"), None)
    if not t_kol or not v_kol:
        return []
    out = []
    for satir in rd:
        t = (satir.get(t_kol) or "").strip()
        try:
            d = float(str(satir.get(v_kol) or "").strip())
        except Exception:
            continue
        if t:
            out.append({"tarih": t, "deger": round(d, 3)})
    out.sort(key=lambda x: x["tarih"])
    return out


async def _ecb_seri(cl, anahtar: str, n: int = 16) -> list[dict]:
    try:
        r = await cl.get(_ECB_URL.format(anahtar=anahtar, n=n),
                         headers={**HDR, "Accept": "text/csv"}, timeout=TO)
        if r.status_code != 200:
            return []
        return _ecb_csv_coz(r.text)
    except Exception:
        return []


async def ab_veri(cl) -> dict:
    """Euro Bölgesi göstergeleri (anahtarsız). Gelmeyen gösterge 'veri yok'."""
    out = {}
    for k, (anahtar, ad, birim) in ECB_SERILER.items():
        rows = await _ecb_seri(cl, anahtar)
        if not rows and k in _ECB_YEDEK:
            rows = await _ecb_seri(cl, _ECB_YEDEK[k])
        out[k] = _gosterge(rows, ad, birim, "ECB veri servisi")
    return out


# ═══════════════════════════════════════════════════════════════════
#  JAPONYA — anahtarsız resmî kaynak YOK; FRED varsa oradan, yoksa 'veri yok'
# ═══════════════════════════════════════════════════════════════════
async def jp_veri(cl) -> dict:
    out = {}
    try:
        from macro_engine import _fred
    except Exception:
        _fred = None

    tufe = await _fred(cl, "JPNCPIALLMINMEI", 26) if _fred else None
    if tufe and len(tufe) >= 13:
        ys = [{"tarih": tufe[i]["tarih"],
               "deger": round((tufe[i]["deger"] - tufe[i - 12]["deger"]) / tufe[i - 12]["deger"] * 100, 2)}
              for i in range(12, len(tufe)) if tufe[i - 12]["deger"]]
        out["jp_tufe"] = _gosterge(ys, "Japonya enflasyon (yıllık)", "%", "FRED")
    else:
        out["jp_tufe"] = _gosterge([], "Japonya enflasyon (yıllık)", "%", "",
                                   neden="anahtarsız resmî kaynak yok")

    # BoJ politika faizi + yen: carry panelinde ZATEN toplanıyor → tekrar çekme
    try:
        from macro_engine import carry_trade
        c = await carry_trade()
        cg = (c or {}).get("gostergeler") or {}
        boj, usdjpy = cg.get("boj") or {}, cg.get("usdjpy") or {}
        if boj.get("fiyat") is not None:
            out["jp_faiz"] = _gosterge([{"tarih": datetime.now(timezone.utc).strftime("%Y-%m"),
                                         "deger": boj["fiyat"]}],
                                       "BoJ politika faizi", "%", "carry paneli")
        if usdjpy.get("fiyat") is not None:
            out["usdjpy"] = _gosterge([{"tarih": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                                        "deger": round(usdjpy["fiyat"], 2)}],
                                      "USD/JPY", "", "carry paneli")
            out["usdjpy"]["degisim"] = usdjpy.get("chg")
        out["carry_riski"] = (c or {}).get("risk")
    except Exception:
        pass
    return out


def _gosterge(rows, ad, birim, kaynak, neden="kaynak yanıt vermedi") -> dict:
    if not rows:
        return {"ad": ad, "birim": birim, "guncel": None, "veri_yok": True,
                "kaynak": kaynak, "neden": neden, "gecmis": []}
    son = rows[-1]
    onc = rows[-2] if len(rows) >= 2 else None
    return {"ad": ad, "birim": birim, "guncel": son["deger"], "tarih": son["tarih"],
            "onceki": onc["deger"] if onc else None,
            "degisim": round(son["deger"] - onc["deger"], 2) if onc else None,
            "gecmis": rows[-14:], "kaynak": kaynak}


# ═══════════════════════════════════════════════════════════════════
#  BEKLENTİ ÜRETİMİ — OAR projeksiyonu (konsensüs DEĞİL, öyle etiketlenir)
# ═══════════════════════════════════════════════════════════════════
# olay kodu → (gösterge anahtarı, yöntem, birim, "paralel" bandı, kategori)
# band = bu kadarlık sapma "beklentiye paralel" sayılır; aşarsa sıcak/soğuk dal.
PROJEKSIYON = {
    # ABD
    "CPI":        ("cpi",        "hiz3",      "%",    0.2,   "enflasyon"),
    "PCE":        ("pce",        "trend",     "%",    0.2,   "enflasyon"),
    "PPI":        ("ppi",        "trend",     "%",    0.3,   "enflasyon"),
    "NFP":        ("nfp",        "ort3",      " bin", 50,    "istihdam"),
    "CLAIMS":     ("claims",     "ort4",      "",     15000, "issizlik"),
    "GDP":        ("gsyih",      "son",       "%",    0.3,   "buyume"),
    "ISM_IMALAT": ("ism",        "trend",     "",     1.0,   "buyume"),
    "ISM_HIZMET": (None,         None,        "",     1.0,   "buyume"),
    "PERAKENDE":  ("perakende",  "trend",     "%",    0.5,   "buyume"),
    "SANAYI":     ("sanayi",     "trend",     "%",    0.5,   "buyume"),
    "GUVEN":      ("guven",      "trend",     "",     2.0,   "buyume"),
    "FOMC":       ("fedFaiz",    "son",       "%",    0.24,  "politika"),
    # Euro Bölgesi
    "HICP_FLASH": ("ab_hicp",    "trend",     "%",    0.2,   "enflasyon"),
    "HICP_FINAL": ("ab_hicp",    "son",       "%",    0.1,   "enflasyon"),
    "AB_GDP":     ("ab_gsyih",   "son",       "%",    0.3,   "buyume"),
    "AB_ISSIZLIK": ("ab_issizlik", "trend",   "%",    0.1,   "issizlik"),
    "PMI_FLASH":  (None,         None,        "",     1.0,   "buyume"),
    "ZEW":        (None,         None,        "",     3.0,   "buyume"),
    "IFO":        (None,         None,        "",     1.0,   "buyume"),
    "ECB":        ("ab_faiz",    "son",       "%",    0.24,  "politika"),
    # Japonya
    "JP_CPI":       ("jp_tufe",  "trend",     "%",    0.2,   "enflasyon"),
    "JP_TOKYO_CPI": ("jp_tufe",  "trend",     "%",    0.2,   "enflasyon"),
    "JP_GDP":       ("jp_gsyih", "son",       "%",    0.3,   "buyume"),
    "TANKAN":       (None,       None,        "",     2.0,   "buyume"),
    "JP_UCRET":     (None,       None,        "%",    0.3,   "buyume"),
    "BOJ":          ("jp_faiz",  "son",       "%",    0.09,  "politika"),
}


def _seri(g: dict, k: str, n: int = 6) -> list[float]:
    v = (g or {}).get(k) or {}
    s = v.get("gecmis12") or v.get("gecmis") or []
    return [d["deger"] for d in s[-n:] if isinstance(d, dict) and d.get("deger") is not None]


def _egim(vals: list[float]) -> float:
    """Basit doğrusal eğim (son n nokta) — trend projeksiyonu için."""
    n = len(vals)
    if n < 2:
        return 0.0
    ort_i, ort_v = (n - 1) / 2, sum(vals) / n
    payda = sum((i - ort_i) ** 2 for i in range(n))
    if not payda:
        return 0.0
    return sum((i - ort_i) * (v - ort_v) for i, v in enumerate(vals)) / payda


def beklenti_uret(olay: dict, g: dict) -> dict | None:
    """
    Bir açıklama için beklenti. Öncelik:
      1) override'daki manuel konsensüs (kaynak='konsensüs')
      2) OAR projeksiyonu (kaynak='OAR projeksiyonu' — konsensüs DEĞİL)
    Hiçbiri hesaplanamıyorsa None (kolon boş kalır, sayı uydurulmaz).
    """
    kod = olay.get("kod")
    tanim = PROJEKSIYON.get(kod)
    birim = (tanim[2] if tanim else "")
    band = (tanim[3] if tanim else 0.2)
    kategori = (tanim[4] if tanim else "buyume")

    manuel = olay.get("beklenti_girdi")
    if manuel is not None:
        try:
            return {"deger": float(manuel), "birim": birim, "band": band,
                    "kategori": kategori, "kaynak": "konsensüs",
                    "kaynak_not": "elle girilen piyasa konsensüsü",
                    "onceki": olay.get("onceki_girdi")}
        except Exception:
            pass

    if not tanim or not tanim[0]:
        return {"deger": None, "birim": birim, "band": band, "kategori": kategori,
                "kaynak": "yok",
                "kaynak_not": "bu gösterge için seri tutulmuyor — beklenti üretilemiyor",
                "onceki": olay.get("onceki_girdi")}

    anahtar, yontem = tanim[0], tanim[1]
    v = (g or {}).get(anahtar) or {}
    if v.get("veri_yok") or v.get("guncel") is None:
        return {"deger": None, "birim": birim, "band": band, "kategori": kategori,
                "kaynak": "yok", "kaynak_not": "gösterge verisi okunamıyor",
                "onceki": None}

    guncel = v.get("guncel")
    deger, aciklama = None, ""
    if yontem == "son":
        deger, aciklama = guncel, "son açıklanan değerin tekrarı varsayıldı"
    elif yontem == "trend":
        s = _seri(g, anahtar, 5)
        deger = round(guncel + _egim(s), 2) if len(s) >= 2 else guncel
        aciklama = "son 5 gözlemin eğilimi bir dönem ileri taşındı"
    elif yontem == "hiz3":
        # Endeks serisi (TÜFE): son 3 ayın yıllıklandırılmış hızı, yıllık için projeksiyon
        s = _seri(g, anahtar, 4)
        if len(s) >= 4 and s[0]:
            try:
                deger = round(((s[-1] / s[0]) ** 4 - 1) * 100, 1)
                aciklama = "son 3 ayın hızı 12 aya yıllıklandırıldı"
            except Exception:
                deger = None
        if deger is None:
            deger = v.get("yillik") if v.get("yillik") is not None else guncel
            aciklama = "son yıllık değer taşındı"
    elif yontem == "ort3":
        s = _seri(g, anahtar, 3)
        deger = round(sum(s) / len(s)) if s else guncel
        aciklama = "son 3 ayın ortalaması"
    elif yontem == "ort4":
        s = _seri(g, anahtar, 4)
        deger = round(sum(s) / len(s)) if s else guncel
        aciklama = "son 4 haftanın ortalaması"

    if deger is None:
        return None
    return {"deger": deger, "birim": birim, "band": band, "kategori": kategori,
            "kaynak": "OAR projeksiyonu",
            "kaynak_not": f"{aciklama} — piyasa konsensüsü DEĞİLDİR",
            "onceki": guncel, "onceki_donem": v.get("tarih")}


# ═══════════════════════════════════════════════════════════════════
#  SENARYO MOTORU — "iyi/kötü" RİSK VARLIĞI açısından
# ═══════════════════════════════════════════════════════════════════
# Zincir: veri sürprizi → faiz/merkez bankası beklentisi → para birimi kanalı → BTC.
# Bölge kanalı ters çalışabilir; asıl katma değer burada.
def _zincir(kategori: str, kanal: str, sicak: bool) -> tuple[str, str]:
    """(etiket, zincir metni) — sicak=True → veri beklentinin ÜSTÜNDE geldi."""
    if kategori == "enflasyon":
        if kanal == "dolar":            # ABD
            return (("KÖTÜ", "Enflasyon beklenti üstü → faiz indirimi ötelenir → reel getiri ve dolar yükselir → BTC'de satış baskısı.")
                    if sicak else
                    ("İYİ", "Enflasyon beklenti altı → dezenflasyon teyidi → indirim beklentisi öne çekilir → dolar zayıflar → BTC'ye destek."))
        if kanal == "dolar_ters":       # Euro Bölgesi — TERS kanal
            return (("İYİ", "AB enflasyonu beklenti üstü → ECB şahin → euro güçlenir → dolar endeksi ZAYIFLAR → BTC'ye destek. (ABD verisinin tersi yönde çalışır.)")
                    if sicak else
                    ("KÖTÜ", "AB enflasyonu beklenti altı → ECB güvercin → euro zayıflar → dolar endeksi GÜÇLENİR → BTC'ye baskı."))
        return (("KÖTÜ", "Japonya enflasyonu beklenti üstü → BoJ sıkılaşma beklentisi → yen güçlenir → yen ile fonlanan carry pozisyonları kapanır → küresel risk varlıklarında satış → BTC'ye baskı.")
                if sicak else
                ("İYİ", "Japonya enflasyonu beklenti altı → BoJ gevşek kalır → yen zayıf → carry akışı sürer → risk varlıkları için destekleyici zemin."))

    if kategori == "istihdam":
        if kanal == "dolar":
            return (("KÖTÜ", "İstihdam beklenti üstü → iş piyasası sıkı → Fed indirimi öteler → dolar ve getiriler yukarı → BTC'ye baskı.")
                    if sicak else
                    ("İYİ", "İstihdam beklenti altı → soğuma → gevşeme beklentisi güçlenir → BTC'ye destek. UYARI: 50 binin çok altı 'soğuma' değil ÇÖKÜŞ okunur; ilk tepki resesyon fiyatlamasıyla risk-off olabilir."))
        if kanal == "dolar_ters":
            return (("İYİ", "AB istihdamı güçlü → ECB'ye sıkı duruş alanı → euro güçlü → dolar zayıf → BTC'ye destek.")
                    if sicak else
                    ("KÖTÜ", "AB istihdamı zayıf → ECB güvercin → euro zayıf → dolar güçlü → BTC'ye baskı."))
        return (("KÖTÜ", "Japonya'da güçlü istihdam/ücret → BoJ normalleşme beklentisi → yen güçlenir → carry unwind → BTC'ye baskı.")
                if sicak else
                ("İYİ", "Japonya'da zayıf ücret verisi → BoJ acele etmez → yen zayıf kalır → carry akışı korunur."))

    if kategori == "issizlik":
        # Yüksek işsizlik/başvuru = ekonomi için kötü, faiz kanalı üzerinden risk varlığı için iyi
        if kanal == "dolar":
            return (("İYİ", "İşsizlik/başvuru beklenti üstü → iş piyasası soğuyor → Fed gevşemeye yaklaşır → BTC'ye destek. UYARI: sert bozulma resesyon fiyatlamasına dönerse ilk fazda risk-off gelir.")
                    if sicak else
                    ("KÖTÜ", "İşsizlik/başvuru beklenti altı → iş piyasası sıkı → indirim gerekçesi zayıflar → BTC'ye baskı."))
        if kanal == "dolar_ters":
            return (("KÖTÜ", "AB'de işsizlik beklenti üstü → ECB güvercin → euro zayıf → dolar güçlü → BTC'ye baskı.")
                    if sicak else
                    ("İYİ", "AB'de işsizlik beklenti altı → ECB'ye alan → euro güçlü → dolar zayıf → BTC'ye destek."))
        return (("İYİ", "Japonya iş piyasası zayıf → BoJ gevşek kalır → yen zayıf → carry korunur.")
                if sicak else
                ("KÖTÜ", "Japonya iş piyasası sıkı → BoJ normalleşme baskısı → yen güçlü → carry unwind riski."))

    if kategori == "politika":
        if kanal == "dolar":
            return (("KÖTÜ", "Faiz beklentinin üstünde / şahin ton → gevşeme fiyatlaması geri alınır → dolar ve reel getiri yukarı → BTC'ye sert baskı.")
                    if sicak else
                    ("İYİ", "Faiz beklentinin altında / güvercin ton → likidite beklentisi açılır → dolar zayıflar → BTC en çok fayda gören varlıklardan."))
        if kanal == "dolar_ters":
            return (("İYİ", "ECB beklenenden şahin → euro güçlenir → dolar endeksi zayıflar → BTC'ye destek.")
                    if sicak else
                    ("KÖTÜ", "ECB beklenenden güvercin → euro zayıflar → dolar endeksi güçlenir → BTC'ye baskı."))
        return (("KÖTÜ", "BoJ beklenenden şahin (faiz artışı / tahvil alım kısıntısı) → yen sert güçlenir → carry unwind → 2024 Ağustos benzeri zincirleme satış riski → BTC için en kritik dış tehdit.")
                if sicak else
                ("İYİ", "BoJ gevşek kalıyor → yen zayıf → carry fonlaması ucuz → küresel risk varlıklarına akış sürer."))

    # buyume (GSYİH, PMI, güven, Tankan, perakende, sanayi)
    if kanal == "dolar":
        return (("KARIŞIK", "Büyüme beklenti üstü → risk iştahı olumlu AMA faiz indirimini öteler; iki etki birbirini kısmen dengeler, net etki ılımlı.")
                if sicak else
                ("KARIŞIK", "Büyüme beklenti altı → gevşeme beklentisi güçlenir ama talep/kazanç endişesi artar; net etki ılımlı, sert sapmada risk-off baskın."))
    if kanal == "dolar_ters":
        return (("İYİ", "AB büyümesi beklenti üstü → euro güçlenir → dolar endeksi zayıflar → BTC'ye destek.")
                if sicak else
                ("KÖTÜ", "AB büyümesi beklenti altı → euro zayıflar → dolar endeksi güçlenir → BTC'ye baskı."))
    return (("KÖTÜ", "Japonya büyümesi beklenti üstü → BoJ normalleşme alanı → yen güçlenir → carry unwind → BTC'ye baskı.")
            if sicak else
            ("İYİ", "Japonya büyümesi beklenti altı → BoJ gevşek kalır → yen zayıf → carry akışı korunur."))


def senaryolar(olay: dict, beklenti: dict | None) -> list[dict]:
    """Bir açıklama için SICAK / PARALEL / SOĞUK dalları + BTC etkisi."""
    kategori = (beklenti or {}).get("kategori") or \
               (PROJEKSIYON.get(olay.get("kod")) or (None, None, "", 0.2, "buyume"))[4]
    kanal = olay.get("kanal") or "dolar"
    birim = (beklenti or {}).get("birim") or ""
    b = (beklenti or {}).get("deger")
    band = (beklenti or {}).get("band") or 0.2

    def _esik(yon):
        if b is None:
            return "beklentinin üstü" if yon > 0 else "beklentinin altı"
        v = b + band * yon
        s = f"{v:,.0f}".replace(",", ".") if abs(v) >= 1000 else f"{v:.2f}".rstrip("0").rstrip(".")
        return ("≥ " if yon > 0 else "≤ ") + s + birim

    sicak_et, sicak_tx = _zincir(kategori, kanal, True)
    soguk_et, soguk_tx = _zincir(kategori, kanal, False)
    return [
        {"dal": "SICAK", "esik": _esik(+1), "yon": "beklenti ÜSTÜ",
         "etiket": sicak_et, "metin": sicak_tx},
        {"dal": "PARALEL", "esik": (f"±{band}{birim} bandı" if b is not None else "beklentiye yakın"),
         "yon": "beklentiye PARALEL", "etiket": "NÖTR",
         "metin": "Veri beklentiyle uyumlu → mevcut faiz/dolar fiyatlaması korunur. "
                  "Yön makro değil, o anki pozisyonlanma ve teknik seviyelerden gelir; "
                  "açıklama penceresindeki ilk hareket çoğu zaman geri alınır."},
        {"dal": "SOĞUK", "esik": _esik(-1), "yon": "beklenti ALTI",
         "etiket": soguk_et, "metin": soguk_tx},
    ]


# ═══════════════════════════════════════════════════════════════════
#  DERLEME
# ═══════════════════════════════════════════════════════════════════
async def kuresel_veri(refresh: bool = False) -> dict:
    """AB + JP gerçekleşen değerleri (15 dk cache)."""
    if not refresh and _cache["veri"] and (time.time() - _cache["ts"]) < _CACHE_SN:
        return _cache["veri"]
    veri = {}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=20) as cl:
            ab, jp = await asyncio.gather(ab_veri(cl), jp_veri(cl), return_exceptions=True)
        if isinstance(ab, dict):
            veri.update(ab)
        if isinstance(jp, dict):
            veri.update(jp)
    except Exception as e:
        veri["_hata"] = str(e)[:80]
    _cache["veri"], _cache["ts"] = veri, time.time()
    return veri


async def kuresel_takvim(once_gun: int = 2, sonra_gun: int = 21,
                         min_etki: str = "YÜKSEK") -> dict:
    """
    Sitenin en altındaki küresel takvim bloğu:
    kronolojik olaylar + beklenti (etiketli) + 3 senaryo + bölge özetleri.
    """
    from makro_takvim import takvim, BOLGELER
    from macro_engine import makro_veri

    us = await makro_veri()
    kur = await kuresel_veri()
    g = dict((us or {}).get("gostergeler") or {})
    g.update(kur)                      # ABD + AB + JP göstergeleri tek sözlükte

    olaylar = takvim(once_gun, sonra_gun, ["ABD", "AB", "JAPONYA"], min_etki)
    zengin = []
    for o in olaylar:
        bek = beklenti_uret(o, g)
        zengin.append({**o, "beklenti": bek, "senaryolar": senaryolar(o, bek)})

    ileri = [o for o in zengin if not o["gecti"]]
    kritik = sorted(ileri, key=lambda o: (-o["etki_sira"], o["utc"]))[:3]

    bolge_ozet = {
        "ABD": _bolge_ozet(g, ["cpi", "isRate", "fedFaiz", "us10y"], "ABD"),
        "AB": _bolge_ozet(g, ["ab_hicp", "ab_cekirdek", "ab_faiz", "ab_issizlik"], "AB"),
        "JAPONYA": _bolge_ozet(g, ["jp_tufe", "jp_faiz", "usdjpy"], "JAPONYA"),
    }
    return {
        "olaylar": zengin,
        "kritik": kritik,
        "bolgeler": {k: {**v, **BOLGELER[k]} for k, v in bolge_ozet.items()},
        "carry_riski": kur.get("carry_riski"),
        "min_etki": min_etki,
        "beklenti_notu": ("Beklenti kolonu: elle girilmiş konsensüs varsa o kullanılır; "
                          "yoksa göstergenin kendi serisinden türetilen OAR projeksiyonu "
                          "gösterilir ve 'konsensüs değildir' diye etiketlenir."),
        "guncellendi": datetime.now(timezone.utc).isoformat(),
    }


# ABD göstergeleri macro_engine'den `ad`/`birim` alanı TAŞIMAZ (site kartlarında
# başlık ayrı yazılıyordu) → şeritte ham anahtar görünmesin diye burada eşleniyor.
_ABD_ETIKET = {
    "cpi": ("Enflasyon (TÜFE endeksi)", ""), "isRate": ("İşsizlik", "%"),
    "fedFaiz": ("Politika faizi", "%"), "nfp": ("Tarım dışı istihdam", " bin"),
    "us10y": ("10 yıllık getiri", "%"), "us2y": ("2 yıllık getiri", "%"),
}


def _bolge_ozet(g: dict, anahtarlar: list[str], bolge: str) -> dict:
    satir = []
    for k in anahtarlar:
        v = (g or {}).get(k) or {}
        if v.get("veri_yok") or v.get("guncel") is None:
            continue
        etiket = _ABD_ETIKET.get(k)
        ad = v.get("ad") or (etiket[0] if etiket else k)
        birim = v.get("birim") if v.get("birim") is not None else (etiket[1] if etiket else "")
        deger = v.get("guncel")
        if k == "cpi" and v.get("yillik") is not None:
            # Endeks seviyesi (307.8) şeritte anlamsız → yıllık % göster
            deger, ad, birim = v["yillik"], "Enflasyon (yıllık)", "%"
        satir.append({"anahtar": k, "ad": ad, "deger": deger,
                      "birim": birim, "tarih": v.get("tarih"),
                      "degisim": v.get("degisim")})
    eksik = [k for k in anahtarlar if not any(s["anahtar"] == k for s in satir)]
    return {"gostergeler": satir, "eksik": eksik}


if __name__ == "__main__":
    async def _dene():
        d = await kuresel_takvim()
        print(json.dumps({"kritik": [(o["tarih_utc"], o["ad"]) for o in d["kritik"]],
                          "olay_sayisi": len(d["olaylar"])}, ensure_ascii=False, indent=1))
    asyncio.run(_dene())
