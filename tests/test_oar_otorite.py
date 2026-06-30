"""
trading_supervisor._oar_kapi_uygula — OAR otorite kapısı (Faz 5) saf testleri.
Trade yalnız OAR aynı yönü onaylarsa geçer.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from trading_supervisor import _oar_kapi_uygula


def test_oar_onay_ayni_yon_gecer():
    karar, yon, mesaj, onay = _oar_kapi_uygula("TRADE_LONG", "LONG", "LONG")
    assert karar == "TRADE_LONG" and yon == "LONG" and onay is True


def test_oar_ters_yon_no_trade():
    karar, yon, mesaj, onay = _oar_kapi_uygula("TRADE_LONG", "LONG", "SHORT")
    assert karar == "NO_TRADE" and yon == "YOK" and onay is False


def test_oar_onay_yoksa_no_trade():
    karar, yon, mesaj, onay = _oar_kapi_uygula("TRADE_SHORT", "SHORT", None)
    assert karar == "NO_TRADE" and yon == "YOK" and onay is False


def test_zaten_no_trade_degismez():
    karar, yon, mesaj, onay = _oar_kapi_uygula("NO_TRADE", "YOK", "LONG")
    assert karar == "NO_TRADE" and mesaj is None
