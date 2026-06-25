"""
walk_forward — OOS karar mantığı testleri (canlı API gerekmez, sentetik sinyaller).
Kabul kriteri: in-sample iyi / OOS kötü bir parametre setinin OOS'ta düştüğü görünür.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from walk_forward import dilimler, parametre_robustlugu, walk_forward, metrik


def _sinyal(ts, outcome, pct, fib=2.272):
    return {"ts": ts, "outcome": outcome, "pct": pct, "fib": fib}


def test_dilimler_is_oos_ayrimi():
    d = dilimler(0, 1000, fold_sayisi=2, is_oran=0.7)
    assert len(d) == 2
    is_b, is_e, oos_b, oos_e = d[0]
    assert is_b == 0 and oos_e == 500
    assert oos_b == is_e  # IS biter OOS başlar, çakışma yok


def test_parametre_robustlugu_zirve():
    # tek param çok yüksek, diğerleri düşük → ZİRVE
    r = parametre_robustlugu({"a": 90, "b": 40, "c": 35, "d": 38})
    assert r["tip"] == "ZİRVE"


def test_parametre_robustlugu_plato():
    r = parametre_robustlugu({"a": 90, "b": 88, "c": 85, "d": 83})
    assert r["tip"] == "PLATO"


def test_overfit_param_oos_ta_dusuyor():
    """
    'overfit' param: in-sample diliminde tüm WIN ama OOS diliminde tüm LOSS.
    'saglam' param: hem IS hem OOS dengeli WIN.
    walk_forward IS'te overfit'i seçse bile KARAR (OOS) metriği düşük olmalı.
    """
    # Tek fold: ts 0-69 IS (is_oran 0.7 → kesim 70), 70-99 OOS
    def sinyal_fn(param):
        if param == "overfit":
            # IS bölgesi: 2 küçük kayıp + sonra hep WIN (yüksek wr/sharpe/calmar)
            # → in-sample zirve yapar. OOS bölgesi (ts>=70): hep LOSS → çöker.
            kayip = [_sinyal(0, "LOSS", -1.0), _sinyal(4, "LOSS", -1.0)]
            is_win = [_sinyal(t, "WIN", 1.0 + (t % 3) * 0.5) for t in range(8, 70, 4)]
            oos = [_sinyal(t, "LOSS", -1.0) for t in range(70, 100, 3)]
            return kayip + is_win + oos
        else:  # saglam: her yerde ~%50 WIN (vasat ama tutarlı)
            return [_sinyal(t, "WIN" if (t // 5) % 2 else "LOSS",
                            1.0 if (t // 5) % 2 else -1.0) for t in range(0, 100, 5)]

    res = walk_forward(sinyal_fn, ["overfit", "saglam"],
                       fold_sayisi=1, is_oran=0.7)
    fold = res["foldlar"][0]
    # IS'te overfit zirve yapar → seçilir
    assert fold["secilen_param"] == "overfit"
    # Ama OOS'ta çöker → overfit işareti yanar
    assert fold["overfit_isareti"] is True
    assert fold["oos_puan"] < fold["is_puan"]
    assert res["karar_metrik"] == "OOS"


def test_metrik_mevcut_skorlamayi_kullanir():
    sinyaller = [_sinyal(t, "WIN" if t % 2 else "LOSS", 1.0 if t % 2 else -1.0)
                 for t in range(20)]
    m = metrik(sinyaller)
    assert "win_rate" in m and "sharpe" in m and "calmar" in m
    assert "puan" in m and 0 <= m["puan"] <= 100
