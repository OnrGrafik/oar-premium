"""
oar_kesif + oar_sinyaller — keşif motoru testleri (sentetik sinyaller, pandas yok).
Edge taşıyan blok kombinasyonu OOS'ta öne çıkmalı ve HOLDOUT'ta da ayakta kalmalı.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from oar_sinyaller import cvd_yon, cvd_guclu, poc_taraf, fib_ekstrem, blok_uygula
from oar_kesif import _filtre, _holdout_ayir, kesfet


# ─── Blok birim testleri ─────────────────────────────────────────────────────
def test_bloklar():
    assert cvd_yon({"yon": "SHORT", "cvd_delta": -5}) is True
    assert cvd_yon({"yon": "LONG", "cvd_delta": -5}) is False
    assert cvd_guclu({"cvd_delta": -10, "cvd_esik": 5}) is True
    assert cvd_guclu({"cvd_delta": -2, "cvd_esik": 5}) is False
    assert poc_taraf({"yon": "SHORT", "fiyat": 105, "poc": 100}) is True
    assert fib_ekstrem({"fib": 1.618}) is True
    assert fib_ekstrem({"fib": 0.5}) is False


def test_blok_uygula_veri_yok_none():
    # Gelecek blok (htf_vwap) verisi yoksa None
    assert blok_uygula({}, "htf_vwap") is None
    assert blok_uygula({}, "olmayan_blok") is None


def test_filtre_none_veri_yok():
    sinyaller = [{"ts": 1, "yon": "SHORT", "cvd_delta": -1}]
    # htf_vwap verisi yok → kombinasyon uygulanamaz → None
    assert _filtre(sinyaller, ["htf_vwap"]) is None


def test_holdout_ayir_zaman():
    sinyaller = [{"ts": t} for t in range(10)]
    arama, holdout = _holdout_ayir(sinyaller, 0.2)
    assert len(holdout) == 2 and len(arama) == 8
    assert max(s["ts"] for s in arama) < min(s["ts"] for s in holdout)


# ─── Keşif: edge'li kombinasyon öne çıkmalı + holdout doğrulamalı ────────────
def _sentetik():
    """
    Taban WR ~%50 (pass-through bloklar bunu görür → düşük puan).
    cvd_delta SONUÇLA ilişkili: WIN→cvd doğru tarafta, LOSS→ters. Böylece SADECE
    'cvd_yon' filtresi yüksek WR üretir → keşifte en üst sırada çıkmalı.
    120 sinyal, zamanca yayılmış; yon ve sonuç ayrıştırılmış (taban %50).
    """
    sinyaller = []
    for i in range(120):
        yon = "SHORT" if (i % 4) in (0, 1) else "LONG"   # yön, sonuçtan bağımsız
        win = (i % 2 == 0)                                # taban %50 WR
        # cvd: WIN→doğru taraf (cvd_yon True), LOSS→ters (False); %10 gürültü
        gurultu = (i % 10 == 7)
        dogru_taraf = win != gurultu
        cvd = (-5 if yon == "SHORT" else 5) if dogru_taraf else (5 if yon == "SHORT" else -5)
        out = "WIN" if win else "LOSS"
        pct = (0.3 + (i % 5) * 0.15) if win else -0.5
        sinyaller.append({"ts": i, "yon": yon, "cvd_delta": cvd, "cvd_esik": 0,
                          "fib": 1.618 if yon == "SHORT" else -0.618, "fiyat": 100,
                          "poc": None, "outcome": out, "pct": pct})
    return sinyaller


def test_kesfet_edge_kombinasyon_one_cikar():
    res = kesfet(_sentetik(), min_k=1, max_k=2, fold=3, is_oran=0.7,
                 holdout_orani=0.2, min_trade=10, ust_n=5)
    assert res["toplam_aday"] >= 1
    assert res["holdout_sinyal"] > 0
    # En iyi adayların hepsinde holdout metrikleri hesaplanmış olmalı
    for a in res["en_iyiler"]:
        assert "holdout_puan" in a and "saglam" in a
    # cvd_yon edge taşıyor → en iyi adaylardan biri cvd_yon içermeli
    assert any("cvd_yon" in a["bloklar"] for a in res["en_iyiler"])


def test_kesfet_bos_sinyal():
    res = kesfet([], min_trade=1)
    assert res["toplam_aday"] == 0 and res["en_iyiler"] == []
