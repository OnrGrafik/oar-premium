"""
oar_sinyaller.py — Modüler Sinyal/Teyit Blok Kütüphanesi (keşif motoru için)
═══════════════════════════════════════════════════════════════════════════════
Her "blok" = bana öğrettiğin bir yöntem; deterministik bir fonksiyon. Bir aday
sinyalin feature sözlüğünü alır, TEYİT geçti mi diye True/False döner.
  - True  → bu teyit sağlanıyor
  - False → sağlanmıyor (sinyal elenir)
  - None  → bu yöntemin VERİSİ henüz yok (uygulanamaz; keşifte kullanılmaz)

Strateji = bu blokların KOMBİNASYONU. Keşif motoru (oar_kesif) hangi kombinasyonun
OOS'ta + maliyet sonrası en iyi sonucu verdiğini bulur. Sen yeni yöntem öğrettikçe
buraya yeni blok eklenir ve AKTIF_BLOKLAR'a alınır.

feature sözlüğü (aday sinyal) örnek alanlar:
  yon (LONG/SHORT), fib (oran), fiyat, cvd_delta, poc, asia_h, asia_l,
  outcome, pct, ts ...  + gelecekte: htf_vwap_ok, fp_absorp, dvol_ok, makro_ok
"""


# ─── AKTİF BLOKLAR (verisi olan — şimdi kullanılabilir) ──────────────────────
def cvd_yon(s) -> bool:
    """CVD yönü işlemle uyumlu mu (SHORT→negatif, LONG→pozitif delta)."""
    d = s.get("cvd_delta", 0) or 0
    return d < 0 if s.get("yon") == "SHORT" else d > 0


def cvd_guclu(s) -> bool:
    """CVD büyüklüğü eşiğin üstünde mi (gürültü değil, anlamlı akış)."""
    return abs(s.get("cvd_delta", 0) or 0) >= (s.get("cvd_esik", 0) or 0)


def poc_taraf(s) -> bool:
    """Fiyat POC'un doğru tarafında mı (SHORT→üstü/direnç, LONG→altı/destek)."""
    poc, f = s.get("poc"), s.get("fiyat")
    if not poc or not f:
        return True
    return f >= poc if s.get("yon") == "SHORT" else f <= poc


def fib_ekstrem(s) -> bool:
    """Giriş ekstrem fib'de mi (≥1.0 üst sweep / ≤0.0 alt sweep)."""
    o = s.get("fib", 0.5)
    return o >= 1.0 or o <= 0.0


# ─── GELECEK BLOKLAR (veri eklenince doldurulacak — şimdilik None) ────────────
# Her biri ilgili veri/feature geldiğinde gerçek mantıkla doldurulacak ve
# AKTIF_BLOKLAR'a eklenecek. None döndükçe keşif motoru bunları KULLANMAZ.
def htf_vwap(s):
    """HTF (haftalık/aylık/çeyreklik) VWAP yakınlığı/teyidi."""
    return s.get("htf_vwap_ok")


def htf_vpfr(s):
    """HTF VPFR yoğun hacim seviyesi çakışması."""
    return s.get("htf_vpfr_ok")


def footprint_absorpsiyon(s):
    """Footprint absorpsiyon/trapped/balina teyidi."""
    return s.get("fp_absorp")


def dvol_rejim(s):
    """Opsiyon ana-yön rejimi (DVOL percentile yüksekse opsiyon belirleyici)."""
    return s.get("dvol_ok")


def makro_korelasyon(s):
    """Makro (DXY/10Y/20Y/VIX/CPI + SP500/Nasdaq VPFR) uyumu/riski."""
    return s.get("makro_ok")


BLOKLAR = {
    "cvd_yon": cvd_yon,
    "cvd_guclu": cvd_guclu,
    "poc_taraf": poc_taraf,
    "fib_ekstrem": fib_ekstrem,
    "htf_vwap": htf_vwap,
    "htf_vpfr": htf_vpfr,
    "footprint_absorpsiyon": footprint_absorpsiyon,
    "dvol_rejim": dvol_rejim,
    "makro_korelasyon": makro_korelasyon,
}

# Şu an VERİSİ olan, keşifte kullanılabilecek bloklar (öğrendikçe genişler).
AKTIF_BLOKLAR = ["cvd_yon", "cvd_guclu", "poc_taraf", "fib_ekstrem"]


def blok_uygula(sinyal: dict, blok_adi: str):
    """Tek blok sonucu: True/False/None (None=veri yok/uygulanamaz)."""
    f = BLOKLAR.get(blok_adi)
    if f is None:
        return None
    try:
        return f(sinyal)
    except Exception:
        return None
