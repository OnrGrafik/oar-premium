"""
komuta_merkezi.kutula_goreli — göreli (percentile) kova testleri.
Ağ gerekmez; saf sıralama mantığı.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from komuta_merkezi import kutula_goreli


def _coinler(konfidanslar):
    return [{"sembol": f"C{i}", "konfidans": k} for i, k in enumerate(konfidanslar)]


def test_dagilim_garanti_hepsi_az_olmaz():
    # Hepsi düşük konfidans (eskiden hepsi 'az' olurdu) → göreli dağılım
    skorlar = _coinler([10, 12, 8, 15, 9, 11, 7, 13, 14, 6,
                        16, 5, 17, 4, 18, 3, 19, 2, 20, 1])  # 20 coin
    kutula_goreli(skorlar)
    kutular = {}
    for s in skorlar:
        kutular[s["kutu"]] = kutular.get(s["kutu"], 0) + 1
    # 4 kovanın hepsi dolu olmalı — hiçbir kova boş değil
    assert set(kutular.keys()) == {"yuksek", "guvenli", "orta", "az"}
    assert kutular["yuksek"] >= 1
    # n=20, 0-tabanlı rank i/n<=0.15 → i=0..3 → 4 coin
    assert kutular["yuksek"] == 4


def test_en_yuksek_skor_yuksek_kovada():
    skorlar = _coinler([50, 10, 20, 90, 30])
    kutula_goreli(skorlar)
    d = {s["sembol"]: s["kutu"] for s in skorlar}
    # En yüksek (90 → C3) yüksek kovada olmalı
    assert d["C3"] == "yuksek"
    # En düşük (10 → C1) az kovada
    assert d["C1"] == "az"


def test_mutlak_kutu_saklanir():
    skorlar = [{"sembol": "X", "konfidans": 90, "kutu": "yuksek"}]
    kutula_goreli(skorlar)
    assert skorlar[0]["kutu_mutlak"] == "yuksek"


def test_bos_liste():
    assert kutula_goreli([]) == []
