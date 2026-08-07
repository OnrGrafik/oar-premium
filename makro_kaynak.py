"""
makro_kaynak.py — ANAHTARSIZ (keysiz) makro veri kaynakları
═══════════════════════════════════════════════════════════════════════════════
SORUN (kullanıcı): "Bugün ABD verisi açıklandı ama makro veriler kısmında yok,
site güncel değil, otomatik çekmiyor."

KÖK NEDEN: macro_engine'deki 9 göstergenin 8'i YALNIZ FRED'e bağlıydı ve FRED
API ANAHTARI GEREKTİRİR (`FRED_API_KEY`). Anahtar boş/geçersizse `_fred()` daha
ilk satırda None döner → tüm göstergeler "veri yok" → sayfa hiç güncellenmez.
Tek keysiz kaynak CPI'ın BLS flat file'ıydı.

BU MODÜL: anahtar GEREKTİRMEYEN resmi kaynakları ekler (FRED yedeği):
  • BLS Public API   → TÜFE, çekirdek TÜFE, ÜFE, NFP, işsizlik, saatlik kazanç
                       (v1 KEYSİZ, günde 25 istek; BLS_API_KEY varsa v2, 500/gün)
  • US Treasury CSV  → 3A/2Y/10Y/30Y getiri eğrisi (GÜNLÜK, keysiz, sınırsız)

Her kaynak kendi disk cache'i + istek bütçesiyle çalışır (BLS v1 sınırı aşılmaz).
Kaynak çökerse None döner — ASLA uydurma değer üretmez (macro_engine 'veri yok'
işaretler; ANAYASA #3).
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
from datetime import datetime, timezone

HDR = {"Accept": "application/json", "User-Agent": "OAR-MacroDesk/1.0"}
BLS_KEY = os.environ.get("BLS_API_KEY", "")

# BLS v1 (keysiz) günlük sınırı 25 istek/IP. 3 istek pay bırakıyoruz.
V1_GUNLUK_LIMIT = 22

# ── BLS seri kimlikleri (hepsi mevsimsellikten arındırılmış = SA) ─────────────
BLS_SERI: dict[str, tuple[str, str, str]] = {
    # anahtar        seri_id            açıklama                         tip
    "cpi":        ("CUSR0000SA0",    "TÜFE — tüm kalemler (SA)",      "endeks"),
    "cpi_cekirdek": ("CUSR0000SA0L1E", "Çekirdek TÜFE (gıda+enerji hariç)", "endeks"),
    "ppi":        ("WPSFD4",         "ÜFE — nihai talep (SA)",        "endeks"),
    "nfp":        ("CES0000000001",  "Toplam tarım dışı istihdam (bin, SA)", "seviye"),
    "isRate":     ("LNS14000000",    "İşsizlik oranı (SA)",           "oran"),
    "kazanc":     ("CES0500000003",  "Ort. saatlik kazanç ($, SA)",   "endeks"),
}


# ═══════════════════════════════════════════════════════════════════
#  Disk cache (kalıcı volume) — kaynak çökse de son iyi veri elde kalır
# ═══════════════════════════════════════════════════════════════════
def _kok():
    try:
        from data_ingest import hist_dir
        p = hist_dir() / "makro_kaynak"
    except Exception:
        from pathlib import Path
        p = Path("data") / "makro_kaynak"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def _oku(ad: str):
    try:
        p = _kok() / f"{ad}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _yaz(ad: str, obj):
    try:
        (_kok() / f"{ad}.json").write_text(json.dumps(obj, ensure_ascii=False),
                                           encoding="utf-8")
    except Exception:
        pass


def _taze_mi(kayit, saniye: float) -> bool:
    return bool(kayit) and (time.time() - float(kayit.get("ts") or 0)) < saniye


# ═══════════════════════════════════════════════════════════════════
#  BLS — istek bütçesi (v1 keysiz 25/gün sınırını AŞMA)
# ═══════════════════════════════════════════════════════════════════
# Disk yazılamayan ortamda (salt-okunur FS) sayaç sıfır kalıp sınır aşılmasın diye
# bellekte de tutuluyor; ikisinin BÜYÜĞÜ esas alınır.
_bellek_butce = {"gun": "", "sayac": 0}


def _butce_durum() -> dict:
    bugun = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    b = _oku("bls_butce") or {}
    if b.get("gun") != bugun:
        b = {"gun": bugun, "sayac": 0}
    if _bellek_butce["gun"] != bugun:
        _bellek_butce["gun"], _bellek_butce["sayac"] = bugun, 0
    b["sayac"] = max(int(b.get("sayac") or 0), _bellek_butce["sayac"])
    return b


def _butce_harca(n: int = 1):
    b = _butce_durum()
    b["sayac"] = int(b.get("sayac") or 0) + n
    _bellek_butce["gun"], _bellek_butce["sayac"] = b["gun"], b["sayac"]
    _yaz("bls_butce", b)


def bls_kalan_istek() -> int:
    """Bugün BLS v1 (keysiz) için kalan istek hakkı. Anahtar varsa sınır yok."""
    if BLS_KEY:
        return 999
    return max(0, V1_GUNLUK_LIMIT - int(_butce_durum().get("sayac") or 0))


def _bls_parse(seri_obj) -> list[dict]:
    """BLS 'data' listesini artan sıralı [{tarih:'YYYY-MM', deger:float}] yapar."""
    out = []
    for d in (seri_obj or {}).get("data", []) or []:
        per = str(d.get("period") or "")
        if not per.startswith("M") or per == "M13":   # M13 = yıllık ortalama
            continue
        try:
            v = float(str(d.get("value", "")).replace(",", "").strip())
        except Exception:
            continue
        yil = str(d.get("year") or "")
        if len(yil) != 4:
            continue
        out.append({"tarih": f"{yil}-{per[1:].zfill(2)}", "deger": v})
    out.sort(key=lambda x: x["tarih"])
    # aynı ay iki kez gelirse sonuncuyu tut
    tek = {}
    for r in out:
        tek[r["tarih"]] = r
    return [tek[k] for k in sorted(tek)]


async def _bls_v2(cl, seri_idler: list[str]) -> dict:
    """Anahtarlı toplu istek (500/gün) — tek HTTP çağrısında tüm seriler."""
    yil = datetime.now(timezone.utc).year
    govde = {"seriesid": seri_idler, "startyear": str(yil - 2), "endyear": str(yil),
             "registrationkey": BLS_KEY}
    r = await cl.post("https://api.bls.gov/publicAPI/v2/timeseries/data/",
                      json=govde, headers={**HDR, "Content-Type": "application/json"},
                      timeout=20)
    if r.status_code != 200:
        return {}
    d = r.json()
    if str(d.get("status")) != "REQUEST_SUCCEEDED":
        return {}
    out = {}
    for s in (d.get("Results") or {}).get("series", []) or []:
        rows = _bls_parse(s)
        if rows:
            out[str(s.get("seriesID"))] = rows
    return out


async def _bls_v1(cl, seri_id: str) -> list[dict] | None:
    """Keysiz tekil istek (25/gün). Son ~3 yıl döner."""
    if bls_kalan_istek() <= 0:
        return None
    r = await cl.get(f"https://api.bls.gov/publicAPI/v1/timeseries/data/{seri_id}",
                     headers=HDR, timeout=20)
    _butce_harca(1)
    if r.status_code != 200:
        return None
    d = r.json()
    if str(d.get("status")) != "REQUEST_SUCCEEDED":
        return None
    for s in (d.get("Results") or {}).get("series", []) or []:
        rows = _bls_parse(s)
        if rows:
            return rows
    return None


async def bls_getir(cl, anahtarlar: list[str], taze_saat: float = 12.0) -> dict:
    """
    İstenen BLS göstergelerini döndürür: {anahtar: [{tarih,deger}, ...]} (artan).

    taze_saat: cache bu süreden yeniyse HTTP yapılmaz (BLS v1 bütçesini korur).
    Açıklama günlerinde macro_engine bunu 1 saate düşürür.
    Kaynak çökerse SON İYİ cache döner (varsa) — uydurma yok.
    """
    anahtarlar = [a for a in anahtarlar if a in BLS_SERI]
    sonuc, yenile = {}, []
    for a in anahtarlar:
        c = _oku(f"bls_{a}")
        if _taze_mi(c, taze_saat * 3600) and c.get("rows"):
            sonuc[a] = c["rows"]
        else:
            yenile.append(a)
            if c and c.get("rows"):
                sonuc[a] = c["rows"]          # bayat ama elde — HTTP başarısızsa kalır
    if not yenile:
        return sonuc

    try:
        if BLS_KEY:
            idler = [BLS_SERI[a][0] for a in yenile]
            ham = await _bls_v2(cl, idler)
            for a in yenile:
                rows = ham.get(BLS_SERI[a][0])
                if rows:
                    sonuc[a] = rows
                    _yaz(f"bls_{a}", {"ts": time.time(), "rows": rows,
                                      "kaynak": "BLS API v2"})
        else:
            for a in yenile:
                if bls_kalan_istek() <= 0:
                    break
                try:
                    rows = await _bls_v1(cl, BLS_SERI[a][0])
                except Exception:
                    rows = None
                if rows:
                    sonuc[a] = rows
                    _yaz(f"bls_{a}", {"ts": time.time(), "rows": rows,
                                      "kaynak": "BLS API v1 (keysiz)"})
    except Exception:
        pass
    return sonuc


def bls_kaynak_adi() -> str:
    return "BLS API v2" if BLS_KEY else "BLS API (keysiz)"


# ═══════════════════════════════════════════════════════════════════
#  US TREASURY — günlük getiri eğrisi (KEYSİZ, sınırsız)
#  Piyasa BEKLENTİSİ buradan çıkar: 2Y < Fed Funds ⇒ indirim fiyatlanıyor.
# ═══════════════════════════════════════════════════════════════════
_HAZINE_URL = ("https://home.treasury.gov/resource-center/data-chart-center/"
               "interest-rates/daily-treasury-rates.csv/{yil}/all"
               "?type=daily_treasury_yield_curve&field_tdr_date_value={yil}&page&_format=csv")

# CSV başlıkları yıllara göre değişti ("4 Mo" 2022'de, "1.5 Month" 2024'te eklendi)
# → sabit indeks YOK, başlık ADIYLA eşleştiriyoruz.
_VADE_ESLEME = {"m1": ("1 mo",), "m3": ("3 mo",), "m6": ("6 mo",),
                "y1": ("1 yr",), "y2": ("2 yr",), "y5": ("5 yr",),
                "y10": ("10 yr",), "y30": ("30 yr",)}


def _hazine_csv_coz(metin: str) -> list[dict]:
    """Treasury CSV → artan sıralı [{tarih:'YYYY-MM-DD', m3,y2,y10,...}]."""
    try:
        rd = csv.DictReader(io.StringIO(metin))
        basliklar = [h for h in (rd.fieldnames or []) if h]
    except Exception:
        return []
    if not basliklar:
        return []
    kolon = {}
    for kod, adaylar in _VADE_ESLEME.items():
        for h in basliklar:
            hn = h.strip().lower().replace("month", "mo").replace("year", "yr")
            if hn in adaylar:
                kolon[kod] = h
                break
    tarih_kol = next((h for h in basliklar if h.strip().lower() == "date"), None)
    if not tarih_kol or "y10" not in kolon:
        return []
    out = []
    for satir in rd:
        ham = (satir.get(tarih_kol) or "").strip()
        t = None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
            try:
                t = datetime.strptime(ham, fmt).strftime("%Y-%m-%d")
                break
            except Exception:
                continue
        if not t:
            continue
        kayit = {"tarih": t}
        for kod, h in kolon.items():
            try:
                kayit[kod] = float(str(satir.get(h) or "").strip())
            except Exception:
                kayit[kod] = None
        if kayit.get("y10") is not None:
            out.append(kayit)
    out.sort(key=lambda x: x["tarih"])
    return out


async def hazine_egrisi(cl, taze_dk: float = 60.0) -> dict | None:
    """
    ABD Hazine günlük getiri eğrisi (keysiz).
    Döner: {"tarih","m3","y2","y10","y30","egri_10_2","seri_y10","seri_y2","kaynak"}
    """
    c = _oku("hazine_egri")
    if _taze_mi(c, taze_dk * 60) and c.get("veri"):
        return c["veri"]

    satirlar = []
    try:
        yil = datetime.now(timezone.utc).year
        for y in (yil, yil - 1):          # yıl başında güncel dosya boş olabilir
            r = await cl.get(_HAZINE_URL.format(yil=y),
                             headers={**HDR, "Accept": "text/csv"}, timeout=20)
            if r.status_code == 200:
                satirlar = _hazine_csv_coz(r.text) + satirlar
            if len(satirlar) >= 8:
                break
    except Exception:
        satirlar = []

    if not satirlar:
        return (c or {}).get("veri")      # son iyi veri (varsa)

    son = satirlar[-1]
    y10, y2 = son.get("y10"), son.get("y2")
    veri = {
        "tarih": son["tarih"],
        "m3": son.get("m3"), "y2": y2, "y10": y10, "y30": son.get("y30"),
        "egri_10_2": (round(y10 - y2, 2) if (y10 is not None and y2 is not None) else None),
        "seri_y10": [{"tarih": s["tarih"], "deger": s["y10"]}
                     for s in satirlar[-40:] if s.get("y10") is not None],
        "seri_y2": [{"tarih": s["tarih"], "deger": s["y2"]}
                    for s in satirlar[-40:] if s.get("y2") is not None],
        "kaynak": "US Treasury (keysiz)",
    }
    _yaz("hazine_egri", {"ts": time.time(), "veri": veri})
    return veri


# ═══════════════════════════════════════════════════════════════════
#  Yardımcılar — endeks serisinden yıllık/aylık % ve seviye farkı
# ═══════════════════════════════════════════════════════════════════
def yillik_yuzde(rows: list[dict]) -> float | None:
    """Son gözlemin 12 ay öncesine göre % değişimi (endeks serileri için)."""
    if not rows or len(rows) < 13:
        return None
    son, yil_once = rows[-1]["deger"], rows[-13]["deger"]
    if not yil_once:
        return None
    return round((son - yil_once) / yil_once * 100, 2)


def aylik_yuzde(rows: list[dict]) -> float | None:
    if not rows or len(rows) < 2 or not rows[-2]["deger"]:
        return None
    return round((rows[-1]["deger"] - rows[-2]["deger"]) / rows[-2]["deger"] * 100, 2)


def seviye_farki(rows: list[dict]) -> list[dict]:
    """Seviye serisini aylık FARK serisine çevirir (PAYEMS/CES → NFP değişimi)."""
    if not rows or len(rows) < 2:
        return []
    return [{"tarih": rows[i]["tarih"], "deger": round(rows[i]["deger"] - rows[i - 1]["deger"])}
            for i in range(1, len(rows))]


def durum() -> dict:
    """Teşhis: hangi keysiz kaynak elde, BLS bütçesi ne durumda."""
    b = _butce_durum()
    hz = _oku("hazine_egri") or {}
    return {
        "bls_anahtar": bool(BLS_KEY),
        "bls_kaynak": bls_kaynak_adi(),
        "bls_gunluk_kullanim": f"{b.get('sayac', 0)}/{V1_GUNLUK_LIMIT}" if not BLS_KEY else "sınırsız",
        "bls_kalan": bls_kalan_istek(),
        "bls_cache": {a: bool((_oku(f'bls_{a}') or {}).get("rows")) for a in BLS_SERI},
        "hazine_tarih": (hz.get("veri") or {}).get("tarih"),
        "fred_anahtar": bool(os.environ.get("FRED_API_KEY")),
    }


# ═══════════════════════════════════════════════════════════════════
#  CANLI KAYNAK TESTİ — "hangi kaynak gerçekten çalışıyor?"
# ═══════════════════════════════════════════════════════════════════
# Bu modüldeki uç noktalar geliştirme ortamında (dış ağ kapalı) DOĞRULANAMADI.
# Kod savunmacı yazıldı ve boş yanıtta 'veri yok' der — ama sessiz kalmasın diye
# her kaynağı TEK TEK canlı deneyip sonucu raporlayan bir teşhis var.
# Her kaynak: durum(ok|bos|hata) + kısa detay + örnek değer (doğrulanabilir olsun).
async def _ham_dene(cl, ad: str, url: str, coz, yontem: str = "GET",
                    govde=None, kabul: str = "application/json") -> dict:
    """
    Ham HTTP sondası. ÖNEMLİ: bls_getir/hazine_egrisi gibi sarmalayıcılar hatayı
    YUTUP boş liste döner → teşhis "ağ koptu"yu "kaynak boş yanıt verdi" sanır.
    Bu yüzden teşhis uçları SARMALAYICIYI KULLANMAZ, doğrudan istek atar:
    HTTP kodu ve istisna tipi olduğu gibi görünsün.
    """
    t0 = time.time()
    try:
        if yontem == "POST":
            r = await cl.post(url, json=govde,
                              headers={**HDR, "Content-Type": "application/json"}, timeout=20)
        else:
            r = await cl.get(url, headers={**HDR, "Accept": kabul}, timeout=20)
    except Exception as e:
        return {"ad": ad, "durum": "hata", "ms": int((time.time() - t0) * 1000),
                "detay": f"isteğe çıkılamadı — {type(e).__name__}: {str(e)[:90]}"}
    ms = int((time.time() - t0) * 1000)
    if r.status_code != 200:
        return {"ad": ad, "durum": "hata", "ms": ms, "http": r.status_code,
                "detay": f"HTTP {r.status_code} — uç nokta/seri anahtarı değişmiş olabilir"}
    try:
        rows = coz(r)
    except Exception as e:
        return {"ad": ad, "durum": "bos", "ms": ms, "http": 200,
                "detay": f"200 döndü ama yanıt çözülemedi — {type(e).__name__}: {str(e)[:70]}"}
    if not rows:
        return {"ad": ad, "durum": "bos", "ms": ms, "http": 200,
                "detay": "200 döndü ama gözlem yok — seri kimliği geçersiz ya da yayın durmuş"}
    son = rows[-1]
    return {"ad": ad, "durum": "ok", "ms": ms, "http": 200, "gozlem": len(rows),
            "ornek": {"tarih": son.get("tarih"), "deger": son.get("deger")},
            "detay": f"{len(rows)} gözlem, son: {son.get('tarih')} = {son.get('deger')}"}


async def kaynak_testi(cl) -> list[dict]:
    """BLS + Hazine + NY Fed + FRED uçlarını CANLI ve HAM olarak dener."""
    sonuc = []

    # 1) BLS — anahtarsız yolda günlük bütçeden 1 istek harcar
    if BLS_KEY:
        yil = datetime.now(timezone.utc).year
        sonuc.append(await _ham_dene(
            cl, "BLS API v2 — TÜFE serisi",
            "https://api.bls.gov/publicAPI/v2/timeseries/data/",
            lambda r: _bls_parse(((r.json().get("Results") or {}).get("series") or [{}])[0]),
            yontem="POST",
            govde={"seriesid": [BLS_SERI["cpi"][0]], "startyear": str(yil - 1),
                   "endyear": str(yil), "registrationkey": BLS_KEY}))
    elif bls_kalan_istek() > 0:
        _butce_harca(1)
        sonuc.append(await _ham_dene(
            cl, "BLS API v1 (anahtarsız) — TÜFE serisi",
            f"https://api.bls.gov/publicAPI/v1/timeseries/data/{BLS_SERI['cpi'][0]}",
            lambda r: _bls_parse(((r.json().get("Results") or {}).get("series") or [{}])[0])))
    else:
        sonuc.append({"ad": "BLS", "durum": "bos",
                      "detay": "günlük anahtarsız istek hakkı bitti — BLS_API_KEY "
                               "eklenirse sınır 500/gün olur"})

    # 2) US Treasury günlük getiri eğrisi
    yil = datetime.now(timezone.utc).year
    sonuc.append(await _ham_dene(
        cl, "US Treasury — günlük getiri eğrisi", _HAZINE_URL.format(yil=yil),
        lambda r: [{"tarih": s["tarih"], "deger": s.get("y10")}
                   for s in _hazine_csv_coz(r.text)],
        kabul="text/csv"))

    # 3) NY Fed efektif gecelik faiz (politika faizi yedeği)
    def _effr_coz(r):
        d = r.json()
        return [{"tarih": str(o.get("effectiveDate") or o.get("date"))[:10],
                 "deger": o.get("percentRate", o.get("rate"))}
                for o in (d.get("refRates") or [])
                if (o.get("percentRate", o.get("rate")) is not None)][::-1]
    sonuc.append(await _ham_dene(
        cl, "NY Fed — efektif gecelik faiz",
        "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json", _effr_coz))

    # 4) FRED (anahtar varsa)
    fk = os.environ.get("FRED_API_KEY")
    if fk:
        def _fred_coz(r):
            d = r.json()
            return [{"tarih": o["date"], "deger": float(o["value"])}
                    for o in (d.get("observations") or []) if o.get("value") != "."][::-1]
        sonuc.append(await _ham_dene(
            cl, "FRED — işsizlik serisi",
            "https://api.stlouisfed.org/fred/series/observations"
            f"?series_id=UNRATE&api_key={fk}&file_type=json&sort_order=desc&limit=3",
            _fred_coz))
    else:
        sonuc.append({"ad": "FRED", "durum": "bos",
                      "detay": "FRED_API_KEY tanımsız — GSYİH/PCE/perakende/sanayi/güven/"
                               "haftalık başvuru ve Japonya TÜFE'si yalnız burada var"})
    return sonuc
