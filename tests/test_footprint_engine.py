"""
footprint_engine — elle hesaplanmış küçük örnekle doğrulama.
pandas yoksa atlanır.
Çalıştır (yerel): pytest tests/test_footprint_engine.py
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
pd = pytest.importorskip("pandas")

from footprint_engine import aggressor_delta, cvd_toplam, cvd_serisi, footprint, bar_delta_ozet


def _ornek():
    """
    Elle hesap için 4 trade (tek 5dk bar):
      t  price qty  is_buyer_maker  aggressor  delta
      0  100   2    False           BUY        +2
      1  100   1    True            SELL       -1
      2  101   3    False           BUY        +3
      3  101   5    True            SELL       -5
    Net CVD = 2 -1 +3 -5 = -1
    Footprint:
      fiyat 100: alis 2, satis 1, delta +1
      fiyat 101: alis 3, satis 5, delta -2
    """
    return pd.DataFrame([
        {"timestamp": 0,    "price": 100, "quantity": 2, "is_buyer_maker": False},
        {"timestamp": 1000, "price": 100, "quantity": 1, "is_buyer_maker": True},
        {"timestamp": 2000, "price": 101, "quantity": 3, "is_buyer_maker": False},
        {"timestamp": 3000, "price": 101, "quantity": 5, "is_buyer_maker": True},
    ])


def test_aggressor_delta_isaretleri():
    d = list(aggressor_delta(_ornek()))
    assert d == [2, -1, 3, -5]


def test_cvd_toplam():
    assert cvd_toplam(_ornek()) == -1


def test_cvd_serisi_kumulatif():
    _, cvd = cvd_serisi(_ornek())
    assert list(cvd) == [2, 1, 4, -1]


def test_footprint_seviye_delta():
    fp = footprint(_ornek(), bar_ms=300_000)
    d = {row.fiyat: (row.alis, row.satis, row.delta) for row in fp.itertuples()}
    assert d[100] == (2, 1, 1)
    assert d[101] == (3, 5, -2)


def test_bar_delta_ozet_poc():
    ozet = bar_delta_ozet(_ornek(), bar_ms=300_000)
    assert len(ozet) == 1
    r = ozet.iloc[0]
    assert r["delta"] == -1            # net bar delta
    assert r["hacim"] == 11            # 2+1+3+5
    assert r["poc_fiyat"] == 101       # en yüksek hacimli seviye (3+5=8 > 3)


def test_buyer_maker_string_normalize():
    df = _ornek()
    df["is_buyer_maker"] = ["false", "true", "false", "true"]
    assert cvd_toplam(df) == -1
