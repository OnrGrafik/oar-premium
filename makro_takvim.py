"""
Makro Takvim — OAR Premium
══════════════════════════════════════════════════════════════════════════════
Research Agent — Bilimsel Bulgular ÖNERİ'sinin kod karşılığı:

  "OAR'a, makro verilerin (enflasyon, faiz kararları) açıklanacağı gün ve
   saatlerde sistemin risk iştahını otomatik olarak düşüren veya belirli
   hipotezleri askıya alan bir modül eklenmesi önerilir. Ayrıca 'move_source'
   bilgisinin belirlenmesi, rejim analizinin derinleşmesi için kritiktir."

Bu modül:
  1) Yüksek etkili makro açıklamaların (CPI, FOMC, NFP, PCE, PPI) pencere
     saatlerini bilir; pencere içindeyse risk_carpani < 1 döndürür
     (risk_skoru bunu kullanarak mutlak skoru/iştahı kısar).
  2) askiya_alinan_hipotezler() → bu pencerede devre dışı bırakılacak
     hipotez etiketlerini verir.
  3) move_source_belirle() → son fiyat hareketinin kaynağını (makro / opsiyon
     / likidite / teknik) sınıflandırır; rejim analizini derinleştirir.

Saatler ABD Doğu Saati (ET) referanslı yaygın açıklama saatleridir:
  • CPI / PPI / NFP / Retail / PCE : 08:30 ET
  • FOMC faiz kararı + basın       : 14:00–15:00 ET
Pencere = açıklamadan ÖNCE 60 dk + SONRA 90 dk (volatilite kuyruğu).
"""

from __future__ import annotations
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Haftanın günü bağımsız, "tipik" yüksek etkili açıklama saatleri (ET).
# Gerçek tarih API'si yoksa bile gün-içi pencereyi yakalamak için saat bazlı.
MAKRO_PENCERELER = [
    # (etiket, saat ET, dakika, önce_dk, sonra_dk, risk_carpani, etki)
    ("CPI/PPI/NFP/PCE (08:30 ET veri bloğu)", 8, 30, 60, 90, 0.4, "YÜKSEK"),
    ("FOMC faiz kararı (14:00 ET)",          14,  0, 45, 60, 0.3, "ÇOK YÜKSEK"),
    ("FOMC basın toplantısı (14:30 ET)",     14, 30, 15, 75, 0.3, "ÇOK YÜKSEK"),
]

# Bu pencerelerde askıya alınacak hipotez/strateji etiketleri.
PENCEREDE_ASKIYA = ["ters", "mean_reversion", "gridbot", "range_fade", "countertrend"]


def _et_now() -> datetime:
    return datetime.now(ET)


def aktif_olay_penceresi(now: datetime | None = None) -> dict | None:
    """
    Şu an yüksek etkili bir makro açıklama penceresinde miyiz?
    Döner: {etiket, aciklama, risk_carpani, etki, dakika_kala} | None
    Hafta sonu pencere yok (ABD verisi açıklanmaz).
    """
    n = (now or _et_now()).astimezone(ET)
    if n.weekday() >= 5:   # Cmt/Pazar
        return None
    for etiket, sa, dk, once, sonra, carpan, etki in MAKRO_PENCERELER:
        hedef = n.replace(hour=sa, minute=dk, second=0, microsecond=0)
        bas = hedef - timedelta(minutes=once)
        bit = hedef + timedelta(minutes=sonra)
        if bas <= n <= bit:
            kala = int((hedef - n).total_seconds() // 60)
            durum = (f"{abs(kala)} dk {'sonra' if kala < 0 else 'kala'}")
            return {
                "etiket":       etiket,
                "aciklama":     f"{etiket} — {durum}",
                "risk_carpani": carpan,
                "etki":         etki,
                "dakika_kala":  kala,
            }
    return None


def askiya_alinan_hipotezler(now: datetime | None = None) -> list[str]:
    """Aktif makro penceresinde askıya alınacak hipotez etiketleri."""
    if aktif_olay_penceresi(now):
        return list(PENCEREDE_ASKIYA)
    return []


def hipotez_askida_mi(hipotez_etiketi: str, now: datetime | None = None) -> bool:
    """Verilen hipotez/strateji etiketi şu an askıda mı?"""
    if not hipotez_etiketi:
        return False
    et = hipotez_etiketi.lower()
    return any(a in et for a in askiya_alinan_hipotezler(now))


# ── move_source: hareketin kaynağını sınıflandır ──────────────────────────────
def move_source_belirle(*, fiyat_chg_pct: float = 0.0,
                        oi_chg_pct: float = 0.0,
                        funding: float = 0.0,
                        gamma_rejim: str = "",
                        cvd_yon: str = "",
                        makro_penceresi: bool | None = None) -> dict:
    """
    Son fiyat hareketinin baskın kaynağını sınıflandırır.

    Döner: {"source": MACRO|OPTIONS|LIQUIDATION|SPOT_FLOW|TECHNICAL,
            "guven": 0-100, "aciklama": str}
    """
    if makro_penceresi is None:
        makro_penceresi = aktif_olay_penceresi() is not None

    adaylar = []  # (source, guven, aciklama)

    if makro_penceresi and abs(fiyat_chg_pct) >= 0.4:
        adaylar.append(("MACRO", 80,
                        "Hareket makro açıklama penceresinde — kaynak makro."))

    if "NEGATİF" in (gamma_rejim or "") and abs(fiyat_chg_pct) >= 0.6:
        adaylar.append(("OPTIONS", 70,
                        "Negatif gamma + sert hareket — dealer hedging baskısı."))

    # OI düşerken büyük fiyat hareketi → likidasyon (pozisyon kapanması)
    if oi_chg_pct <= -1.5 and abs(fiyat_chg_pct) >= 0.8:
        adaylar.append(("LIQUIDATION", 65,
                        "OI düşüşü + sert hareket — zincirleme likidasyon."))

    # OI artarken yön + CVD aynı → yeni spot/futures akışı
    if oi_chg_pct >= 1.5 and cvd_yon and abs(fiyat_chg_pct) >= 0.5:
        adaylar.append(("SPOT_FLOW", 60,
                        f"OI artışı + CVD {cvd_yon} — yeni yönlü akış."))

    if abs(funding) >= 0.05:
        adaylar.append(("LIQUIDATION", 50,
                        f"Aşırı funding (%{funding:.3f}) — squeeze riski."))

    if not adaylar:
        return {"source": "TECHNICAL", "guven": 40,
                "aciklama": "Belirgin makro/opsiyon/akış tetiği yok — teknik hareket."}

    adaylar.sort(key=lambda x: x[1], reverse=True)
    src, guven, acik = adaylar[0]
    return {"source": src, "guven": guven, "aciklama": acik,
            "alternatifler": [a[0] for a in adaylar[1:]]}


# ═══════════════════════════════════════════════════════════════════════════════
#  AÇIKLAMA TAKVİMİ — "bugün hangi ABD verisi açıklandı / sırada ne var"
# ═══════════════════════════════════════════════════════════════════════════════
# Kullanıcı sitemi: "Bugün ABD verisi açıklandı ama makro veriler kısmında yok."
# Sitede takvim HİÇ yoktu; yalnız gün-içi risk penceresi vardı (saat bazlı).
#
# DÜRÜSTLÜK KURALI (ANAYASA #3): ücretsiz + anahtarsız bir "ekonomik takvim API'si"
# YOK. O yüzden tarihleri TÜRETİYORUZ ve her olayı `kesin` bayrağıyla etiketliyoruz:
#   kesin=True  → takvim kuralı KESİN (NFP = ayın ilk Cuma'sı, başvurular = her
#                 Perşembe, ISM = ayın 1./3. iş günü). Sapma nadir (tatil kayması).
#   kesin=False → TİPİK PENCERE (CPI ~10-15, perakende ~15-17, PCE/GSYİH ay sonu).
#                 Sitede "tahmini" diye gösterilir — kesinmiş gibi sunulmaz.
# FOMC gibi sabit tarihli olaylar tahmin EDİLMEZ; repo kökündeki
# `makro_takvim_override.json` dosyasından okunur (kullanıcı elle girer, git-senkron):
#   {"2026-09-16": [{"kod":"FOMC","ad":"FOMC faiz kararı","saat":"14:00","etki":"ÇOK YÜKSEK"}]}

from datetime import date as _date

_OVERRIDE_AD = "makro_takvim_override.json"

# ── BÖLGELER ─────────────────────────────────────────────────────────────────
# Her bölgenin açıklama saati KENDİ yerel saatinde sabittir (ABD 08:30 ET,
# Euro Bölgesi 11:00 CET, Japonya 08:30 JST) → UTC karşılığı yaz saatiyle kayar.
# Bu yüzden saatler yerel tutulup UTC'ye ÇEVRİLİYOR, sabit UTC yazılmıyor.
BOLGELER = {
    "ABD":      {"ad": "ABD",            "bayrak": "🇺🇸", "tz": "America/New_York",
                 "para": "USD", "kanal": "dolar"},
    "AB":       {"ad": "Euro Bölgesi",   "bayrak": "🇪🇺", "tz": "Europe/Berlin",
                 "para": "EUR", "kanal": "dolar_ters"},
    "JAPONYA":  {"ad": "Japonya",        "bayrak": "🇯🇵", "tz": "Asia/Tokyo",
                 "para": "JPY", "kanal": "carry"},
}

ETKI_SIRA = {"ÇOK YÜKSEK": 3, "YÜKSEK": 2, "ORTA": 1, "DÜŞÜK": 0}

# kural: tarih türetme kuralı · aylar: None=her ay, (1,4,7,10)=yalnız o aylarda
# gostergeler: bu açıklamanın güncellediği makro gösterge anahtarları
RELEASE_KURALLARI = [
    # ── ABD ───────────────────────────────────────────────────────────────────
    {"bolge": "ABD", "kod": "NFP", "ad": "Tarım Dışı İstihdam (NFP) + İşsizlik",
     "kural": "ilk_cuma", "sa": 8, "dk": 30, "etki": "ÇOK YÜKSEK", "kesin": True,
     "gostergeler": ["nfp", "isRate", "kazanc"]},
    {"bolge": "ABD", "kod": "CLAIMS", "ad": "Haftalık İşsizlik Başvuruları",
     "kural": "her_persembe", "sa": 8, "dk": 30, "etki": "ORTA", "kesin": True,
     "gostergeler": ["claims"]},
    {"bolge": "ABD", "kod": "ISM_IMALAT", "ad": "ISM İmalat PMI",
     "kural": "is_gunu_1", "sa": 10, "dk": 0, "etki": "YÜKSEK", "kesin": True,
     "gostergeler": ["ism"]},
    {"bolge": "ABD", "kod": "ISM_HIZMET", "ad": "ISM Hizmet PMI",
     "kural": "is_gunu_3", "sa": 10, "dk": 0, "etki": "YÜKSEK", "kesin": True,
     "gostergeler": []},
    {"bolge": "ABD", "kod": "CPI", "ad": "TÜFE (CPI) enflasyon",
     "kural": "ay_10_15", "sa": 8, "dk": 30, "etki": "ÇOK YÜKSEK", "kesin": False,
     "gostergeler": ["cpi"]},
    {"bolge": "ABD", "kod": "PPI", "ad": "ÜFE (PPI) üretici enflasyonu",
     "kural": "ay_11_16", "sa": 8, "dk": 30, "etki": "YÜKSEK", "kesin": False,
     "gostergeler": ["ppi"]},
    {"bolge": "ABD", "kod": "PERAKENDE", "ad": "Perakende Satışlar",
     "kural": "ay_15_17", "sa": 8, "dk": 30, "etki": "YÜKSEK", "kesin": False,
     "gostergeler": ["perakende"]},
    {"bolge": "ABD", "kod": "GDP", "ad": "GSYİH (öncü/revize)",
     "kural": "ay_son_persembe", "sa": 8, "dk": 30, "etki": "YÜKSEK", "kesin": False,
     "gostergeler": ["gsyih"]},
    {"bolge": "ABD", "kod": "PCE", "ad": "PCE — Fed'in tercih ettiği enflasyon",
     "kural": "ay_son_is_gunu", "sa": 8, "dk": 30, "etki": "ÇOK YÜKSEK", "kesin": False,
     "gostergeler": ["pce"]},
    {"bolge": "ABD", "kod": "SANAYI", "ad": "Sanayi Üretimi",
     "kural": "ay_15_17", "sa": 9, "dk": 15, "etki": "ORTA", "kesin": False,
     "gostergeler": ["sanayi"]},
    {"bolge": "ABD", "kod": "GUVEN", "ad": "Michigan Tüketici Güveni (öncü)",
     "kural": "ikinci_cuma", "sa": 10, "dk": 0, "etki": "ORTA", "kesin": False,
     "gostergeler": ["guven"]},

    # ── EURO BÖLGESİ ──────────────────────────────────────────────────────────
    # BTC'ye kanal: EUR zayıflarsa dolar endeksi GÜÇLENİR → BTC baskı (ters kanal).
    {"bolge": "AB", "kod": "HICP_FLASH", "ad": "Öncü Enflasyon (HICP flash)",
     "kural": "ay_son_is_gunu", "sa": 11, "dk": 0, "etki": "ÇOK YÜKSEK", "kesin": False,
     "gostergeler": ["ab_hicp"]},
    {"bolge": "AB", "kod": "HICP_FINAL", "ad": "Nihai Enflasyon (HICP) + çekirdek",
     "kural": "ay_17_19", "sa": 11, "dk": 0, "etki": "YÜKSEK", "kesin": False,
     "gostergeler": ["ab_hicp", "ab_cekirdek"]},
    {"bolge": "AB", "kod": "PMI_FLASH", "ad": "Öncü PMI (imalat + hizmet)",
     "kural": "ay_22_24", "sa": 10, "dk": 0, "etki": "YÜKSEK", "kesin": False,
     "gostergeler": []},
    {"bolge": "AB", "kod": "AB_GDP", "ad": "GSYİH (öncü tahmin)",
     "kural": "ay_son_is_gunu", "sa": 11, "dk": 0, "etki": "YÜKSEK", "kesin": False,
     "aylar": (1, 4, 7, 10), "gostergeler": ["ab_gsyih"]},
    {"bolge": "AB", "kod": "AB_ISSIZLIK", "ad": "İşsizlik Oranı",
     "kural": "ay_1_3", "sa": 11, "dk": 0, "etki": "ORTA", "kesin": False,
     "gostergeler": ["ab_issizlik"]},
    {"bolge": "AB", "kod": "ZEW", "ad": "ZEW Yatırımcı Beklentisi",
     "kural": "ikinci_sali", "sa": 11, "dk": 0, "etki": "ORTA", "kesin": False,
     "gostergeler": []},
    {"bolge": "AB", "kod": "IFO", "ad": "Ifo İş İklimi (Almanya)",
     "kural": "ay_24_26", "sa": 10, "dk": 0, "etki": "ORTA", "kesin": False,
     "gostergeler": []},

    # ── JAPONYA ───────────────────────────────────────────────────────────────
    # BTC'ye kanal: yen GÜÇLENİRSE carry unwind → küresel risk varlıklarında satış.
    {"bolge": "JAPONYA", "kod": "JP_CPI", "ad": "Ulusal TÜFE",
     "kural": "ay_18_20", "sa": 8, "dk": 30, "etki": "YÜKSEK", "kesin": False,
     "gostergeler": ["jp_tufe"]},
    {"bolge": "JAPONYA", "kod": "JP_TOKYO_CPI", "ad": "Tokyo TÜFE (ulusal verinin öncüsü)",
     "kural": "son_cuma", "sa": 8, "dk": 30, "etki": "YÜKSEK", "kesin": True,
     "gostergeler": []},
    {"bolge": "JAPONYA", "kod": "JP_GDP", "ad": "GSYİH (öncü)",
     "kural": "ay_14_17", "sa": 8, "dk": 50, "etki": "YÜKSEK", "kesin": False,
     "aylar": (2, 5, 8, 11), "gostergeler": ["jp_gsyih"]},
    {"bolge": "JAPONYA", "kod": "TANKAN", "ad": "Tankan İmalat Endeksi",
     "kural": "is_gunu_1", "sa": 8, "dk": 50, "etki": "YÜKSEK", "kesin": False,
     "aylar": (4, 7, 10), "gostergeler": []},
    {"bolge": "JAPONYA", "kod": "JP_UCRET", "ad": "Ortalama Nakit Kazanç (ücret)",
     "kural": "ay_5_9", "sa": 8, "dk": 30, "etki": "ORTA", "kesin": False,
     "gostergeler": []},
]


def _ay_gunleri(yil: int, ay: int) -> list[_date]:
    from calendar import monthrange
    return [_date(yil, ay, g) for g in range(1, monthrange(yil, ay)[1] + 1)]


def _is_gunleri(yil: int, ay: int) -> list[_date]:
    return [d for d in _ay_gunleri(yil, ay) if d.weekday() < 5]


# "ay_A_B" biçimli kurallar tipik açıklama PENCERESİdir (kesin=False ile etiketlenir)
_PENCERE_KURALLARI = {
    "ay_1_3": (1, 3), "ay_5_9": (5, 9), "ay_10_15": (10, 15), "ay_11_16": (11, 16),
    "ay_14_17": (14, 17), "ay_15_17": (15, 17), "ay_17_19": (17, 19),
    "ay_18_20": (18, 20), "ay_22_24": (22, 24), "ay_24_26": (24, 26),
}


def _kural_tarihleri(kural: str, yil: int, ay: int) -> list[_date]:
    """Bir kuralın o aydaki tarih(ler)i. Bilinmeyen kural → boş."""
    gunler = _ay_gunleri(yil, ay)
    isg = _is_gunleri(yil, ay)
    cumalar = [d for d in gunler if d.weekday() == 4]
    persembeler = [d for d in gunler if d.weekday() == 3]
    saliler = [d for d in gunler if d.weekday() == 1]
    if kural == "ilk_cuma":
        return cumalar[:1]
    if kural == "ikinci_cuma":
        return cumalar[1:2]
    if kural == "son_cuma":
        return cumalar[-1:]
    if kural == "ikinci_sali":
        return saliler[1:2]
    if kural == "her_persembe":
        return persembeler
    if kural == "is_gunu_1":
        return isg[:1]
    if kural == "is_gunu_3":
        return isg[2:3]
    if kural == "ay_son_is_gunu":
        return isg[-1:]
    if kural == "ay_son_persembe":
        return persembeler[-1:]
    if kural in _PENCERE_KURALLARI:
        bas, bit = _PENCERE_KURALLARI[kural]
        aday = [d for d in gunler if bas <= d.day <= bit and d.weekday() < 5]
        # pencerenin ortasındaki iş gününü temsilci seç (tipik açıklama günü)
        return aday[len(aday) // 2:len(aday) // 2 + 1] if aday else []
    return []


def _override_oku() -> dict:
    """Kullanıcının elle girdiği kesin tarihler (FOMC vb.). Yoksa boş."""
    import json
    from pathlib import Path
    for kok in (Path(__file__).resolve().parent, Path.cwd()):
        p = kok / _OVERRIDE_AD
        try:
            if p.exists():
                d = json.loads(p.read_text(encoding="utf-8"))
                return d if isinstance(d, dict) else {}
        except Exception:
            continue
    return {}


def _olay(tarih: _date, k: dict, bolge: str = "ABD") -> dict:
    b = BOLGELER.get(bolge) or BOLGELER["ABD"]
    tz = ZoneInfo(b["tz"])
    sa, dk = int(k.get("sa", 8)), int(k.get("dk", 30))
    yerel = datetime(tarih.year, tarih.month, tarih.day, sa, dk, tzinfo=tz)
    utc = yerel.astimezone(ZoneInfo("UTC"))
    simdi = datetime.now(ZoneInfo("UTC"))
    fark_dk = (utc - simdi).total_seconds() / 60.0
    etki = k.get("etki", "YÜKSEK")
    return {
        "tarih": tarih.isoformat(),          # BÖLGENİN YEREL tarihi
        # ⚠ Japonya 08:30 JST = bir ÖNCEKİ günün 23:30 UTC'si → yerel tarih + UTC saati
        # yan yana yazılırsa yanıltır. Kronolojik liste `tarih_utc` + `saat_utc` kullanır.
        "tarih_utc": utc.date().isoformat(),
        "bolge": bolge, "bolge_ad": b["ad"], "bayrak": b["bayrak"], "kanal": b["kanal"],
        "kod": k.get("kod", "OLAY"), "ad": k.get("ad", "Makro olay"),
        "saat_yerel": f"{sa:02d}:{dk:02d}",
        "saat_et": f"{sa:02d}:{dk:02d}" if bolge == "ABD" else None,
        "saat_utc": utc.strftime("%H:%M"),
        "utc": utc.isoformat(),
        "etki": etki, "etki_sira": ETKI_SIRA.get(etki, 1),
        "kesin": bool(k.get("kesin", False)),
        "gostergeler": list(k.get("gostergeler") or []),
        "beklenti_girdi": k.get("beklenti"),        # override'dan gelen konsensüs
        "onceki_girdi": k.get("onceki"),
        "gecti": fark_dk <= 0,
        "dakika": int(fark_dk),
        "bugun": tarih == simdi.astimezone(tz).date(),
    }


def takvim(once_gun: int = 5, sonra_gun: int = 14,
           bolgeler: list[str] | None = None, min_etki: str | None = None) -> list[dict]:
    """
    [bugün−once_gun, bugün+sonra_gun] aralığındaki makro açıklamalar.
    Kural-türetimli (kesin=True) + tipik pencere (kesin=False) + override (kesin).

    bolgeler: None → yalnız ABD (geriye dönük uyum: mevcut ABD çağrıları bozulmasın).
              ["ABD","AB","JAPONYA"] → küresel takvim.
    min_etki: "YÜKSEK" verilirse ORTA/DÜŞÜK olaylar elenir.
    """
    hedef = list(bolgeler or ["ABD"])
    esik = ETKI_SIRA.get(min_etki or "", -1)
    bugun = datetime.now(ET).date()
    bas, bit = bugun - timedelta(days=once_gun), bugun + timedelta(days=sonra_gun)
    aylar = {(bas.year, bas.month), (bugun.year, bugun.month), (bit.year, bit.month)}
    out = []
    for yil, ay in sorted(aylar):
        for k in RELEASE_KURALLARI:
            bolge = k.get("bolge", "ABD")
            if bolge not in hedef:
                continue
            if k.get("aylar") and ay not in k["aylar"]:
                continue          # çeyreklik olaylar (GSYİH, Tankan) yalnız kendi ayında
            for t in _kural_tarihleri(k["kural"], yil, ay):
                if bas <= t <= bit:
                    out.append(_olay(t, k, bolge))
    for tarih_str, olaylar in (_override_oku() or {}).items():
        try:
            t = datetime.strptime(str(tarih_str)[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if not (bas <= t <= bit):
            continue
        for o in (olaylar if isinstance(olaylar, list) else [olaylar]):
            if not isinstance(o, dict):
                continue
            bolge = o.get("bolge", "ABD")
            if bolge not in hedef:
                continue
            try:
                sa, dk = (int(x) for x in str(o.get("saat", "08:30")).split(":")[:2])
            except Exception:
                sa, dk = 8, 30
            out.append(_olay(t, {**o, "sa": sa, "dk": dk, "kesin": True}, bolge))
    if esik >= 0:
        out = [o for o in out if o["etki_sira"] >= esik]
    out.sort(key=lambda x: x["utc"])   # gerçek kronoloji (bölge saatleri karışmasın)
    return out


def bugunku_aciklamalar(bolgeler: list[str] | None = None) -> list[dict]:
    """Bugün açıklanan / açıklanacak veriler (varsayılan: ABD)."""
    return [o for o in takvim(0, 1, bolgeler) if o["bugun"]]


def sonraki_olaylar(n: int = 4, yalniz_yuksek: bool = False,
                    bolgeler: list[str] | None = None) -> list[dict]:
    """Sıradaki n açıklama (henüz geçmemiş)."""
    ileri = [o for o in takvim(0, 21, bolgeler) if not o["gecti"]]
    if yalniz_yuksek:
        ileri = [o for o in ileri if o["etki"] in ("YÜKSEK", "ÇOK YÜKSEK")]
    return ileri[:n]


def gosterge_olaylari(anahtar: str, once_gun: int = 10,
                      bolgeler: list[str] | None = None) -> list[dict]:
    """Bir göstergeyi (ör. 'nfp') etkileyen son açıklamalar."""
    return [o for o in takvim(once_gun, 0, bolgeler)
            if anahtar in (o.get("gostergeler") or []) and o["gecti"]]


if __name__ == "__main__":
    import json
    print("Bugün:", json.dumps(bugunku_aciklamalar(), ensure_ascii=False, indent=1))
    print("Sıradaki:", json.dumps(sonraki_olaylar(3), ensure_ascii=False, indent=1))
    print("Aktif pencere:", json.dumps(aktif_olay_penceresi(), ensure_ascii=False))
    print("Askıdaki hipotezler:", askiya_alinan_hipotezler())
    print("move_source:", json.dumps(
        move_source_belirle(fiyat_chg_pct=1.2, oi_chg_pct=-2.0,
                            gamma_rejim="NEGATİF GAMMA"), ensure_ascii=False))
