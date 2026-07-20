"""
oar_kanit_kapisi.py — KANIT KAPISI: serap-geçer kanıtı canlı karara bağlar (5e açığı)
════════════════════════════════════════════════════════════════════════════════════
5e AÇIĞI (CLAUDE.md): kanıtlı bulgular canlı OAR karar akışına BESLENMİYORDU. Bu modül
kanıtı canlı karara bağlar — AMA GÜVENLİ yönde.

⚠️ EN KRİTİK İLKE (CLAUDE.md GÜVEN İLKESİ + 5m + 5p): canlıya YALNIZ serap testinden
(DSR≥0.95 ∧ bootstrap CI-alt>0 ∧ FDR-geçer ∧ 5x-likidasyon=0) geçmiş sistem bağlanır.
`kanitli_bulgular.json` (hipotez motorunun LIFT/OOS kazananları) ADAYDIR, KANIT DEĞİL —
çoğu SERAP'tır (ör. "Pazar n49 OOS%100" tipi; 5m'de gerçek-işleme çevrilince DSR 0,
perm-p~1 çıktı). Bu kapı ikisini AYIRIR; naif "kazananı canlıya al" = canlıya gürültü
enjekte etmek olurdu. Kaynak: serap_testi_sonuc.json (git-senkron, repo kökü).

BAĞLAMA BİÇİMİ (ANAYASA #8 GÜVENLİ — şampiyon giriş mantığına DOKUNMAZ):
  • sistem_kanit(stil)  → canlı sistemin serap kanıt kartı (DSR/PF/beklenti/likidasyon).
  • canli_uygun(stil)   → bu sistem canlıya bağlanabilir mi (serap-geçer). 5p'nin kod hali.
  • aday_durum(...)      → bir kanitli_bulgular adayı serap-testli mi (çoğu: hayır → ADAY).
  • canli_kanit_ozeti()  → lider + site + karar bağlamı için tüm canlı sistemlerin kanıtı.
Canlı karar (_ac_karar / _ac_karar_trend) her işleme HANGİ kanıtlı sisteme ait olduğunu
+ DSR'sini iliştirir (attribution). INCUMBENT şampiyon için FAIL-OPEN: serap dosyası yoksa
şampiyonu DURDURMAZ (bilinen kanıtlı sistem çalışmaya devam eder); kapı YENİ sistemleri
canlıya almadan önce zorlanır.
"""
import json
from pathlib import Path

SERAP = Path(__file__).resolve().parent / "serap_testi_sonuc.json"

# canlı sistem stili → serap karne anahtarı
_STIL_KARNE = {
    "ekstrem_donus_fade": "ŞAMPİYON:ekstrem_donus_fade",    # SİSTEM 1 FADE
    "kirilim_devam_trend": "ŞAMPİYON:kirilim_devam_trend",  # SİSTEM 2 TREND
}


def _serap_yukle() -> dict:
    try:
        return json.loads(SERAP.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _karne(stil: str) -> dict | None:
    d = _serap_yukle()
    k = _STIL_KARNE.get(stil, f"ŞAMPİYON:{stil}")
    return (d.get("karneler") or {}).get(k)


def serap_gecer(karne: dict | None) -> bool:
    """
    Karne serap bateri EŞİĞİNİ geçti mi:
    DSR≥0.95 ∧ bootstrap beklenti CI-alt>0 ∧ FDR-geçer ∧ 5x-likidasyon=0.
    (CLAUDE.md 5l karar kuralı ile birebir.)
    """
    if not karne:
        return False
    try:
        dsr = (karne.get("deflated_sharpe") or {}).get("dsr")
        ci_alt = (karne.get("bootstrap_beklenti_ci") or {}).get("alt")
        fdr = karne.get("fdr_gecti")
        liq5 = ((karne.get("mc_equity") or {}).get("5x") or {}).get("likidasyon_orani")
        return (dsr is not None and dsr >= 0.95
                and ci_alt is not None and ci_alt > 0
                and fdr is True and liq5 == 0.0)
    except Exception:
        return False


def sistem_kanit(stil: str) -> dict | None:
    """Bir canlı sistemin (stil) serap kanıt kartı. Serap dosyası yoksa None."""
    karne = _karne(stil)
    if not karne:
        return None
    t = karne.get("temel") or {}
    return {
        "stil": stil,
        "serap_gecer": serap_gecer(karne),
        "karar": karne.get("karar"),
        "dsr": (karne.get("deflated_sharpe") or {}).get("dsr"),
        "pf": t.get("pf"), "beklenti": t.get("beklenti"),
        "wr": t.get("wr"), "n": t.get("n"),
        "likidasyon_5x": ((karne.get("mc_equity") or {}).get("5x") or {}).get("likidasyon_orani"),
    }


def canli_uygun(stil: str) -> bool:
    """
    Bu sistem canlıya BAĞLANABİLİR mi (serap-geçer)? 5p kuralının kod hali:
    'çoklu-şampiyon çıktısı SERAP TESTİNDEN geçmeden canlı portföye ALINMAZ'.
    YENİ sistem aktive edilmeden ÖNCE bu çağrılmalı. (Incumbent şampiyona zorlanmaz —
    canlı kararlar fail-open; serap dosyası eksikse bilinen şampiyon durmaz.)
    """
    return serap_gecer(_karne(stil))


def aday_durum(ad: str = "") -> dict:
    """
    kanitli_bulgular.json adayının canlı-uygunluk durumu. Bu dosya hipotez motorunun
    LIFT/OOS kazananıdır → ADAY; serap testinden (gerçek-işleme çevrilip DSR ölçülerek)
    geçmeden CANLIYA ALINMAZ. 5m: bu tip yüksek-WR/küçük-n kazananlar SERAP çıktı.
    """
    return {"ad": ad, "canli_uygun": False,
            "durum": "ADAY — serap testi bekliyor (LIFT/OOS kazananı ≠ kanıt)",
            "not": "Canlıya çıkması için gerçek-işleme çevrilip DSR≥0.95 geçmeli (oar_serap_testi)."}


def kanit_iliştir(stil: str) -> dict:
    """
    Canlı karara İLİŞTİRİLECEK hafif kanıt referansı (attribution). Her işlem hangi
    kanıtlı sisteme ait + DSR'si taşır. Serap dosyası yoksa incumbent'i DURDURMAZ
    (fail-open): sadece kanıt 'okunamadı' işaretlenir, işlem yine açılır.
    """
    k = sistem_kanit(stil)
    if not k:
        return {"kanit_sistem": stil, "kanit_dsr": None, "kanit_durum": "serap_okunamadi"}
    return {"kanit_sistem": stil, "kanit_dsr": k["dsr"],
            "kanit_serap_gecer": k["serap_gecer"],
            "kanit_durum": "serap_gecer" if k["serap_gecer"] else "serap_zayif"}


def canli_kanit_ozeti() -> dict:
    """Tüm canlı sistemlerin serap kanıt durumu (lider bağlamı + site + karar)."""
    out = {}
    for stil in _STIL_KARNE:
        k = sistem_kanit(stil)
        if k:
            out[stil] = k
    return out


def baglam_metni() -> str:
    """Lider/karar bağlamına eklenecek kısa metin (kanıt kapısı durumu)."""
    oz = canli_kanit_ozeti()
    if not oz:
        return "[KANIT KAPISI] serap_testi_sonuc.json okunamadı — canlı sistemler bilinen şampiyon olarak sürer (fail-open)."
    satir = []
    for stil, k in oz.items():
        durum = "✅ serap-geçer (canlıya uygun)" if k["serap_gecer"] else "⚠ serap-zayıf"
        satir.append(f"{stil}: {durum} · DSR {k['dsr']} · PF {k['pf']} · beklenti {k['beklenti']} · 5x-likid {k['likidasyon_5x']}")
    return ("[KANIT KAPISI · 5e bağı] Canlıya YALNIZ serap-geçer (DSR≥0.95) sistem bağlanır; "
            "kanitli_bulgular LIFT/OOS kazananları ADAYDIR (çoğu serap, canlıya ALINMAZ). "
            + " | ".join(satir))


if __name__ == "__main__":
    print(baglam_metni())
    import json as _j
    print(_j.dumps(canli_kanit_ozeti(), ensure_ascii=False, indent=2))
