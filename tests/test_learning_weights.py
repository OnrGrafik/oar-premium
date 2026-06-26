"""
agirliklari_yeniden_hesapla — kanıta dayalı ağırlık testleri (DB'siz, monkeypatch).
Yüksek doğruluklu agent daha çok ağırlık almalı; yetersiz örneklem prior'a düşmeli.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _setup(tmp_path, monkeypatch, trades, detaylar):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import learning_engine as le
    importlib.reload(le)

    class _FakeDB:
        @staticmethod
        def trade_gecmisi(limit=5000):
            return trades
        @staticmethod
        def karar_detay_json(kid):
            return detaylar.get(kid, {})

    monkeypatch.setitem(sys.modules, "persistence", _FakeDB)
    return le


def _trade(tid, kid, yon, pnl):
    return {"id": tid, "karar_id": kid, "yon": yon, "pnl_pct": pnl, "durum": "CLOSED"}


def _detay(skorlar):
    # skorlar: {agent: skor}; guvenis>0 verir
    return {"agent_skorlar": {a: {"skor": s, "guvenis": 70} for a, s in skorlar.items()}}


def test_dogru_agent_daha_cok_agirlik(tmp_path, monkeypatch):
    # 'oar' her zaman doğru tarafı işaret ediyor; 'macro' hep yanlış.
    trades, detaylar = [], {}
    for i in range(20):
        kazanan = i % 2 == 0   # dönüşümlü WIN/LOSS
        yon = "LONG"
        pnl = 1.0 if kazanan else -1.0
        # oar: kazananı doğru bilir (WIN'de +, LOSS'ta -)
        oar_skor = 50 if kazanan else -50
        # macro: hep + (yani LOSS'larda yanılır)
        macro_skor = 50
        kid = 1000 + i
        trades.append(_trade(i, kid, yon, pnl))
        detaylar[kid] = _detay({"oar": oar_skor, "macro": macro_skor})

    le = _setup(tmp_path, monkeypatch, trades, detaylar)
    w = le.agirliklari_yeniden_hesapla(min_ornek=10)
    assert w["oar"] > w["macro"], f"doğru agent daha ağır olmalı: {w}"
    assert abs(sum(w.values()) - 1.0) < 1e-6   # normalize


def test_yetersiz_ornek_prior_korunur(tmp_path, monkeypatch):
    # Sadece 3 trade → min_ornek altı → anlamlı kanıt yok → mevcut korunur
    trades = [_trade(i, 100 + i, "LONG", 1.0) for i in range(3)]
    detaylar = {100 + i: _detay({"oar": 50}) for i in range(3)}
    le = _setup(tmp_path, monkeypatch, trades, detaylar)
    w = le.agirliklari_yeniden_hesapla(min_ornek=10)
    # Veri yetersiz → varsayılana eşit kalır
    assert w == le._yukle()


def test_normalize_toplam_bir(tmp_path, monkeypatch):
    trades, detaylar = [], {}
    for i in range(15):
        kid = 200 + i
        trades.append(_trade(i, kid, "SHORT", 1.0 if i % 3 else -1.0))
        detaylar[kid] = _detay({"oar": -40, "footprint": 30})
    le = _setup(tmp_path, monkeypatch, trades, detaylar)
    w = le.agirliklari_yeniden_hesapla(min_ornek=10)
    assert abs(sum(w.values()) - 1.0) < 1e-6
