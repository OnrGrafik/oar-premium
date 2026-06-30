"""
oar_swing — fib-anchor çekirdeği testleri (saf, ağ yok).
Swing range: %15+ kırılım sonrası tepe-dip; LL/HH geçerlilik kuralı.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from oar_swing import pivotlar, swing_kurulum, fib_swing


def _m(o, h, l, c):
    return [0, o, h, l, c]


def test_fib_swing_oranlar():
    f = fib_swing(100.0, 200.0)
    assert f[0.0] == 100.0 and f[1.0] == 200.0 and f[0.5] == 150.0
    assert f[1.618] == 261.8 and f[-0.618] == round(100 + (-0.618) * 100, 8)


def test_pivot_tespit():
    # Tepe ortada (idx 3), dip idx 7
    mumlar = [_m(10, 11, 9, 10), _m(11, 12, 10, 11), _m(12, 13, 11, 12),
              _m(13, 20, 12, 19), _m(13, 14, 12, 13), _m(12, 13, 11, 12),
              _m(11, 12, 10, 11), _m(10, 11, 5, 6), _m(6, 8, 6, 7), _m(7, 9, 7, 8),
              _m(8, 10, 8, 9)]
    tepeler, dipler = pivotlar(mumlar, sol=2, sag=2)
    idx_tepe = [i for i, _ in tepeler]
    idx_dip = [i for i, _ in dipler]
    assert 3 in idx_tepe        # büyük tepe
    assert 7 in idx_dip         # büyük dip


def _seri_down_break():
    """Yukarıda range → AŞAĞI %15+ impuls → TEPE(bounce) → DİP(LL yok) → geçerli SHORT."""
    m = []
    # başta yatay ~100 (range)
    for _ in range(6):
        m.append(_m(100, 101, 99, 100))
    # AŞAĞI impuls: 100 → 80 (%20 düşüş), dip pivotu
    m += [_m(99, 99, 90, 91), _m(91, 92, 80, 81), _m(81, 82, 80, 81)]
    # bounce TEPE ~ 90
    m += [_m(81, 90, 81, 89), _m(89, 90, 88, 89), _m(89, 90, 87, 88)]
    # DİP ~ 84 (impuls dibi 80'in Üstünde → LL yok → geçerli)
    m += [_m(88, 89, 84, 85), _m(85, 86, 84, 85), _m(85, 87, 85, 86)]
    # kuyruk
    for _ in range(4):
        m.append(_m(86, 88, 85, 87))
    return m


def test_swing_down_break_short_gecerli():
    k = swing_kurulum(_seri_down_break(), esik_pct=15.0, sol=2, sag=2)
    assert k is not None
    assert k["yon"] == "SHORT" and k["kirilim"] == "DOWN"
    assert k["impuls_pct"] >= 15.0
    # range = [dip~84, tepe~90]; dip impuls dibi(80) üstünde → geçerli (LL yok)
    assert k["range_low"] >= 80
    assert k["range_high"] > k["range_low"]
    assert k["gecerli"] is True
    # fib range'den çekilmiş
    assert k["fibs"][0.0] == k["range_low"] and k["fibs"][1.0] == k["range_high"]


def test_swing_yetersiz_pivot_none():
    assert swing_kurulum([_m(100, 101, 99, 100)] * 5) is None
