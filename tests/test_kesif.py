"""
oar_kesif + oar_sinyaller — keşif motoru testleri (sentetik sinyaller, pandas yok).
Edge taşıyan blok kombinasyonu OOS'ta öne çıkmalı ve HOLDOUT'ta da ayakta kalmalı.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from oar_sinyaller import (cvd_yon, cvd_guclu, poc_taraf, fib_ekstrem, blok_uygula,
                           footprint_absorpsiyon, footprint_balina,
                           footprint_yuksek_hacim, footprint_trapped, AKTIF_BLOKLAR)
from oar_kesif import _filtre, _holdout_ayir, kesfet


def test_footprint_bloklari():
    assert footprint_absorpsiyon({"absorp": True}) is True
    assert footprint_balina({"balina": False}) is False
    assert footprint_yuksek_hacim({"vol_yuksek": True}) is True
    assert footprint_trapped({"reclaim": True}) is True
    # feature yoksa None (veri yok → keşifte atlanır)
    assert footprint_absorpsiyon({}) is None


def test_aktif_havuz_footprint_icerir():
    assert "footprint_balina" in AKTIF_BLOKLAR
    assert "footprint_trapped" in AKTIF_BLOKLAR
    # degenerate (her zaman True) bloklar aktif havuzda olmamalı
    assert "fib_ekstrem" not in AKTIF_BLOKLAR
    assert "cvd_guclu" not in AKTIF_BLOKLAR


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


def test_filtre_veri_yok_sinyal_atlanir():
    sinyaller = [{"ts": 1, "yon": "SHORT", "cvd_delta": -1}]
    # htf_vwap verisi yok → bu sinyal atlanır (komple kombinasyon düşmez) → boş liste
    assert _filtre(sinyaller, ["htf_vwap"]) == []


def test_filtre_kismi_veri_altkume():
    # İki sinyal: biri oi verili (geçer), biri oi'siz (atlanır) → sadece geçeni döner
    sinyaller = [
        {"ts": 1, "yon": "SHORT", "oi_yuksek": True, "reclaim": True},   # oi_tuzak True
        {"ts": 2, "yon": "SHORT"},                                       # oi yok → atla
    ]
    f = _filtre(sinyaller, ["oi_tuzak"])
    assert len(f) == 1 and f[0]["ts"] == 1


def test_vpfr_deger_alani():
    import oar_local_backtest as lb
    assert lb._vpfr_deger_alani({}) == (None, None, None)
    # POC=100 (en yüksek hacim); %70 değer alanı POC etrafında genişler
    bins = {98: 1.0, 99: 2.0, 100: 10.0, 101: 3.0, 102: 1.0}
    poc, vah, val = lb._vpfr_deger_alani(bins, va_pct=0.70)
    assert poc == 100
    assert val <= 100 <= vah          # değer alanı POC'u içerir
    assert 99 <= val and vah <= 101   # yüksek hacimli komşular önce eklenir


def test_htf_vpfr_blok():
    from oar_sinyaller import htf_vpfr, AKTIF_BLOKLAR
    assert htf_vpfr({"htf_vpfr_ok": True}) is True
    assert htf_vpfr({"htf_vpfr_ok": False}) is False
    assert htf_vpfr({}) is None
    assert "htf_vpfr" in AKTIF_BLOKLAR


def test_footprint_kalicilik_blok():
    from oar_sinyaller import footprint_kalicilik, AKTIF_BLOKLAR
    assert footprint_kalicilik({"kalicilik": True}) is True
    assert footprint_kalicilik({"kalicilik": False}) is False
    assert footprint_kalicilik({}) is None        # eski cache → feature yok
    assert "footprint_kalicilik" in AKTIF_BLOKLAR


def test_oi_tuzak_blok():
    from oar_sinyaller import oi_tuzak, AKTIF_BLOKLAR
    assert oi_tuzak({"oi_yuksek": True, "reclaim": True}) is True
    assert oi_tuzak({"oi_yuksek": True, "reclaim": False}) is False
    assert oi_tuzak({"oi_yuksek": False, "reclaim": True}) is False
    assert oi_tuzak({"reclaim": True}) is None        # oi verisi yok
    assert "oi_tuzak" in AKTIF_BLOKLAR


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
    # Beklenti/kârlılık metrikleri hesaplanmış olmalı
    for a in res["en_iyiler"]:
        b = a.get("beklenti")
        assert b and "ort_net" in b and "wr" in b and "rr" in b


def test_beklenti_hesap():
    from oar_kesif import _beklenti
    assert _beklenti([]) == {}
    s = [{"pct": 2.0}, {"pct": 2.0}, {"pct": -1.0}, {"pct": -1.0}]
    b = _beklenti(s)
    assert b["n"] == 4 and b["wr"] == 50.0
    assert b["ort_net"] == 0.5          # (2+2-1-1)/4
    assert b["toplam_net"] == 2.0
    assert b["profit_factor"] == 2.0    # 4 / 2
    assert b["rr"] == 2.0               # 2 / 1


def test_kesfet_bos_sinyal():
    res = kesfet([], min_trade=1)
    assert res["toplam_aday"] == 0 and res["en_iyiler"] == []


# ─── Çok-yıllı birleşik keşif (havuzlama mantığı, veri monkeypatch) ──────────
def test_yil_araligi():
    import oar_local_backtest as lb
    assert lb._yil_araligi("2019-01", "2023-12") == [2019, 2020, 2021, 2022, 2023]
    assert lb._yil_araligi("2024-03", "2024-09") == [2024]


def test_kesif_coklu_havuzlar(monkeypatch):
    import oar_local_backtest as lb
    # Loader'ları ve aday üreticiyi sahteleyip havuzlama + sembol etiketini test et
    monkeypatch.setattr(lb, "_klines_oku", lambda s, b, e: object())
    monkeypatch.setattr(lb, "_aggt_ay_yollari", lambda s, b, e: ["dummy"])
    monkeypatch.setattr(lb, "_metrics_oku", lambda s, b, e: None)
    monkeypatch.setattr(lb, "_gun_hazirla", lambda k, y, m=None: {})
    # Önbelleği izole et: test diske yazmasın/okumasın, hep yeniden hesaplasın
    monkeypatch.setattr(lb, "_aday_cache_oku", lambda s, b, e: None)
    monkeypatch.setattr(lb, "_aday_cache_yaz", lambda s, b, e, a: None)

    def sahte_aday(gunler, **kw):
        # yıl/sembol başına 30 aday (10 LOSS taban + cvd edge)
        out = []
        for i in range(30):
            win = i % 2 == 0
            out.append({"ts": i, "yon": "SHORT", "cvd_delta": -5 if win else 5,
                        "cvd_esik": 0, "fiyat": 100, "poc": None, "fib": 1.618,
                        "outcome": "WIN" if win else "LOSS",
                        "pct": 0.5 if win else -0.5})
        return out
    monkeypatch.setattr(lb, "aday_sinyaller_uret", sahte_aday)

    res = lb.kesif_coklu(["BTCUSDT", "ETHUSDT"], "2019-01", "2020-12",
                         min_trade=5, holdout_orani=0.2, ust_n=3)
    # 2 sembol × 2 yıl × 30 = 120 aday havuzda
    assert res["havuz_boyutu"] == 120
    assert len(res["veri_ozeti"]) == 4
    assert res["kesif"]["toplam_aday"] >= 1


# ─── OI / whale-retail blokları ──────────────────────────────────────────────
def test_oi_whale_retail_bloklari():
    from oar_sinyaller import oi_yuksek, whale_retail_zit, AKTIF_BLOKLAR
    assert oi_yuksek({"oi_yuksek": True}) is True
    assert whale_retail_zit({"whale_retail_zit": False}) is False
    # metrics yoksa feature yok → None (keşifte atlanır)
    assert oi_yuksek({}) is None
    assert whale_retail_zit({}) is None
    assert "oi_yuksek" in AKTIF_BLOKLAR and "whale_retail_zit" in AKTIF_BLOKLAR


def test_htf_vwap_blok():
    from oar_sinyaller import htf_vwap, AKTIF_BLOKLAR
    assert htf_vwap({"htf_vwap_yakin": True}) is True
    assert htf_vwap({"htf_vwap_yakin": False}) is False
    assert htf_vwap({}) is None          # klines yoksa/hesaplanmadıysa
    assert "htf_vwap" in AKTIF_BLOKLAR
