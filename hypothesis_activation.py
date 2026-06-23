"""
Hypothesis Activation Engine — OAR Premium
═══════════════════════════════════════════════════════
GPT'nin önerdiği "rejim uyumlu hipotez aktivasyonu":

Backtest hipotezleri statik değildir — her biri YALNIZCA belirli piyasa
rejimlerinde yüksek kazanma oranı taşır. Bu modül:

1. KOMPOZİT REJİM ÜRETİR
   fiyat rejimi (regime_engine) + makro duruş (risk-on/off) +
   opsiyon gamma rejimi + move_source (spot/futures) birleştirilir.

2. UYUMLU HİPOTEZLERİ AKTİVE EDER
   Depodaki otomatik hipotezleri (theory_engine) tarar, mevcut rejimle
   uyumlu olanları "AKTİF" işaretler, uyumsuzları "BEKLEMEDE" bırakır.

   Örnek (GPT'nin volatilite arbitrajı tezi):
     NEGATİF GAMMA + risk-off + güçlü ters hareket + fib -0.272
     → yüksek kazanma oranlı LONG sweep setup'ı AKTİF olur.
"""

import asyncio
from datetime import datetime, timezone


# ─────────────────────────────────────────────────────────────────
# KOMPOZİT REJİM
# ─────────────────────────────────────────────────────────────────

async def kompozit_rejim(sembol: str = "BTCUSDT") -> dict:
    """
    4 kaynaktan tek bir rejim profili üretir.
    Döner: fiyat/makro/gamma/move alt rejimleri + birleşik etiket + risk yönü.
    """
    cur = "BTC" if "BTC" in sembol else "ETH" if "ETH" in sembol else sembol.replace("USDT", "")

    async def _fiyat_rejim():
        try:
            from regime_engine import rejim_tespit
            return await rejim_tespit(sembol)
        except Exception as e:
            return {"rejim": "UNKNOWN", "aciklama": str(e)[:60]}

    async def _gamma():
        try:
            from options_engine import gex_ozet
            g = await gex_ozet(cur)
            return {"gamma_rejim": g.get("gamma_rejim", "—"),
                    "zero_gamma": g.get("zero_gamma"), "spot": g.get("spot")}
        except Exception as e:
            return {"gamma_rejim": "—", "hata": str(e)[:60]}

    async def _makro():
        try:
            from confidence_engine import _macro_skoru
            m = await _macro_skoru()
            skor = m.get("skor", 0)
            durus = ("RISK_ON" if skor > 15 else
                     "RISK_OFF" if skor < -15 else "NÖTR")
            return {"durus": durus, "skor": skor, "aciklama": m.get("aciklama", "")}
        except Exception as e:
            return {"durus": "NÖTR", "skor": 0, "hata": str(e)[:60]}

    async def _move():
        try:
            from exchange_client import klines
            # Spot vs futures CVD (son 12x5m)
            spot_k = await klines(sembol, "5m", 12, futures=False)
            fut_k = await klines(sembol, "5m", 12, futures=True)
            def _cvd(rows):
                # taker buy bilgisi exchange_client'ta yok; proxy: kapanış-açılış yönlü hacim
                return sum((r[4] - r[1]) / r[1] * r[5] for r in rows if r[1])
            sc, fc = _cvd(spot_k), _cvd(fut_k)
            toplam = abs(sc) + abs(fc)
            if toplam == 0:
                return {"kaynak": "NÖTR", "spot_pct": 50}
            spot_pct = abs(sc) / toplam * 100
            kaynak = ("SPOT-DRIVEN" if spot_pct > 60 else
                      "FUTURES-DRIVEN" if spot_pct < 40 else "DENGELİ")
            return {"kaynak": kaynak, "spot_pct": round(spot_pct, 1),
                    "spot_yon": "+" if sc > 0 else "-",
                    "fut_yon": "+" if fc > 0 else "-"}
        except Exception as e:
            return {"kaynak": "BİLİNMİYOR", "hata": str(e)[:60]}

    fiyat, gamma, makro, move = await asyncio.gather(
        _fiyat_rejim(), _gamma(), _makro(), _move())

    fr = fiyat.get("rejim", "UNKNOWN")
    gr = gamma.get("gamma_rejim", "—")
    md = makro.get("durus", "NÖTR")
    mv = move.get("kaynak", "BİLİNMİYOR")

    negatif_gamma = "NEGATİF" in gr
    risk_off = md == "RISK_OFF"

    # Birleşik etiket — en belirgin durumu öne çıkar
    if negatif_gamma and risk_off:
        etiket = "VOLATİLİTE GENİŞLEMESİ (negatif gamma + risk-off)"
        risk_yonu = "YÜKSEK_VOL"
    elif fr in ("TREND_UP", "TREND_DOWN"):
        etiket = f"TRENDLİ ({fr})"
        risk_yonu = "TREND"
    elif fr == "PANIC" or (negatif_gamma and fr == "HIGH_VOL"):
        etiket = "PANİK / STRES"
        risk_yonu = "STRES"
    elif fr == "RANGE" and not negatif_gamma:
        etiket = "SAKİN RANGE (pozitif gamma stabilize)"
        risk_yonu = "RANGE"
    else:
        etiket = f"KARMA ({fr})"
        risk_yonu = "KARMA"

    return {
        "sembol": sembol,
        "etiket": etiket,
        "risk_yonu": risk_yonu,
        "fiyat_rejim": fr,
        "gamma_rejim": gr,
        "makro_durus": md,
        "move_source": mv,
        "detay": {"fiyat": fiyat, "gamma": gamma, "makro": makro, "move": move},
        "tarih": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────
# HİPOTEZ AKTİVASYONU
# ─────────────────────────────────────────────────────────────────

def _fib_tipi(fib: float) -> str:
    """Fib seviyesinin karakteri → hangi rejimde çalışır."""
    if fib <= 0.0:
        return "ALT_SWEEP"      # -0.272, 0.0 → dip avı / LONG ekstrem
    if fib >= 1.0:
        return "UST_SWEEP"      # 1.0, 1.272 → tepe avı / SHORT ekstrem
    if 0.4 <= fib <= 0.65:
        return "ORTALAMA"       # 0.5, 0.618 → mean reversion
    return "ARA"


def _uyum_skoru(fib_tipi: str, rejim: dict) -> tuple[bool, str]:
    """
    Bir hipotezin fib tipi mevcut kompozit rejimle uyumlu mu?
    Döner: (aktif_mi, gerekçe)
    """
    fr = rejim.get("fiyat_rejim", "")
    risk = rejim.get("risk_yonu", "")
    negatif_gamma = "NEGATİF" in rejim.get("gamma_rejim", "")
    risk_off = rejim.get("makro_durus") == "RISK_OFF"

    # GPT'nin volatilite arbitrajı: alt sweep + negatif gamma + risk-off
    if fib_tipi == "ALT_SWEEP" and negatif_gamma and risk_off:
        return True, "Volatilite arbitrajı: negatif gamma + risk-off + alt sweep → güçlü LONG dönüş bölgesi"
    if fib_tipi == "ALT_SWEEP" and fr in ("PANIC", "TREND_DOWN"):
        return True, f"{fr} içinde alt sweep → kapitülasyon dönüşü olasılığı yüksek"
    if fib_tipi == "UST_SWEEP" and fr in ("HIGH_VOL", "TREND_UP") and negatif_gamma:
        return True, "Üst sweep + negatif gamma + yükseliş → SHORT için stop avı bölgesi"
    if fib_tipi == "ORTALAMA" and risk == "RANGE":
        return True, "Sakin range + ortalama fib → mean reversion çalışır"
    if fib_tipi == "ORTALAMA" and fr in ("TREND_UP", "TREND_DOWN"):
        return False, "Trendde ortalama-dönüş zayıf — fib pullback teyit bekler"

    return False, f"Mevcut rejim ({rejim.get('etiket','?')}) bu kurulumla uyumsuz — beklemede"


async def aktif_hipotezler(sembol: str = "BTCUSDT", rejim: dict = None) -> dict:
    """
    Depodaki hipotezleri mevcut kompozit rejimle eşleştirir.
    Aktif (rejim uyumlu) ve beklemedeki hipotezleri ayırır.
    """
    if rejim is None:
        rejim = await kompozit_rejim(sembol)

    try:
        from theory_engine import son_hipotezler
        depo = son_hipotezler() or {}
    except Exception as e:
        return {"rejim": rejim, "aktif": [], "beklemede": [],
                "hata": f"hipotez deposu okunamadı: {str(e)[:60]}"}

    # son_hipotezler yapısı: {sym: {bulgular: [...]}} ya da düz liste olabilir
    bulgular = []
    if isinstance(depo, dict):
        if sembol in depo and isinstance(depo[sembol], dict):
            bulgular = depo[sembol].get("bulgular", [])
        elif "bulgular" in depo:
            bulgular = depo.get("bulgular", [])
        else:
            # tüm enstrümanları birleştir
            for v in depo.values():
                if isinstance(v, dict) and "bulgular" in v:
                    bulgular.extend(v["bulgular"])

    aktif, beklemede = [], []
    for h in bulgular:
        fib = h.get("fib", 0.5)
        ft = _fib_tipi(fib)
        uyumlu, gerekce = _uyum_skoru(ft, rejim)
        wr = h.get("win_rate", 0)
        durum = h.get("durum", "Testing")

        # Sadece çürütülmemiş ve makul örnekli hipotezler aday
        aday = durum != "Rejected" and h.get("ornek", 0) >= 20

        kayit = {
            "fib": fib,
            "fib_tipi": ft,
            "win_rate": wr,
            "durum": durum,
            "ornek": h.get("ornek", 0),
            "en_iyi_skor_aralik": h.get("en_iyi_skor_aralik"),
            "gerekce": gerekce,
        }
        if uyumlu and aday:
            # Rejim uyumu + yüksek WR → güçlü aktivasyon
            kayit["aktivasyon"] = "GÜÇLÜ" if (wr >= 60 and durum == "Confirmed") else "ORTA"
            aktif.append(kayit)
        else:
            beklemede.append(kayit)

    aktif.sort(key=lambda x: (x["aktivasyon"] != "GÜÇLÜ", -x["win_rate"]))
    beklemede.sort(key=lambda x: -x["win_rate"])

    return {
        "rejim": rejim,
        "aktif": aktif,
        "beklemede": beklemede[:8],
        "ozet": (f"{len(aktif)} hipotez '{rejim.get('etiket','?')}' rejimiyle uyumlu → aktif"
                 if aktif else
                 f"Mevcut rejim ({rejim.get('etiket','?')}) ile uyumlu aktif hipotez yok — temkinli ol"),
    }


if __name__ == "__main__":
    import json
    r = asyncio.run(kompozit_rejim("BTCUSDT"))
    print(json.dumps(r, ensure_ascii=False, indent=2))
