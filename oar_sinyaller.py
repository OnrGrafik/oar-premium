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


# ─── OI / WHALE-RETAIL BLOKLARI (futures metrics verisi gelince aktif) ───────
def oi_yuksek(s):
    """Open Interest yüksek mi (günlük z ≥ 1) — güçlü ilgi/pozisyonlanma."""
    return s.get("oi_yuksek")


def whale_retail_zit(s):
    """Whale ve retail zıt tarafta mı (SHORT: whale short+retail long; LONG tersi)."""
    return s.get("whale_retail_zit")


def range_rejimi(s):
    """
    Rejim filtresi: SADECE range gününde fade al (trend gününde alma).
    OAR fade mean-reversion'dır → range'de kazanır, trend'de SL yer. Efficiency
    Ratio < 0.40 = range (True). Feature yoksa None (keşifte atlanır).
    """
    return s.get("range_rejimi")


def oi_tuzak(s):
    """
    OI tuzağı: süpürme anında OI yüksek (pozisyon birikti) + reclaim (fiyat döndü)
    → tuzağa düşen pozisyonlar (MM trap), fade'i güçlendirir. Mevcut feature'lardan
    türer (oi_yuksek + reclaim) — yeniden işleme gerekmez. OI verisi yoksa None.
    """
    oi = s.get("oi_yuksek")
    rc = s.get("reclaim")
    if oi is None or rc is None:
        return None
    return bool(oi and rc)


# ─── MOD / TREND BLOKLARI (dual-mode: range→fade, trend→breakout) ─────────────
def trend_rejimi(s):
    """
    Rejim filtresi: SADECE trend gününde breakout al. range_rejimi'nin tersi;
    Efficiency Ratio ≥ 0.40 = trend (True). trend/breakout adayları için anlamlı.
    Feature yoksa None (keşifte atlanır).
    """
    return s.get("trend_rejimi")


def mod_fade(s):
    """Aday fade modunda mı (mean-reversion: ekstremi ters yönde fade et)."""
    m = s.get("mod")
    return m == "fade" if m else None


def mod_trend(s):
    """Aday trend/breakout modunda mı (ekstrem kırılımı yönünde işlem)."""
    m = s.get("mod")
    return m == "trend" if m else None


def gun_bias_uyum(s):
    """Dünden gelen trend yönü bugünkü işlemle uyumlu mu (bias devamı)."""
    return s.get("gun_bias_uyum")


def breakout_teyit(s):
    """
    Teyitli kırılım: CVD kırılım yönünde + absorpsiyon (kırılan tarafın pasif
    emirleri tükendi). Naif breakout yerine, kullanıcının anlattığı 'Asia high
    güçlü gelme + pasif satıcı tüketimi + CVD devam' yığınını kodlar. Feature yoksa None.
    """
    return s.get("breakout_teyit")


# ─── GELECEK BLOKLAR (veri eklenince doldurulacak — şimdilik None) ────────────
# Her biri ilgili veri/feature geldiğinde gerçek mantıkla doldurulacak ve
# AKTIF_BLOKLAR'a eklenecek. None döndükçe keşif motoru bunları KULLANMAZ.
def htf_vwap(s):
    """HTF (haftalık/aylık/çeyreklik) anchored VWAP yakınlığı (≤%0.5 confluence)."""
    return s.get("htf_vwap_yakin")


def htf_vpfr(s):
    """HTF VPFR yoğun hacim seviyesi çakışması."""
    return s.get("htf_vpfr_ok")


def footprint_absorpsiyon(s):
    """Yüksek hacim ama fiyat ilerlemiyor → absorpsiyon (aday gen'de hesaplanır)."""
    return s.get("absorp")


def footprint_balina(s):
    """Giriş dakikasında balina deltası (günlük |delta| 80. persentil üstü)."""
    return s.get("balina")


def footprint_yuksek_hacim(s):
    """Giriş yüksek hacimli dakikada mı (vol z ≥ 1)."""
    return s.get("vol_yuksek")


def footprint_trapped(s):
    """Sweep sonrası geri dönüş (tuzağa düşenler) — fade'i destekler."""
    return s.get("reclaim")


def footprint_kalicilik(s):
    """
    Büyük delta seviyesi kalıcılığı: girişe kadar oluşan en büyük |delta| barı
    bir S/R gibi davranıp fiyatı fade-uyumlu tarafta tutmuş mu (no-lookahead).
    Yeni feature — aday_sinyaller_uret hesaplar; eski cache'te yoksa None.
    """
    return s.get("kalicilik")


def dvol_rejim(s):
    """Opsiyon ana-yön rejimi (DVOL percentile yüksekse opsiyon belirleyici)."""
    return s.get("dvol_ok")


def dvol_skew_bearish(s):
    """
    DVOL percentile yüksek + DVOL yükseliyor + skew (25Δ RR) yükseliyor → düşüş
    sinyali (SHORT teyidi / LONG blok). Canlı/forward-log verisi yoksa None.
    """
    return s.get("dvol_skew_bearish")


def vol_sinyali(s):
    """
    Spot-vol korelasyonu düşüyor + IV vade yapısı slope'u yükseliyor → volatilite
    genişlemesi (breakout/geniş hareket beklentisi). Veri yoksa None.
    """
    return s.get("vol_sinyali")


def makro_korelasyon(s):
    """Makro (DXY/10Y/20Y/VIX/CPI + SP500/Nasdaq VPFR) uyumu/riski."""
    return s.get("makro_ok")


# ─── YENİ BLOKLAR (kullanıcı isteği — genişletilmiş uzay, no-lookahead) ──────
# aday_sinyaller_uret bu alanları hesaplar (klines+aggTrades'ten, geçmişe bakmadan).
def gun_vwap_ust(s):
    """Fiyat gün-içi VWAP'ın ÜSTÜNDE mi (trend bağlamı)."""
    return s.get("gun_vwap_ust")


def vwap_uzak(s):
    """Fiyat gün-içi VWAP'tan ≥%0.8 uzak mı (ekstrem gerilme → mean-reversion)."""
    return s.get("vwap_uzak")


def vwap_fade_uyum(s):
    """VWAP gerilmesi işlem yönüyle uyumlu mu (fade: karşı yöne gerilmiş; trend: yönünde geçmiş)."""
    return s.get("vwap_fade_uyum")


def frvp_vah(s):
    """Fiyat gün-içi FRVP değer alanı ÜST kenarında mı (VAH = direnç)."""
    return s.get("frvp_vah")


def frvp_val(s):
    """Fiyat gün-içi FRVP değer alanı ALT kenarında mı (VAL = destek)."""
    return s.get("frvp_val")


def frvp_poc(s):
    """Fiyat gün-içi FRVP POC'ta mı (en yoğun hacim = mıknatıs)."""
    return s.get("frvp_poc")


def frvp_kenar_fade(s):
    """Değer alanı kenarı işlem yönüyle uyumlu mu (fade: SHORT@VAH/LONG@VAL kenar dönüşü)."""
    return s.get("frvp_kenar_fade")


def delta_patlama(s):
    """Yüksek hacim + büyük delta ama absorpsiyon DEĞİL → agresif patlama hareketi."""
    return s.get("delta_patlama")


def delta_kuruma(s):
    """Hacim kuruması (vol z ≤ −0.5) — likidite boşluğu / ilgisizlik."""
    return s.get("delta_kuruma")


def delta_divergence(s):
    """Gizli delta uyumsuzluğu (fade: ekstremde ters CVD; trend: yön CVD teyidi)."""
    return s.get("delta_divergence")


def opsiyon_duvar_yakin(s):
    """0DTE call/put duvarı + Max Pain konfluensi. GEÇMİŞ opsiyon verisi YOK →
    backtest'te None (kullanılmaz); canlıda confirm olarak bağlanabilir (KALAN İŞ)."""
    return s.get("opsiyon_duvar_ok")


def seans_absorp_up(s):
    """SEANS-ÖLÇEKLİ absorpsiyon YUKARI: girişten önceki ~60 barda satıcı deltası
    geldi ama fiyat düşmedi (absorbe) + fiyat kademeli yukarı sürüldü. Kullanıcının
    FP gözlemi ('satıcı pasife düşüyor + fiyat sürülüyor')."""
    return s.get("seans_absorp_up")


def seans_absorp_dn(s):
    """SEANS-ÖLÇEKLİ absorpsiyon AŞAĞI (ayna): alıcı pasife düşüyor + fiyat aşağı sürülüyor."""
    return s.get("seans_absorp_dn")


def seans_absorp_fade_uyum(s):
    """Seans absorpsiyonu işlem yönüyle uyumlu mu — sürülen yöne fade = TEHLİKE
    (fade: SHORT ancak seans-DOWN'da / LONG ancak seans-UP'ta; trend: yönünde sürülüyorsa teyit)."""
    return s.get("seans_absorp_fade_uyum")


BLOKLAR = {
    "cvd_yon": cvd_yon,
    "cvd_guclu": cvd_guclu,
    "poc_taraf": poc_taraf,
    "fib_ekstrem": fib_ekstrem,
    "footprint_absorpsiyon": footprint_absorpsiyon,
    "footprint_balina": footprint_balina,
    "footprint_yuksek_hacim": footprint_yuksek_hacim,
    "footprint_trapped": footprint_trapped,
    "footprint_kalicilik": footprint_kalicilik,
    "range_rejimi": range_rejimi,
    "trend_rejimi": trend_rejimi,
    "mod_fade": mod_fade,
    "mod_trend": mod_trend,
    "gun_bias_uyum": gun_bias_uyum,
    "breakout_teyit": breakout_teyit,
    "oi_yuksek": oi_yuksek,
    "whale_retail_zit": whale_retail_zit,
    "oi_tuzak": oi_tuzak,
    # Gelecek (veri eklenince): None döndükçe keşifte kullanılmaz
    "htf_vwap": htf_vwap,
    "htf_vpfr": htf_vpfr,
    "dvol_rejim": dvol_rejim,
    "dvol_skew_bearish": dvol_skew_bearish,
    "vol_sinyali": vol_sinyali,
    "makro_korelasyon": makro_korelasyon,
    # YENİ (kullanıcı isteği — genişletilmiş uzay)
    "gun_vwap_ust": gun_vwap_ust,
    "vwap_uzak": vwap_uzak,
    "vwap_fade_uyum": vwap_fade_uyum,
    "frvp_vah": frvp_vah,
    "frvp_val": frvp_val,
    "frvp_poc": frvp_poc,
    "frvp_kenar_fade": frvp_kenar_fade,
    "delta_patlama": delta_patlama,
    "delta_kuruma": delta_kuruma,
    "delta_divergence": delta_divergence,
    "opsiyon_duvar_yakin": opsiyon_duvar_yakin,   # backtest'te None (veri yok)
    "seans_absorp_up": seans_absorp_up,
    "seans_absorp_dn": seans_absorp_dn,
    "seans_absorp_fade_uyum": seans_absorp_fade_uyum,
}

# Şu an VERİSİ olan, keşifte kullanılabilecek bloklar (öğrendikçe genişler).
AKTIF_BLOKLAR = [
    "cvd_yon", "poc_taraf",
    "footprint_absorpsiyon", "footprint_balina",
    "footprint_yuksek_hacim", "footprint_trapped", "footprint_kalicilik",
    "range_rejimi",                    # trend gününde fade'i eler (mean-reversion koruması)
    "trend_rejimi",                    # trend gününde breakout adayını seçer (dual-mode)
    "mod_fade", "mod_trend",           # aday modu (range→fade / trend→breakout)
    "gun_bias_uyum", "breakout_teyit", # trend teyit yığını (bias devamı + CVD/absorpsiyon)
    "oi_yuksek", "whale_retail_zit", "oi_tuzak",   # metrics varsa devreye girer (kısmi-veri OK)
    "htf_vwap", "htf_vpfr",             # klines'tan hesaplanır (her zaman var)
    # YENİ boyutlar (klines+aggTrades'ten, no-lookahead — genişletilmiş keşif uzayı):
    "gun_vwap_ust", "vwap_uzak", "vwap_fade_uyum",       # günlük VWAP mesafe/gerilme
    "frvp_vah", "frvp_val", "frvp_poc", "frvp_kenar_fade",  # gün-içi FRVP değer alanı konumu
    "delta_patlama", "delta_kuruma", "delta_divergence",   # delta-profil davranışı
    "seans_absorp_up", "seans_absorp_dn", "seans_absorp_fade_uyum",  # SEANS-ölçekli absorpsiyon (kullanıcı FP gözlemi)
    # "opsiyon_duvar_yakin" AKTIF DEĞİL: geçmiş opsiyon verisi yok → canlı confirm için KALAN
]


def blok_uygula(sinyal: dict, blok_adi: str):
    """Tek blok sonucu: True/False/None (None=veri yok/uygulanamaz)."""
    f = BLOKLAR.get(blok_adi)
    if f is None:
        return None
    try:
        return f(sinyal)
    except Exception:
        return None
