"""
walk_forward.py — Walk-Forward / OOS Doğrulama Sarmalayıcısı (YENİ — yalnız backtest)
═══════════════════════════════════════════════════════════════════════════════════
SORUN: autonomous_researcher / oar_autonomous_backtest tüm veride optimize ediyor
(in-sample); OOS (out-of-sample) doğrulama YOK → overfit riski.

ÇÖZÜM: Pencere kaydırmalı IN-SAMPLE optimize → OUT-OF-SAMPLE doğrula.
  ⚠️ KARAR METRİĞİ YALNIZCA OOS. In-sample skor sadece parametre SEÇMEK için
     kullanılır; sistemi değerlendiren rapor OOS dilimlerden gelir.

MEVCUT MOTORU SARMALAR — SİLMEZ/YENİDEN YAZMAZ:
  - Skorlama (Win Rate, Sharpe, Trade, Max DD, Calmar) → autonomous_researcher
    içindeki `_istatistik` + `puan_hesapla` AYNEN yeniden kullanılır.
  - Bu modül onları OOS dilimlerinde çağıran ince bir sarmalayıcıdır.

PARAMETRE ROBUSTLUĞU: iyi OOS sonucu bir PLATO mu (komşu parametreler de iyi)
yoksa tek ZİRVE mi (yalnız o nokta iyi = overfit)? → `parametre_robustlugu` raporlar.

ağır dep yok (saf python + autonomous_researcher).
"""
from autonomous_researcher import _istatistik, puan_hesapla, puan_etiketi


# ─────────────────────────────────────────────────────────────────────────────
# Dilim (fold) üretimi — sinyaller zamana göre IS/OOS'a bölünür
# ─────────────────────────────────────────────────────────────────────────────
def _zaman_araligi(sinyaller: list) -> tuple[int, int]:
    ts = [s["ts"] for s in sinyaller if "ts" in s]
    return (min(ts), max(ts)) if ts else (0, 0)


def dilimler(bas_ms: int, bit_ms: int, fold_sayisi: int, is_oran: float):
    """
    [bas,bit] aralığını `fold_sayisi` kaydırmalı pencereye böler.
    Her pencere: is_oran kadar IN-SAMPLE + kalanı OUT-OF-SAMPLE.
    Döner: [(is_bas, is_bit, oos_bas, oos_bit), ...]
    """
    toplam = bit_ms - bas_ms
    if toplam <= 0 or fold_sayisi < 1:
        return []
    pencere = toplam // fold_sayisi
    out = []
    for i in range(fold_sayisi):
        p_bas = bas_ms + i * pencere
        p_bit = p_bas + pencere if i < fold_sayisi - 1 else bit_ms
        kesim = p_bas + int((p_bit - p_bas) * is_oran)
        out.append((p_bas, kesim, kesim, p_bit))
    return out


def _dilimle(sinyaller: list, bas: int, bit: int) -> list:
    """ts ∈ [bas, bit) olan sinyaller."""
    return [s for s in sinyaller if bas <= s.get("ts", -1) < bit]


# ─────────────────────────────────────────────────────────────────────────────
# Tek parametre seti için metrik (mevcut skorlama yeniden kullanılır)
# ─────────────────────────────────────────────────────────────────────────────
def metrik(sinyaller: list) -> dict:
    """autonomous_researcher._istatistik + puan_hesapla → tek paket."""
    st = _istatistik(sinyaller)
    st["puan"] = puan_hesapla(st)
    st["seviye"] = puan_etiketi(st["puan"])
    return st


# ─────────────────────────────────────────────────────────────────────────────
# Parametre robustluğu: plato mu zirve mi?
# ─────────────────────────────────────────────────────────────────────────────
def parametre_robustlugu(oos_skorlar: dict, band: float = 10.0) -> dict:
    """
    oos_skorlar: {param_etiket: oos_puan}.
    En iyi parametrenin etrafında kaç parametre `band` içinde? Çok → PLATO (sağlam),
    tek başına yüksek → ZİRVE (overfit şüphesi).
    """
    if not oos_skorlar:
        return {"tip": "VERİ_YOK", "yakin_sayisi": 0}
    en_iyi_etiket = max(oos_skorlar, key=oos_skorlar.get)
    en_iyi = oos_skorlar[en_iyi_etiket]
    yakin = [k for k, v in oos_skorlar.items() if k != en_iyi_etiket and en_iyi - v <= band]
    diger = len(oos_skorlar) - 1
    plato = diger > 0 and len(yakin) >= max(1, diger // 2)
    return {
        "tip": "PLATO" if plato else "ZİRVE",
        "yorum": ("Komşu parametreler de iyi → sağlam (plato)" if plato
                  else "Yalnız tek parametre iyi → OVERFIT şüphesi (zirve)"),
        "en_iyi_param": en_iyi_etiket,
        "en_iyi_oos_puan": en_iyi,
        "band": band,
        "yakin_sayisi": len(yakin),
        "toplam_param": len(oos_skorlar),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ANA: Walk-forward döngüsü
# ─────────────────────────────────────────────────────────────────────────────
def walk_forward(sinyal_fn, grid: list, etiket_fn=str,
                 fold_sayisi: int = 4, is_oran: float = 0.7) -> dict:
    """
    sinyal_fn(param) -> list[sinyal]   (her sinyal: {ts, outcome, pct, fib})
        Mevcut backtest motorundan tüm aralık sinyallerini döndürür (param'a göre).
        Pahalı çağrıyı önlemek için param başına BİR kez çağrılır, fold'lar
        sinyalleri zaman dilimine bölerek IS/OOS yapar.
    grid: denenecek parametre seti listesi (autonomous_researcher.PARAMETRE_GRID gibi).
    etiket_fn(param) -> str: parametreyi okunur etikete çevirir.

    Mantık (her fold): IN-SAMPLE dilimde en yüksek puanlı param SEÇİLİR; o param'ın
    OUT-OF-SAMPLE dilim metriği TOPLANIR. Karar/rapor YALNIZCA OOS'tan.
    """
    # 1) Her param için tüm sinyalleri bir kez üret
    param_sinyal = {}
    for p in grid:
        try:
            param_sinyal[etiket_fn(p)] = (p, sinyal_fn(p) or [])
        except Exception as e:
            param_sinyal[etiket_fn(p)] = (p, [])

    # 2) Global zaman aralığından fold'ları kur
    tum = [s for _, (_, sg) in param_sinyal.items() for s in sg]
    bas, bit = _zaman_araligi(tum)
    fold_pencere = dilimler(bas, bit, fold_sayisi, is_oran)

    fold_raporlari = []
    oos_birikmis = {et: [] for et in param_sinyal}  # param → tüm OOS sinyalleri
    secilen_oos_puan = {et: [] for et in param_sinyal}

    for fi, (is_b, is_e, oos_b, oos_e) in enumerate(fold_pencere):
        # In-sample: her param için puan → en iyiyi seç
        is_puan = {}
        for et, (p, sg) in param_sinyal.items():
            is_puan[et] = metrik(_dilimle(sg, is_b, is_e))["puan"]
        if not is_puan:
            continue
        secilen = max(is_puan, key=is_puan.get)

        # Out-of-sample: SEÇİLEN param'ın OOS metriği = KARAR metriği
        oos_sinyal = _dilimle(param_sinyal[secilen][1], oos_b, oos_e)
        oos_m = metrik(oos_sinyal)
        oos_birikmis[secilen].extend(oos_sinyal)
        secilen_oos_puan[secilen].append(oos_m["puan"])

        # Robustluk için: bu fold'ta TÜM paramların OOS puanı
        fold_oos_puanlar = {
            et: metrik(_dilimle(sg, oos_b, oos_e))["puan"]
            for et, (_, sg) in param_sinyal.items()
        }
        fold_raporlari.append({
            "fold": fi,
            "secilen_param": secilen,
            "is_puan": is_puan[secilen],
            "oos_puan": oos_m["puan"],
            "oos_metrik": oos_m,
            "overfit_isareti": is_puan[secilen] - oos_m["puan"] >= 25,
            "robustluk": parametre_robustlugu(fold_oos_puanlar),
        })

    # 3) Toplu OOS değerlendirme (karar = OOS birikmiş)
    en_cok_secilen = max(
        ((et, len(v)) for et, v in secilen_oos_puan.items()), key=lambda x: x[1],
        default=(None, 0),
    )[0]
    toplu_oos = metrik(oos_birikmis.get(en_cok_secilen, [])) if en_cok_secilen else {}

    return {
        "fold_sayisi": len(fold_raporlari),
        "is_oran": is_oran,
        "karar_metrik": "OOS",  # in-sample DEĞİL
        "en_cok_secilen_param": en_cok_secilen,
        "toplu_oos_metrik": toplu_oos,
        "foldlar": fold_raporlari,
    }


def rapor(sonuc: dict) -> str:
    if not sonuc.get("foldlar"):
        return "Walk-forward: yeterli veri yok."
    o = sonuc.get("toplu_oos_metrik", {})
    sat = [
        "═══ WALK-FORWARD / OOS DOĞRULAMA ═══",
        f"Karar metriği: {sonuc['karar_metrik']} (in-sample DEĞİL) | "
        f"fold: {sonuc['fold_sayisi']} | IS oranı: {sonuc['is_oran']}",
        f"En çok seçilen param: {sonuc['en_cok_secilen_param']}",
        f"TOPLU OOS → Puan:{o.get('puan',0)} [{o.get('seviye','-')}] "
        f"WR%{o.get('win_rate',0)} Sharpe:{o.get('sharpe',0)} "
        f"Calmar:{o.get('calmar',0)} Trade:{o.get('toplam_sinyal',0)}",
        "── Fold detayı ──",
    ]
    for f in sonuc["foldlar"]:
        bayrak = " ⚠OVERFIT(IS≫OOS)" if f["overfit_isareti"] else ""
        sat.append(
            f"  #{f['fold']} {f['secilen_param']}: IS={f['is_puan']} OOS={f['oos_puan']}"
            f" [{f['robustluk']['tip']}]{bayrak}"
        )
    return "\n".join(sat)


# ─────────────────────────────────────────────────────────────────────────────
# Mevcut araştırma motoruna OOS köprüsü
# ─────────────────────────────────────────────────────────────────────────────
async def arastirma_oos(sym: str = "BTCUSDT", gun: int = 60,
                        fold_sayisi: int = 4, is_oran: float = 0.7) -> dict:
    """
    autonomous_researcher'ı OOS'a sarar. Mevcut backtest motoru sinyal-seviyesi
    çıktısı vermediği için, sinyal üreten bir backtest fonksiyonu mevcutsa onunla
    çalışır; yoksa açık uyarı döner (canlı API gerektirir → sandbox'ta test edilmez).
    """
    from autonomous_researcher import PARAMETRE_GRID
    # historical_backtest sinyal-seviyesi (ts'li) çıktı vermediğinden, entegrasyon
    # bir 'sinyal_fn' enjeksiyonu bekler. Burada yalnız iskeleti sağlarız.
    return {
        "uyari": ("Bu köprü, ts'li sinyal döndüren bir backtest fonksiyonu (sinyal_fn) "
                  "ile çağrılmalıdır. historical_backtest yalnız agregat istatistik "
                  "döndürdüğünden canlı entegrasyon sinyal_fn eklenince aktifleşir."),
        "kullanim": "walk_forward(sinyal_fn, PARAMETRE_GRID, fold_sayisi=..., is_oran=...)",
        "grid_boyutu": len(PARAMETRE_GRID),
    }
