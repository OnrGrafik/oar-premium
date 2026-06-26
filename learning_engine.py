"""
Learning Engine — OAR Premium
═══════════════════════════════════════════════════════
Paper Trade sonuçlarından öğrenerek iki şeyi günceller:

1. Agent ağırlıkları  — hangi agent öngörüleri daha güvenilir?
   Paper trade'i açan kararın hangi agentlar LONG/SHORT dediyse,
   o trade WIN ise o agentların ağırlığı hafifçe artar, LOSS ise düşer.
   EMA benzeri güncelleme (α=0.05) → ani sıçrama yok.

2. Wilson win rate (backtest güveni) — küçük örneklemler abartmasın.
   N=5'te %80 WR ≠ N=200'de %80 WR; Wilson alt sınırı güven aralığı verir.

AGIRLIKLAR sadece hafızada güncellenir; orijinal confidence_engine.py
varsayılanlarını override etmek için bu modülü import et.

Çalıştırma: confidence_engine.py import eder, main.py startup'ta başlatır.
"""

import math
import json
from pathlib import Path
import os

DATA_DIR = Path(os.environ.get("DATA_DIR") or
                ("/var/data" if Path("/var/data").exists() else "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
AGIRLIK_DOSYASI = DATA_DIR / "ogrenilmis_agirliklar.json"

# Başlangıç ağırlıkları (confidence_engine.py ile senkron)
_VARSAYILAN = {
    "oar":       0.30,
    "footprint": 0.20,
    "orderflow": 0.15,
    "volume":    0.10,
    "options":   0.10,
    "macro":     0.10,
    "backtest":  0.05,
}

_ALPHA = 0.05     # öğrenme hızı — her trade %5 hareket
_MIN_A = 0.02     # alt tavan — agent tamamen susturulmasın
_MAX_A = 0.55     # üst tavan — tek agent domine etmesin
_MIN_TRADE = 10   # güvenilir Wilson hesabı için minimum trade sayısı


def _yukle() -> dict:
    try:
        d = json.loads(AGIRLIK_DOSYASI.read_text())
        # Eksik anahtar varsa varsayılanla tamamla
        return {k: float(d.get(k, v)) for k, v in _VARSAYILAN.items()}
    except Exception:
        return dict(_VARSAYILAN)


def _kaydet(agirliklar: dict):
    AGIRLIK_DOSYASI.write_text(json.dumps(agirliklar, ensure_ascii=False, indent=2))


def agirliklar_al() -> dict:
    """Güncel ağırlıkları döndür. confidence_engine bunu kullanır."""
    return _yukle()


def trade_sonucundan_ogren(trade: dict, karar_detay: dict):
    """
    Kapatılan bir paper trade sonucuyla ağırlıkları güncelle.

    Args:
        trade:       persistence.trade_kapat() dönüşü
        karar_detay: db'deki kararlar.detay_json — agent_skorlar dahil
    """
    pnl = trade.get("pnl_pct", 0) or 0
    yon = trade.get("yon")
    agent_skorlar = karar_detay.get("agent_skorlar", {})
    if not agent_skorlar or not yon:
        return

    agirliklar = _yukle()

    for ad, a in agent_skorlar.items():
        if ad not in agirliklar:
            continue
        skor = a.get("skor", 0)
        guven = a.get("guvenis", 0)
        if guven == 0:
            continue  # veri yoktu — öğrenme yok

        # Agent ticaretle aynı yönde miydi?
        agent_dogru = (yon == "LONG" and skor > 0) or (yon == "SHORT" and skor < 0)

        # PnL pozitifse win, negatifse loss
        trade_kazandi = pnl > 0

        # Agent doğru taraftaysa ve trade kazandıysa → ağırlık artır
        # Diğer 3 kombinasyon → ağırlık düşür
        if agent_dogru == trade_kazandi:
            degisim = _ALPHA * abs(pnl) / 100   # büyük kazanç → daha fazla artış
        else:
            degisim = -_ALPHA * abs(pnl) / 100

        mevcut = agirliklar[ad]
        yeni = mevcut + degisim
        agirliklar[ad] = round(max(_MIN_A, min(_MAX_A, yeni)), 4)

    # Toplam 1.0'da normalize et (oranları koru)
    toplam = sum(agirliklar.values())
    if toplam > 0:
        agirliklar = {k: round(v / toplam, 4) for k, v in agirliklar.items()}

    _kaydet(agirliklar)

    # Yeterli geçmiş birikince EMA drift'ini KANIT'a bağla: her agent'ın tüm
    # geçmişteki Wilson doğruluğundan ağırlık türet (hardcoded prior'dan çık).
    try:
        agirliklari_yeniden_hesapla()
    except Exception:
        pass


def agirliklari_yeniden_hesapla(min_ornek: int = 10) -> dict:
    """
    TÜM kapanmış paper trade geçmişinden her agent'ın KANIT'lanmış doğruluğunu
    (Wilson alt sınırı, %90) hesaplar ve ağırlıkları buna göre kurar.
    — Sabit prior yerine VERİ konuşur: yüksek doğruluklu agent → yüksek ağırlık.
    — Yetersiz örneklemli agent (n<min_ornek) → varsayılan prior (shrinkage).
    — Hiç anlamlı kanıt yoksa mevcut (EMA/prior) ağırlıklar korunur.
    """
    try:
        import persistence as db
    except Exception:
        return _yukle()

    trades = db.trade_gecmisi(limit=5000)
    kapali = [t for t in trades if t.get("durum") == "CLOSED" and t.get("karar_id")]

    say = {ad: [0, 0] for ad in _VARSAYILAN}   # agent -> [dogru, toplam_sinyal]
    detay_cache = {}
    for t in kapali:
        kid = t["karar_id"]
        if kid not in detay_cache:
            detay_cache[kid] = db.karar_detay_json(kid) or {}
        agent_skorlar = detay_cache[kid].get("agent_skorlar", {})
        yon = t.get("yon")
        if not yon:
            continue
        trade_kazandi = (t.get("pnl_pct", 0) or 0) > 0
        for ad, a in agent_skorlar.items():
            if ad not in say or (a.get("guvenis", 0) or 0) == 0:
                continue
            skor = a.get("skor", 0)
            if skor == 0:
                continue
            agent_dogru = (yon == "LONG" and skor > 0) or (yon == "SHORT" and skor < 0)
            say[ad][1] += 1
            if agent_dogru == trade_kazandi:   # agent doğru tarafı işaret etti
                say[ad][0] += 1

    # Wilson edge (50 üstü = coin-flip'ten iyi) → ham ağırlık
    ham = {}
    for ad, (dogru, toplam) in say.items():
        if toplam >= min_ornek:
            wilson = wilson_alt_sinir(dogru, toplam)      # 0-100
            ham[ad] = max(0.0, (wilson - 50.0) / 50.0)    # 0..1 edge
        else:
            ham[ad] = None                                 # yetersiz veri

    if all(v is None for v in ham.values()) or not any(v for v in ham.values() if v):
        return _yukle()   # henüz anlamlı kanıt yok — mevcut ağırlıklar kalsın

    birlesik = {}
    for ad in _VARSAYILAN:
        if ham[ad] is None:
            birlesik[ad] = _VARSAYILAN[ad]          # az örneklem → prior
        else:
            birlesik[ad] = max(ham[ad], _MIN_A)     # edge yoksa alt tavan
    birlesik = {k: min(_MAX_A, max(_MIN_A, v)) for k, v in birlesik.items()}
    toplam = sum(birlesik.values())
    birlesik = {k: round(v / toplam, 4) for k, v in birlesik.items()}
    _kaydet(birlesik)
    return birlesik


# ─── Wilson Win Rate ──────────────────────────────────────────────

def wilson_alt_sinir(win: int, toplam: int, z: float = 1.645) -> float:
    """
    Wilson score interval alt sınırı — küçük örneklemde gerçek win rate tahmini.
    z=1.645 → %90 güven aralığı.
    Döner: 0-100 arası (yüzde)
    """
    if toplam == 0:
        return 50.0
    p = win / toplam
    n = toplam
    denom = 1 + z**2 / n
    centre = p + z**2 / (2*n)
    margin = z * math.sqrt(p*(1-p)/n + z**2/(4*n**2))
    alt = (centre - margin) / denom
    return round(max(0.0, min(1.0, alt)) * 100, 1)


def backtest_guven_skoru() -> dict:
    """
    Paper trade geçmişinden Wilson güven skoru üret.
    confidence_engine._backtest_skoru() bunu kullanır.
    """
    try:
        import persistence as db
        stat = db.trade_istatistik()
        toplam = stat.get("toplam", 0)
        win = stat.get("win", 0)

        if toplam < 3:
            return {
                "skor": 0, "yon": "NEUTRAL",
                "aciklama": f"Yetersiz veri ({toplam} trade)",
                "guvenis": 0
            }

        wilson_wr = wilson_alt_sinir(win, toplam)
        ham_wr = stat.get("win_rate", 50)

        # Wilson WR — %50 referans
        skor = max(-100, min(100, (wilson_wr - 50) * 2))

        # Örneklem büyüklüğüne göre güven
        if toplam >= 50:
            guven = 85
        elif toplam >= _MIN_TRADE:
            guven = 50 + toplam * 3   # 10'da 80, 20'de max
            guven = min(84, guven)
        else:
            guven = max(20, toplam * 5)

        return {
            "skor": round(skor, 1),
            "yon": "LONG" if skor > 10 else "SHORT" if skor < -10 else "NEUTRAL",
            "aciklama": (f"Forward test WR %{ham_wr} (Wilson alt sınır %{wilson_wr}) "
                         f"| {toplam} trade | PnL %{stat.get('toplam_pnl_pct', 0)}"),
            "guvenis": guven,
            "wilson_wr": wilson_wr,
            "ham_wr": ham_wr,
            "toplam": toplam,
        }
    except Exception as e:
        return {"skor": 0, "yon": "NEUTRAL",
                "aciklama": f"Learning engine hatası: {str(e)[:60]}", "guvenis": 0}


def agirlik_raporu() -> dict:
    """Mevcut öğrenilmiş ağırlıkları ve değişimleri göster."""
    mevcut = _yukle()
    rapor = {}
    for k, v in mevcut.items():
        varsayilan = _VARSAYILAN.get(k, 0)
        fark = round(v - varsayilan, 4)
        rapor[k] = {
            "mevcut": round(v, 4),
            "varsayilan": varsayilan,
            "fark": fark,
            "yon": "▲" if fark > 0.005 else "▼" if fark < -0.005 else "→"
        }
    return rapor


if __name__ == "__main__":
    print("Backtest güven skoru:", json.dumps(backtest_guven_skoru(), ensure_ascii=False, indent=2))
    print("\nAğırlık raporu:", json.dumps(agirlik_raporu(), ensure_ascii=False, indent=2))
