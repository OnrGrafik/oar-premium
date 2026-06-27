"""
oar_local_backtest — OAR Asia Range deterministik çekirdek testleri (pandas gerekmez).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from oar_local_backtest import (
    fib_seviyeleri, asia_gecerli, fib_yonu, temas_eden_fib,
    cvd_teyit, poc_teyit, degerlendir, _cvd_delta, _sinyaller_uret,
    tp_sl_seviyeleri, degerlendir_tpsl, FEE_PCT, SLIP_PCT,
)


def test_tp_sl_short_long():
    f = fib_seviyeleri(100.0, 200.0)  # mid(0.5)=150
    # SHORT girişi 1.618=261.8 → TP=150 (altta), SL=2.272=327.2 (üstte)
    tp, sl = tp_sl_seviyeleri(1.618, 261.8, f)
    assert tp == 150.0 and sl == 327.2
    # LONG girişi -0.618=38.2 → TP=150 (üstte), SL=-1.272=-27.2 yok→ altındaki fib
    tp2, sl2 = tp_sl_seviyeleri(-0.618, 38.2, f)
    assert tp2 == 150.0 and sl2 < 38.2


def test_degerlendir_tpsl_short_tp_win():
    # SHORT giriş 100, TP 95, SL 105. Fiyat 94'e iner → TP vurur, brüt +%6
    out, net = degerlendir_tpsl(100, "SHORT", 95, 105, [98, 94])
    assert out == "WIN"
    assert abs(net - (5.0 - FEE_PCT - SLIP_PCT)) < 1e-6   # (100-95)/100*100=5


def test_degerlendir_tpsl_short_sl_loss():
    # SHORT giriş 100, SL 105 önce vurulur → brüt -%5 → net negatif → LOSS
    out, net = degerlendir_tpsl(100, "SHORT", 95, 105, [106])
    assert out == "LOSS" and net < 0


def test_degerlendir_tpsl_maliyet_dusulur():
    # TP/SL vurulmaz, son fiyat girişe eşit → brüt 0, net = -(fee+slip) → LOSS
    out, net = degerlendir_tpsl(100, "LONG", 110, 90, [100])
    assert out == "LOSS"
    assert abs(net - (-(FEE_PCT + SLIP_PCT))) < 1e-6


def test_fib_seviyeleri_indikatorle_ayni():
    f = fib_seviyeleri(100.0, 200.0)  # r=100
    assert f[1.0] == 200.0 and f[0.0] == 100.0 and f[0.5] == 150.0
    assert f[2.272] == 327.2 and f[-0.618] == 38.2
    assert set(f.keys()) == {2.618, 2.272, 1.618, 1.377, 1.0, 0.5, 0.0,
                             -0.377, -0.618, -1.272, -1.618}


def test_asia_gecerli_yuzde1():
    assert asia_gecerli(101.0, 100.0, 1.0) is True   # tam %1
    assert asia_gecerli(100.5, 100.0, 1.0) is False  # %0.5 < %1


def test_fib_yonu():
    assert fib_yonu(2.272) == "SHORT"   # üst ekstrem
    assert fib_yonu(-0.618) == "LONG"   # alt ekstrem
    assert fib_yonu(1.0) == "SHORT"


def test_temas_eden_fib_sadece_ekstrem():
    f = fib_seviyeleri(100.0, 200.0)
    # 1.618 = 261.8; fiyat 262 → ±%0.1 içinde
    assert temas_eden_fib(262.0, f, 0.1) == 1.618
    # 0.5 = 150 ortalama, ekstrem değil → None
    assert temas_eden_fib(150.0, f, 0.1) is None
    # uzak fiyat → None
    assert temas_eden_fib(180.0, f, 0.1) is None


def test_cvd_teyit():
    assert cvd_teyit("SHORT", -5.0) is True
    assert cvd_teyit("SHORT", 5.0) is False
    assert cvd_teyit("LONG", 5.0) is True


def test_poc_teyit():
    assert poc_teyit("SHORT", 105, 100) is True   # fiyat POC üstü
    assert poc_teyit("SHORT", 95, 100) is False
    assert poc_teyit("LONG", 95, 100) is True
    assert poc_teyit("SHORT", 105, None) is True   # POC yoksa filtre yok


def test_degerlendir():
    assert degerlendir(100, [101], "LONG", 0.5)[0] == "WIN"
    assert degerlendir(100, [98], "LONG", 0.5)[0] == "LOSS"
    assert degerlendir(100, [98], "SHORT", 0.5)[0] == "WIN"
    assert degerlendir(100, [100.1], "LONG", 0.5)[0] == "FLAT"


def test_cvd_delta_pencere():
    cvd_map = {0: 0.0, 5: 10.0, 10: 4.0}  # dk→cvd
    ts10 = 10 * 60_000
    # dk10 cvd=4, 10dk öncesi dk0 cvd=0 → delta +4
    assert _cvd_delta(cvd_map, ts10, 10) == 4.0
    ts5 = 5 * 60_000
    # dk5 cvd=10, 5dk öncesi dk0=0 → +10
    assert _cvd_delta(cvd_map, ts5, 5) == 10.0


def test_sinyaller_uret_short_setup():
    # Asya: low=100 high=110 (%10 geçerli). 1.618 = 100+10*1.618 = 116.18
    # post bar 116.2'ye değer (SHORT), CVD bearish, POC altında → SHORT sinyal
    gun = {
        0: {
            "a_h": 110.0, "a_l": 100.0,
            "fibs": fib_seviyeleri(100.0, 110.0),
            "post_ts": [4 * 3_600_000, 4 * 3_600_000 + 60_000],
            "post_close": [116.2, 117.0],
            "cvd_map": {int((4 * 3_600_000) // 60_000): -50.0,
                        int((4 * 3_600_000) // 60_000) - 15: 0.0},
            "poc": 108.0,   # fiyat 116 > POC → SHORT teyit
        }
    }
    param = ("test", 1.0, 4, 15, True, 0.10)
    s = _sinyaller_uret(gun, param)
    assert len(s) == 1
    assert s[0]["yon"] == "SHORT"
    assert s[0]["fib"] == 1.618


def test_sinyaller_uret_gecersiz_asia_atlanir():
    gun = {0: {"a_h": 100.5, "a_l": 100.0, "fibs": fib_seviyeleri(100.0, 100.5),
               "post_ts": [0], "post_close": [200.0], "cvd_map": {}, "poc": None}}
    # %0.5 < %1 → geçersiz → sinyal yok
    assert _sinyaller_uret(gun, ("t", 1.0, 4, 15, False, 0.1)) == []
