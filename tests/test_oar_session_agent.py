"""
oar_session_agent._oar_core_teyit — backtest-kanıtlı OAR-CORE trio'sunun canlı
karşılığı (sweep+reclaim+yüksek hacim). Saf fonksiyon, ağ/async yok.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from oar_session_agent import _oar_core_teyit


def _mum(o, h, l, c, v):
    return [o, h, l, c, v]


def _taban(n=20, v=100.0):
    # Düz, düşük hacimli taban mumlar (Asia 100-110 bandında)
    return [_mum(105, 106, 104, 105, v) for _ in range(n)]


def test_yetersiz_veri_none():
    assert _oar_core_teyit([], 110, 100) is None
    assert _oar_core_teyit(_taban(5), 110, 100) is None
    assert _oar_core_teyit(_taban(), 0, 0) is None


def test_ust_supurme_yuksek_hacim_short():
    mumlar = _taban()
    # Son mum: Asia High 110'u süpürüp altına kapanış + yüksek hacim
    mumlar.append(_mum(109, 112, 108, 109, 1000.0))
    sonuc = _oar_core_teyit(mumlar, 110, 100)
    assert sonuc is not None and sonuc[0] == "SHORT" and sonuc[1] >= 1.0


def test_alt_supurme_yuksek_hacim_long():
    mumlar = _taban()
    # Son mum: Asia Low 100'ü süpürüp üstüne kapanış + yüksek hacim
    mumlar.append(_mum(101, 102, 98, 101, 1000.0))
    sonuc = _oar_core_teyit(mumlar, 110, 100)
    assert sonuc is not None and sonuc[0] == "LONG" and sonuc[1] >= 1.0


def test_supurme_var_ama_hacim_dusuk_none():
    mumlar = _taban()
    # Süpürme+reclaim var ama hacim taban seviyesinde (vol_z < 1) → absorpsiyon yok
    mumlar.append(_mum(109, 112, 108, 109, 100.0))
    assert _oar_core_teyit(mumlar, 110, 100) is None


def test_reclaim_yok_none():
    mumlar = _taban()
    # Yüksek hacim ama High üstünde KAPANDI (reclaim yok) → teyit yok
    mumlar.append(_mum(109, 112, 109, 111.5, 1000.0))
    assert _oar_core_teyit(mumlar, 110, 100) is None
