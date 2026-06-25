"""
Golden-sample testleri — data_integrity.dogrula()
Ağır deps (pandas) yoksa otomatik atlanır (Render runtime'da kurulu değil).
Çalıştır (yerel):  pytest tests/test_data_integrity.py
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

pd = pytest.importorskip("pandas")  # pandas yoksa tüm dosya atlanır
from data_integrity import dogrula


# ─── Fixtures (sabit referans pencere) ──────────────────────────────────────
def _temiz_klines():
    """5 ardışık 1m bar, tutarlı OHLC, monoton zaman."""
    t0 = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC
    rows = []
    for i in range(5):
        c = 100 + i
        rows.append({
            "open_time": t0 + i * 60_000,
            "open": c, "high": c + 1, "low": c - 1, "close": c,
            "volume": 10.0 + i,
        })
    return pd.DataFrame(rows)


def _temiz_aggtrades():
    t0 = 1_704_067_200_000
    rows = []
    for i in range(5):
        rows.append({
            "timestamp": t0 + i * 1000,
            "price": 100.0 + i * 0.1,
            "quantity": 1.5,
            "is_buyer_maker": i % 2 == 0,
        })
    return pd.DataFrame(rows)


# ─── klines ─────────────────────────────────────────────────────────────────
def test_temiz_klines_gecer():
    ok, rap = dogrula(_temiz_klines(), "klines", interval="1m")
    assert ok is True, rap
    assert rap["hatalar"] == []
    assert rap["metrikler"]["satir"] == 5
    assert rap["metrikler"]["eksik_bar"] == 0


def test_ohlc_tutarsiz_yakalanir():
    df = _temiz_klines()
    df.loc[2, "low"] = 999  # low > high/open/close
    ok, rap = dogrula(df, "klines")
    assert ok is False
    assert any("OHLC" in h for h in rap["hatalar"])


def test_eksik_bar_gap_yakalanir():
    df = _temiz_klines().drop(index=2).reset_index(drop=True)  # ortadan bir bar sil
    ok, rap = dogrula(df, "klines", interval="1m")
    assert ok is False
    assert rap["metrikler"]["eksik_bar"] == 1
    assert any("eksik" in h for h in rap["hatalar"])


def test_duplicate_timestamp_yakalanir():
    df = _temiz_klines()
    df.loc[3, "open_time"] = df.loc[2, "open_time"]
    ok, rap = dogrula(df, "klines")
    assert ok is False
    assert rap["metrikler"]["duplicate"] >= 1


def test_negatif_hacim_yakalanir():
    df = _temiz_klines()
    df.loc[1, "volume"] = -5
    ok, rap = dogrula(df, "klines")
    assert ok is False
    assert any("negatif hacim" in h for h in rap["hatalar"])


def test_fat_finger_uyarisi():
    df = _temiz_klines()
    df.loc[3, "close"] = 500  # %400 sıçrama
    ok, rap = dogrula(df, "klines")
    assert rap["metrikler"]["outlier"] >= 1
    assert any("fat-finger" in u for u in rap["uyarilar"])


# ─── aggTrades ──────────────────────────────────────────────────────────────
def test_temiz_aggtrades_gecer():
    ok, rap = dogrula(_temiz_aggtrades(), "aggTrades")
    assert ok is True, rap


def test_aggtrades_buyer_maker_eksik():
    df = _temiz_aggtrades().drop(columns=["is_buyer_maker"])
    ok, rap = dogrula(df, "aggTrades")
    assert ok is False
    assert any("is_buyer_maker" in h for h in rap["hatalar"])


def test_aggtrades_negatif_miktar():
    df = _temiz_aggtrades()
    df.loc[0, "quantity"] = 0
    ok, rap = dogrula(df, "aggTrades")
    assert ok is False
