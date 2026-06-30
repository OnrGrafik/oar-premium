"""
oar_paper_box — OAR paper-trade motoru saf fonksiyon testleri (ağ/async yok).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from oar_paper_box import (_net_fiyat_pct, _equity_carpani, _ac_karar,
                           _kapanis_kontrol, FEE_PCT)


def test_net_fiyat_pct_fee_dusulur():
    # SHORT: giris 100 → cikis 99 = +%1 gross, -fee
    assert _net_fiyat_pct("SHORT", 100, 99) == round(1.0 - FEE_PCT, 4)
    # LONG: giris 100 → cikis 101 = +%1 gross
    assert _net_fiyat_pct("LONG", 100, 101) == round(1.0 - FEE_PCT, 4)
    # Kayıp: LONG giris 100 → cikis 99
    assert _net_fiyat_pct("LONG", 100, 99) == round(-1.0 - FEE_PCT, 4)


def test_equity_carpani_kaldirac():
    # +%1 fiyat × 5x = +%5 equity
    assert _equity_carpani(1.0, 5) == 1.05
    # -%25 fiyat × 5x = -%125 → likidasyon (0, negatif değil)
    assert _equity_carpani(-25.0, 5) == 0.0


def test_ac_karar_short_confluence():
    analiz = {
        "yon": "SHORT", "fiyat": 100.0,
        "setup_listesi": ["OAR-CORE Confluence → SHORT"],
        "asia": {"high": 100.5, "low": 96.0, "poc": 98.25},
    }
    k = _ac_karar(analiz)
    assert k and k["yon"] == "SHORT"
    assert k["tp"] == 98.25                    # POC
    assert k["sl"] == round(100.5 * 1.002, 2)  # ekstrem üstü tampon
    assert k["tp"] < analiz["fiyat"] < k["sl"]


def test_ac_karar_long_confluence():
    analiz = {
        "yon": "LONG", "fiyat": 100.0,
        "setup_listesi": ["OAR-CORE Confluence → LONG"],
        "asia": {"high": 104.0, "low": 99.5, "poc": 101.75},
    }
    k = _ac_karar(analiz)
    assert k and k["yon"] == "LONG"
    assert k["tp"] == 101.75
    assert k["sl"] < analiz["fiyat"] < k["tp"]


def test_ac_karar_confluence_yoksa_none():
    # OAR-CORE yok → açma
    analiz = {"yon": "SHORT", "fiyat": 100, "setup_listesi": ["Asia High Breakout → LONG"],
              "asia": {"high": 101, "low": 98, "poc": 99.5}}
    assert _ac_karar(analiz) is None
    # Asia verisi eksik → None
    assert _ac_karar({"yon": "SHORT", "fiyat": 100,
                      "setup_listesi": ["OAR-CORE → SHORT"], "asia": {}}) is None


def test_kapanis_kontrol():
    short = {"yon": "SHORT", "tp": 98.0, "sl": 101.0}
    assert _kapanis_kontrol(short, high=101.2, low=99.0) == ("SL", 101.0)  # SL önce
    assert _kapanis_kontrol(short, high=99.5, low=97.5) == ("TP", 98.0)    # TP
    assert _kapanis_kontrol(short, high=100.0, low=99.0) is None           # ikisi de değil
    long = {"yon": "LONG", "tp": 102.0, "sl": 99.0}
    assert _kapanis_kontrol(long, high=101.0, low=98.5) == ("SL", 99.0)
    assert _kapanis_kontrol(long, high=102.5, low=100.0) == ("TP", 102.0)
