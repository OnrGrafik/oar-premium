"""
oar_rapor + trade penceresi — saf mantık testleri (ağ/async yok, kontrol_ve_gonder hariç).
"""
import os
import sys
import asyncio
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from oar_rapor import ozet, rapor_metni, bu_hafta_islemler, kontrol_ve_gonder, _iso_hafta
from oar_paper_box import trade_penceresi_uygun


def test_trade_penceresi_yasak_uygun():
    # TR 23:00 = UTC 20:00 → yasak; TR 07:00 = UTC 04:00 → serbest başlar
    yasak = [datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc),   # UTC 20:00
             datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc),
             datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
             datetime(2026, 7, 1, 3, 59, tzinfo=timezone.utc)]
    serbest = [datetime(2026, 7, 1, 4, 0, tzinfo=timezone.utc),  # UTC 04:00 (TR 07:00)
               datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
               datetime(2026, 7, 1, 19, 59, tzinfo=timezone.utc)]
    for d in yasak:
        assert trade_penceresi_uygun(d) is False, d
    for d in serbest:
        assert trade_penceresi_uygun(d) is True, d


def test_ozet_ve_metin():
    trades = [{"equity_pct": 5.0, "pnl_usd": 50}, {"equity_pct": -2.0, "pnl_usd": -20}]
    oz = ozet(trades)
    assert oz["n"] == 2 and oz["kazanan"] == 1 and oz["wr"] == 50.0 and oz["net"] == 3.0
    assert ozet([]) is None
    m = rapor_metni("OAR BTC/ETH", "Günlük", "2026-07-01", oz)
    assert "OAR BTC/ETH" in m and "Günlük" in m and "%3.0" in m


def test_bu_hafta_filtre():
    now = datetime.now(timezone.utc)
    bu = _iso_hafta(now)
    trades = [{"kapanis": now.isoformat(), "equity_pct": 1},
              {"kapanis": "2020-01-01T00:00:00+00:00", "equity_pct": 1}]  # eski
    f = bu_hafta_islemler(trades)
    assert len(f) == 1


def test_kontrol_ilk_calisma_rapor_yok():
    # İlk çalışmada tracker boş → rapor gönderilmez, sadece işaretlenir
    gonderilenler = []
    async def tg(m): gonderilenler.append(m)
    durum = {"islemler": []}
    sonuc = asyncio.run(kontrol_ve_gonder(durum, "OAR TEST", tg))
    assert sonuc == [] and gonderilenler == []
    assert "rapor" in durum and durum["rapor"].get("son_gun")


def test_kontrol_ay_donumu_rapor_ve_temizlik():
    gonderilenler = []
    async def tg(m): gonderilenler.append(m)
    now = datetime.now(timezone.utc)
    gecen_ay = "2020-01"
    durum = {
        "islemler": [{"kapanis": "2020-01-15T00:00:00+00:00", "equity_pct": 4.0},
                     {"kapanis": now.isoformat(), "equity_pct": 1.0}],
        "rapor": {"son_gun": now.strftime("%Y-%m-%d"),
                  "son_hafta": _iso_hafta(now),
                  "son_ay": gecen_ay},   # ay değişmiş görünsün
    }
    sonuc = asyncio.run(kontrol_ve_gonder(durum, "OAR TEST", tg))
    assert "ay" in sonuc
    # Aylık rapordan sonra eski ay silinmeli → yalnız current ay kalır
    assert all(t["kapanis"][:7] == now.strftime("%Y-%m") for t in durum["islemler"])
