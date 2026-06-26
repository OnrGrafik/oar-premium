"""
Görev 6 — declarative kural köprüsü testleri.
Deterministik; canlı API gerekmez. DATA_DIR geçici dizine alınır.
"""
import os
import sys
import importlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for m in ("governance", "declarative_rules"):
        if m in sys.modules:
            del sys.modules[m]
    import governance, declarative_rules
    importlib.reload(governance)
    importlib.reload(declarative_rules)
    return declarative_rules


# confidence_karar çıktısı simülasyonu
def _baglam(karar="SHORT", konf=72, rejim="TREND_DOWN", of_yon="SHORT"):
    return {
        "karar": karar, "konfidans": konf, "sembol": "BTCUSDT",
        "rejim": {"rejim": rejim, "atr_pct": 2.1},
        "oy_dagilimi": {"LONG": 1, "SHORT": 5, "NEUTRAL": 1, "toplam": 7},
        "agent_skorlar": {"orderflow": {"yon": of_yon, "skor": -40}},
    }


def _kural():
    return {
        "ad": "Trend-down short teyit", "yon": "SHORT",
        "entry": {"tum": [
            {"alan": "rejim.rejim", "op": "in", "deger": ["TREND_DOWN", "HIGH_VOL"]},
            {"alan": "konfidans", "op": ">=", "deger": 60},
        ]},
        "confirmation": {"tum": [
            {"alan": "agent_skorlar.orderflow.yon", "op": "==", "deger": "SHORT"},
        ]},
        "stop": {"tip": "atr", "carpan": 1.5},
    }


def test_alan_oku_nokta_yolu(tmp_path, monkeypatch):
    dr = _env(tmp_path, monkeypatch)
    b = _baglam()
    assert dr.alan_oku(b, "rejim.rejim") == "TREND_DOWN"
    assert dr.alan_oku(b, "agent_skorlar.orderflow.yon") == "SHORT"
    assert dr.alan_oku(b, "yok.alan") is None


def test_sema_dogrulama(tmp_path, monkeypatch):
    dr = _env(tmp_path, monkeypatch)
    ok, h = dr.dogrula_sema(_kural())
    assert ok and h == []
    bozuk = {"ad": "x", "yon": "YUKARI", "entry": {"tum": [{"op": "??"}]}}
    ok2, h2 = dr.dogrula_sema(bozuk)
    assert ok2 is False and len(h2) >= 2


def test_degerlendir_tetik(tmp_path, monkeypatch):
    dr = _env(tmp_path, monkeypatch)
    r = dr.degerlendir(_kural(), _baglam())
    assert r["entry_ok"] and r["confirmation_ok"]
    assert r["tetik"] is True
    assert r["yon_uyumu"] is True


def test_degerlendir_entry_kosulu_saglanmaz(tmp_path, monkeypatch):
    dr = _env(tmp_path, monkeypatch)
    # rejim RANGE → entry'deki rejim koşulu düşer
    r = dr.degerlendir(_kural(), _baglam(rejim="RANGE"))
    assert r["entry_ok"] is False
    assert r["tetik"] is False


def test_confirmation_dusunce_tetik_yok(tmp_path, monkeypatch):
    dr = _env(tmp_path, monkeypatch)
    # orderflow LONG → confirmation düşer ama entry geçer
    r = dr.degerlendir(_kural(), _baglam(of_yon="LONG"))
    assert r["entry_ok"] is True
    assert r["confirmation_ok"] is False
    assert r["tetik"] is False


def test_determinizm_ayni_girdi_ayni_cikti(tmp_path, monkeypatch):
    dr = _env(tmp_path, monkeypatch)
    b = _baglam()
    r1 = dr.degerlendir(_kural(), b)
    r2 = dr.degerlendir(_kural(), b)
    assert r1 == r2


# ─── Görev 5 kapısı: ADAY değerlendirmeye girmez ────────────────────────────
def test_aday_kural_degerlendirmeye_girmez(tmp_path, monkeypatch):
    dr = _env(tmp_path, monkeypatch)
    k = dr.kural_ekle(_kural())  # durum ADAY
    assert k["durum"] == "ADAY"
    sonuc = dr.degerlendir_hepsi(_baglam(), yalniz_aktif=True)
    assert sonuc["tetikleyen_sayisi"] == 0  # ADAY sayılmaz
    # Aktiflesince girer
    r = dr.kural_aktiflestir(k["id"], oos_puan=80, rejim_uyumlu=True)
    assert r["ok"] is True
    sonuc2 = dr.degerlendir_hepsi(_baglam(), yalniz_aktif=True)
    assert sonuc2["tetikleyen_sayisi"] == 1


def test_aktiflestirme_oos_dususe_reddedilir(tmp_path, monkeypatch):
    dr = _env(tmp_path, monkeypatch)
    k = dr.kural_ekle(_kural())
    r = dr.kural_aktiflestir(k["id"], oos_puan=30, rejim_uyumlu=True)
    assert r["ok"] is False
    assert dr.aktif_kurallar() == []


def test_rbac_yetkisiz_red(tmp_path, monkeypatch):
    dr = _env(tmp_path, monkeypatch)
    with pytest.raises(PermissionError):
        dr.kural_ekle(_kural(), yazar="yabanci", rol="guest")
    k = dr.kural_ekle(_kural(), rol="owner")
    assert k["durum"] == "ADAY"


def test_gecersiz_sema_eklenmez(tmp_path, monkeypatch):
    dr = _env(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        dr.kural_ekle({"ad": "x", "yon": "BOZUK"})
