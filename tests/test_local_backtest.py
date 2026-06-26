"""
oar_local_backtest — deterministik çekirdek testleri (parquet/pandas gerekmez).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from oar_local_backtest import fib_seviyeleri, vpfr_teyit, degerlendir, fib_yonu


def test_fib_seviyeleri():
    f = fib_seviyeleri(100.0, 200.0)  # range=100
    assert f[1.272] == 100 + 100 * 1.272   # 227.2
    assert f[-0.618] == 100 + 100 * -0.618  # 38.2
    assert set(f.keys()) == {-0.272, -0.618, -1.272, -1.618, 1.272, 1.618, 2.272, 2.618}


def test_fib_yonu():
    assert fib_yonu(-1.272) == "LONG"   # alt sweep
    assert fib_yonu(2.272) == "SHORT"   # üst sweep


def test_vpfr_teyit():
    assert vpfr_teyit(100.0, 100.2, tol_pct=0.5) is True    # %0.2 yakın
    assert vpfr_teyit(100.0, 105.0, tol_pct=0.5) is False   # %5 uzak
    assert vpfr_teyit(100.0, None) is False


def test_degerlendir_long_win():
    out, pct = degerlendir(100.0, [100.5, 101.0], "LONG", esik_pct=0.5)
    assert out == "WIN" and pct >= 0.5


def test_degerlendir_long_loss():
    out, pct = degerlendir(100.0, [99.0], "LONG", esik_pct=0.5)
    assert out == "LOSS" and pct < 0


def test_degerlendir_short_win():
    out, pct = degerlendir(100.0, [98.0], "SHORT", esik_pct=0.5)
    assert out == "WIN" and pct > 0


def test_degerlendir_flat():
    out, pct = degerlendir(100.0, [100.1], "LONG", esik_pct=0.5)
    assert out == "FLAT"
