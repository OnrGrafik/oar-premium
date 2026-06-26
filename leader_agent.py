"""
Lider Agent v2 — OAR Premium
════════════════════════════════════════════════════════════════════
3 katmanlı multi-agent sistem:

  Lider Agent
    ├── Research Agent  → matematiksel/bilimsel sinyal analizi
    ├── Backtest Agent  → geçmiş veri üzerinde hipotez testi
    └── Shared Memory   → tüm agentlar aynı bilgi bankasını okur/yazar

Bot kataloğu (tümü bot.py içinde):
  UTBot         → ETH/USDT, xATR trailing stop, BTC trend filtresi, RSI
  MA Scanner    → MA temas + whale/retail filtresi
  CVD Scanner   → CVD momentum + OI + hacim skoru (0-100)
  Asia Ekstrem  → Asia Range fib ekstrem temas (-1.618/-1.272/+2.272/+2.618)
  Balina Bot    → 1000+ BTC tek işlem (aggTrade)
  Volume Bot    → Hacim+OI %5+ artış + VWAP/MA20 üstü
  Korelasyon    → Nasdaq/DXY/Altın/Tahvil korelasyon rejimi
  Whale Tracker → BlackRock/MicroStrategy cüzdan hareketi
  Makro Alarm   → CW/PW/ZG seviyeleri (Vercel API)
"""

import os, json, asyncio, httpx
from pathlib import Path
from datetime import datetime, timezone, timedelta

# OAR Backtest sonuçlarını oku
def _oar_backtest_ozet() -> str:
    """En son OAR tam backtest sonucunu kısa özet olarak döner."""
    try:
        # GERÇEK OAR backtest sonucu oar_autonomous_backtest motorundadır
        # (historical_backtest'te 'oar_gecmis_testler' diye bir fonksiyon hiç yoktu
        #  → eski hali her zaman ImportError verip "okunamadı" dönüyordu).
        from oar_autonomous_backtest import son_sonuc
        s = son_sonuc()
        en_iyi = s.get("en_iyi")
        if not en_iyi:
            return "OAR backtest henüz yok."
        st = en_iyi.get("stats", {}) or {}
        son_run = (s.get("son_5_run") or [{}])[-1]
        return (
            f"OAR Backtest ({son_run.get('sembol','BTCUSDT')} {son_run.get('gun','?')}g, "
            f"{s.get('toplam_run',0)} koşu): En iyi '{en_iyi.get('id','?')}' "
            f"Puan {en_iyi.get('puan',0)} | WR %{st.get('wr',0)} | "
            f"Sharpe {st.get('sharpe',0)} | PnL {st.get('pnl',0):+.1f}% | "
            f"MaxDD -%{st.get('max_dd',0)} | "
            f"Fib: {json.dumps(st.get('by_fib', {}), ensure_ascii=False)[:200]}"
        )
    except Exception as e:
        return f"OAR backtest okunamadı: {e}"

# OAR Kural Bankası bağlamı
def _oar_kural_baglami() -> str:
    """Leader Agent promptuna kullanıcı tanımlı kuralları ekle."""
    try:
        from oar_rules import agent_baglami
        return agent_baglami(max_kural=12)
    except Exception:
        return ""

# Deribit opsiyon bağlamı
async def _deribit_ozet() -> str:
    """GEX, Call/Put Wall, Max Pain, DVOL — Lider Agent promptuna eklenir."""
    try:
        async with httpx.AsyncClient(timeout=12) as cl:
            # DVOL
            r = await cl.get("https://www.deribit.com/api/v2/public/get_volatility_index_data",
                params={"currency": "BTC", "start_timestamp": int(__import__("time").time()*1000) - 3600000,
                        "end_timestamp": int(__import__("time").time()*1000), "resolution": "3600"})
            dvol = None
            if r.status_code == 200:
                data = r.json().get("result", {}).get("data", [])
                if data: dvol = data[-1][4]

            # Anlık spot
            sp = await cl.get("https://www.deribit.com/api/v2/public/get_index_price",
                              params={"index_name": "btc_usd"})
            spot = None
            if sp.status_code == 200:
                spot = sp.json().get("result", {}).get("index_price")

        parcalar = []
        if dvol:   parcalar.append(f"DVOL: {dvol:.1f}")
        if spot:   parcalar.append(f"BTC Spot: ${spot:,.0f}")

        # options_engine'den GEX
        try:
            from options_engine import gex_ozet
            gex = await gex_ozet("BTC")
            if gex and not gex.get("error"):
                parcalar.append(f"GEX Rejim: {gex.get('gamma_rejim', '?')}")
                if gex.get("call_wall"): parcalar.append(f"Call Wall: ${gex['call_wall']:,.0f}")
                if gex.get("put_wall"):  parcalar.append(f"Put Wall:  ${gex['put_wall']:,.0f}")
                if gex.get("max_pain"):  parcalar.append(f"Max Pain:  ${gex['max_pain']:,.0f}")
                if gex.get("zero_gamma"):parcalar.append(f"Zero Gamma: ${gex['zero_gamma']:,.0f}")
        except Exception:
            pass

        return " | ".join(parcalar) if parcalar else "Deribit verisi alınamadı"
    except Exception as e:
        return f"Deribit hatası: {str(e)[:60]}"

# Swing karar fonksiyonu
async def swing_karar(sinyal: dict) -> dict:
    """
    Açık pozisyon için swing taşıma kararı.
    Kural tabanlı (LLM'e gerek yok, kesin matematik):
      - GEX negatif + yön uyumlu → swing olası
      - DVOL < 50 → düşük volatilite, swing riskli
      - Funding yön uyumlu değil → swing YOK
    Dönüş: {swing: bool, reason: str, max_gun: int}
    """
    try:
        direction = sinyal.get("direction", sinyal.get("yon", ""))
        deribit   = await _deribit_ozet()

        # Funding kontrolü
        async with httpx.AsyncClient(timeout=8) as cl:
            sym = sinyal.get("symbol", sinyal.get("sembol", "BTCUSDT"))
            r   = await cl.get("https://fapi.binance.com/fapi/v1/fundingRate",
                               params={"symbol": sym, "limit": 1})
            funding = 0.0
            if r.status_code == 200 and r.json():
                funding = float(r.json()[-1]["fundingRate"])

        reasons = []
        swing   = True

        # Funding yön uyumu
        if direction == "LONG"  and funding < -0.002:
            swing = False; reasons.append(f"Funding negatif ({funding:.4f}) — LONG swing riskli")
        elif direction == "SHORT" and funding >  0.002:
            swing = False; reasons.append(f"Funding pozitif ({funding:.4f}) — SHORT swing riskli")
        else:
            reasons.append(f"Funding nötr ({funding:.4f}) ✓")

        # DVOL kontrolü
        if "DVOL:" in deribit:
            try:
                dvol_val = float(deribit.split("DVOL:")[1].split("|")[0].strip())
                if dvol_val > 80:
                    swing = False; reasons.append(f"DVOL yüksek ({dvol_val:.0f}) — volatilite swing'i zorlaştırır")
                elif dvol_val < 35:
                    swing = False; reasons.append(f"DVOL çok düşük ({dvol_val:.0f}) — momentum zayıf")
                else:
                    reasons.append(f"DVOL normal ({dvol_val:.0f}) ✓")
            except Exception:
                pass

        return {
            "swing":   swing,
            "reason":  " | ".join(reasons),
            "max_gun": 3 if swing else 0,
            "funding": funding,
        }
    except Exception as e:
        return {"swing": False, "reason": str(e), "max_gun": 0, "funding": 0.0}

DATA_DIR     = Path(os.environ.get("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

REPORT_FILE  = DATA_DIR / "leader_report.json"
PATTERN_FILE = DATA_DIR / "leader_patterns.json"
SIGLOG_FILE  = DATA_DIR / "oar_signals_log.json"   # bot.py'nin yazdığı dosya
MEMORY_FILE  = DATA_DIR / "agent_memory.json"       # shared memory
TASKS_FILE   = DATA_DIR / "agent_tasks.json"        # lider → research görev kuyruğu
HEALTH_FILE  = DATA_DIR / "bot_health.json"         # bot sağlık durumları

GEMINI_BASE  = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODEL = "gemini-2.5-flash"

# ── Bot Kataloğu ───────────────────────────────────────────────────────────────
BOT_KATALOG = {
    "UTBot": {
        "tip": "sinyal",
        "sembol": "ETHUSDT",
        "strateji": "xATR Trailing Stop + STC osilatör + RSI filtresi. BTC yön teyidi gerektirir.",
        "sinyal_tipi": ["LONG", "SHORT"],
        "veriler": ["Bybit OHLCV", "xATR", "STC", "RSI"],
        "zaman_dilimleri": ["5m"],
        "kritik_parametreler": {"leverage": 100, "daily_tp": 10, "daily_sl": 10},
    },
    "MA Scanner": {
        "tip": "sinyal",
        "sembol": "Top 100 futures",
        "strateji": "MA temas (±%0.50 tolerans) + Whale/Retail filtresi (7 günlük).",
        "sinyal_tipi": ["LONG", "SHORT"],
        "veriler": ["Binance futures klines", "analiz_bot whale/retail"],
        "zaman_dilimleri": ["1d"],
        "kritik_parametreler": {"temas_tolerans_pct": 0.50, "tarama_aralik_saat": 1},
    },
    "CVD Scanner": {
        "tip": "sinyal",
        "sembol": "Top 100 futures",
        "strateji": "CVD (10m/1H/24H) + OI artış + hacim patlaması. Skor ≥65 sinyal.",
        "sinyal_tipi": ["AKUMULASYON", "GUCLU_PUMP", "ZAYIF_PUMP", "DAGITIM"],
        "veriler": ["Binance klines", "aggTrades", "openInterestHist", "funding"],
        "zaman_dilimleri": ["10m"],
        "kritik_parametreler": {"min_skor": 65, "tarama_aralik_dk": 10},
    },
    "Asia Ekstrem": {
        "tip": "sinyal",
        "sembol": "Top 20 market cap",
        "strateji": "Asia Range (03:00-07:00 TR) fib ekstrem temas. BTC range ≥%1 günde aktif.",
        "sinyal_tipi": ["LONG", "SHORT"],
        "veriler": ["Binance futures 15m", "CoinGecko market cap"],
        "zaman_dilimleri": ["15m"],
        "kritik_parametreler": {
            "btc_min_range_pct": 1.0,
            "fib_long": [-1.272, -1.618],
            "fib_short": [2.272, 2.618],
            "aktif_saat": "07:00-23:00 TR"
        },
    },
    "Balina Bot": {
        "tip": "bilgi",
        "sembol": "BTCUSDT",
        "strateji": "Tek aggTrade ≥1000 BTC taker alış/satış tespiti.",
        "sinyal_tipi": ["ALIS", "SATIS"],
        "veriler": ["Binance Futures aggTrades"],
        "zaman_dilimleri": ["anlık"],
        "kritik_parametreler": {"esik_btc": 1000, "tarama_aralik_sn": 5},
    },
    "Volume Bot": {
        "tip": "sinyal",
        "sembol": "Top 100 futures",
        "strateji": "Hacim 1h ≥%5 + OI 1h ≥%5 + fiyat VWAP/MA20 üstü + whale long filtresi.",
        "sinyal_tipi": ["LONG"],  # tüm koşullar LONG yönlü
        "veriler": ["Binance 1h klines", "openInterestHist", "analiz_bot"],
        "zaman_dilimleri": ["1h"],
        "kritik_parametreler": {"hacim_min_pct": 5, "oi_min_pct": 5, "tarama_aralik_dk": 15},
    },
    "Korelasyon": {
        "tip": "bilgi",
        "sembol": "BTC + QQQ/DXY/GLD/TNX",
        "strateji": "14/30 günlük Pearson korelasyon + beta. Rejim: risk-on/risk-off.",
        "sinyal_tipi": ["RISK_ON", "RISK_OFF"],
        "veriler": ["Binance daily", "yfinance (QQQ/DXY/GLD/TNX)"],
        "zaman_dilimleri": ["1d"],
        "kritik_parametreler": {"pencereler": [14, 30], "gonderim_saati": "09:00 TR"},
    },
    "Whale Tracker": {
        "tip": "bilgi",
        "sembol": "BTC/ETH on-chain",
        "strateji": "BlackRock/MicroStrategy cüzdan hareketi + ETF akış takibi.",
        "sinyal_tipi": ["KURUMSAL_HAREKET"],
        "veriler": ["Blockchain.info", "Etherscan", "yfinance ETF"],
        "zaman_dilimleri": ["anlık"],
        "kritik_parametreler": {"min_btc": 50, "min_eth": 500},
    },
    "Makro Alarm": {
        "tip": "bilgi",
        "sembol": "BTC opsiyonları",
        "strateji": "CW/PW/ZG seviyeleri vade dilimlerine göre (0-7g/8-45g/45g+). 4 saatte güncellenir.",
        "sinyal_tipi": ["CW_TEMAS", "PW_TEMAS", "ZG_GECIS"],
        "veriler": ["Vercel API /alarm-levels", "Deribit"],
        "zaman_dilimleri": ["4h"],
        "kritik_parametreler": {"guncelleme_aralik_saat": 4},
    },
}


# ── Yardımcı Fonksiyonlar ──────────────────────────────────────────────────────
def _load(path, default):
    try:
        if Path(path).exists():
            return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _now():
    return datetime.now(timezone.utc).isoformat()


# ── Shared Memory ──────────────────────────────────────────────────────────────
def memory_yaz(kategori: str, anahtar: str, deger, kaynak: str = "sistem"):
    """Tüm agentların paylaştığı bilgi bankasına yaz."""
    mem = _load(MEMORY_FILE, {})
    if kategori not in mem:
        mem[kategori] = {}
    mem[kategori][anahtar] = {
        "deger": deger,
        "kaynak": kaynak,
        "tarih": _now()
    }
    _save(MEMORY_FILE, mem)

def memory_oku(kategori: str = None):
    """Shared memory'den oku."""
    mem = _load(MEMORY_FILE, {})
    if kategori:
        return mem.get(kategori, {})
    return mem


# ── Backtest Agent ─────────────────────────────────────────────────────────────
def backtest_sinyal_analizi() -> dict:
    """
    Tüm bot sinyallerini matematiksel olarak analiz et.
    """
    log = _load(SIGLOG_FILE, {"signals": []})
    # Eski format (liste) veya yeni format (dict) destekle
    if isinstance(log, list):
        sinyaller = log
    else:
        sinyaller = log.get("signals", [])

    # Sadece SİNYAL botları değerlendirilir — bilgi botlarının (Balina, Volume,
    # Korelasyon, Whale Tracker, Makro Alarm) win rate'i anlamsızdır
    BILGI_BOTLARI = {b for b, i in BOT_KATALOG.items() if i.get("tip") == "bilgi"}
    def _bilgi_botu_mu(bot_adi):
        if not bot_adi:
            return False
        return any(bb.lower() in bot_adi.lower() or bot_adi.lower() in bb.lower() for bb in BILGI_BOTLARI)

    degerlendirilmis = [s for s in sinyaller
                        if s.get("outcome") in ["WIN", "LOSS"] and not _bilgi_botu_mu(s.get("bot"))]

    if not degerlendirilmis:
        return {
            "durum": "sinyal_yok",
            "toplam_kayit": len(sinyaller),
            "degerlendirilmis": 0,
            "mesaj": f"Toplam {len(sinyaller)} sinyal var, henüz değerlendirilmiş yok."
        }

    bot_stats = {}
    saat_stats = {}
    sembol_stats = {}
    gun_stats = {}

    for s in degerlendirilmis:
        bot  = s.get("bot", "Bilinmeyen")
        win  = s.get("outcome") == "WIN"
        yon  = s.get("direction", "?")
        sym  = s.get("symbol", "?")

        # ── Bot istatistikleri
        if bot not in bot_stats:
            bot_stats[bot] = {
                "total": 0, "win": 0, "loss": 0, "win_rate": 0,
                "avg_change_pct": 0, "_changes": [],
                "LONG": {"w": 0, "l": 0}, "SHORT": {"w": 0, "l": 0},
                "son_5": [],
            }
        st = bot_stats[bot]
        st["total"] += 1
        if win:
            st["win"] += 1
        else:
            st["loss"] += 1
        if s.get("change_pct") is not None:
            st["_changes"].append(float(s["change_pct"]))
        if yon in ("LONG", "SHORT"):
            st[yon]["w" if win else "l"] += 1
        st["son_5"].append("W" if win else "L")
        st["son_5"] = st["son_5"][-5:]

        # ── Saat istatistikleri
        try:
            h = datetime.fromisoformat(s.get("time", s.get("logged_at", ""))).hour
            if h not in saat_stats:
                saat_stats[h] = {"win": 0, "loss": 0}
            saat_stats[h]["win" if win else "loss"] += 1
        except Exception:
            pass

        # ── Sembol istatistikleri
        if sym not in sembol_stats:
            sembol_stats[sym] = {"win": 0, "loss": 0}
        sembol_stats[sym]["win" if win else "loss"] += 1

        # ── Günlük
        try:
            gun = datetime.fromisoformat(s.get("time", s.get("logged_at", ""))).strftime("%Y-%m-%d")
            if gun not in gun_stats:
                gun_stats[gun] = {"win": 0, "loss": 0}
            gun_stats[gun]["win" if win else "loss"] += 1
        except Exception:
            pass

    # Win rate ve ortalama hesapla
    for bot, st in bot_stats.items():
        if st["total"] > 0:
            st["win_rate"] = round(st["win"] / st["total"] * 100, 1)
        if st["_changes"]:
            st["avg_change_pct"] = round(sum(st["_changes"]) / len(st["_changes"]), 2)
        del st["_changes"]

    # En iyi/kötü saatler
    saat_sirali = sorted(
        [(h, d["win"] + d["loss"],
          round(d["win"] / (d["win"] + d["loss"]) * 100, 1) if (d["win"] + d["loss"]) > 0 else 0)
         for h, d in saat_stats.items()],
        key=lambda x: x[2], reverse=True
    )

    # Sembol başarı sıralaması (min 3 sinyal)
    sembol_sirali = sorted(
        [(sym, d["win"] + d["loss"],
          round(d["win"] / (d["win"] + d["loss"]) * 100, 1) if (d["win"] + d["loss"]) > 0 else 0)
         for sym, d in sembol_stats.items() if (d["win"] + d["loss"]) >= 3],
        key=lambda x: x[2], reverse=True
    )

    # Genel win rate
    toplam_win  = sum(st["win"] for st in bot_stats.values())
    toplam_lose = sum(st["loss"] for st in bot_stats.values())
    toplam      = toplam_win + toplam_lose

    sonuc = {
        "tarih": _now(),
        "toplam_sinyal": len(sinyaller),
        "degerlendirilmis": len(degerlendirilmis),
        "genel_win_rate": round(toplam_win / toplam * 100, 1) if toplam else 0,
        "bot_stats": bot_stats,
        "en_iyi_saatler": [{"saat": h, "total": t, "win_rate": w} for h, t, w in saat_sirali[:5]],
        "en_kotu_saatler": [{"saat": h, "total": t, "win_rate": w} for h, t, w in saat_sirali[-3:]],
        "sembol_sirali": [{"sembol": s, "total": t, "win_rate": w} for s, t, w in sembol_sirali[:5]],
        "gunluk_son7": {g: v for g, v in sorted(gun_stats.items())[-7:]},
    }

    # Shared memory'ye kaydet
    memory_yaz("backtest", "son_analiz", sonuc, kaynak="BacktestAgent")

    return sonuc


# ── Research Agent ─────────────────────────────────────────────────────────────
def research_analizi() -> dict:
    """
    Matematiksel/bilimsel pattern analizi.
    Backtest verisiyle + bot stratejileriyle karşılaştırır.
    Hipotezler üretir ve görev kuyruğuna ekler.
    """
    backtest = backtest_sinyal_analizi()
    bot_stats = backtest.get("bot_stats", {})

    bulgular = []
    hipotezler = []
    oneriler = []

    if not bot_stats:
        return {
            "durum": "yetersiz_veri",
            "mesaj": "Değerlendirilmiş sinyal yok. Research için en az 10 sinyal gerekli.",
            "hipotezler": [], "bulgular": [], "oneriler": []
        }

    # ── 1. YÖN ANALİZİ (LONG vs SHORT başarısı)
    for bot, st in bot_stats.items():
        long_w = st.get("LONG", {}).get("w", 0)
        long_l = st.get("LONG", {}).get("l", 0)
        short_w = st.get("SHORT", {}).get("w", 0)
        short_l = st.get("SHORT", {}).get("l", 0)
        long_tot  = long_w + long_l
        short_tot = short_w + short_l
        if long_tot >= 3 and short_tot >= 3:
            lwr = long_w / long_tot * 100
            swr = short_w / short_tot * 100
            fark = abs(lwr - swr)
            if fark > 20:
                guclu = "LONG" if lwr > swr else "SHORT"
                zayif = "SHORT" if lwr > swr else "LONG"
                bulgular.append({
                    "bot": bot,
                    "bulgu": f"{guclu} yönü %{max(lwr,swr):.0f} başarılı vs {zayif} %{min(lwr,swr):.0f}",
                    "etki": "yuksek",
                    "oneri": f"{bot} için {zayif} sinyallerini filtrele veya eşikleri artır"
                })
                hipotezler.append({
                    "bot": bot,
                    "hipotez": f"{bot} {zayif} sinyallerinde ek filtre eklenmeli",
                    "test_yontemi": f"{zayif} sinyallerinde CVD yön teyidi + OI artışı koşulu ekle",
                    "beklenen_iyilesme": f"%{fark:.0f} başarı artışı"
                })

    # ── 2. SAAT DİLİMİ ANALİZİ
    saat_data = backtest.get("en_iyi_saatler", [])
    kotu_saat = backtest.get("en_kotu_saatler", [])
    if saat_data and kotu_saat:
        en_iyi = saat_data[0]
        en_kotu = kotu_saat[-1]
        if en_iyi["win_rate"] - en_kotu["win_rate"] > 25:
            bulgular.append({
                "bot": "TÜM BOTLAR",
                "bulgu": f"{en_iyi['saat']}:00 UTC en başarılı saat (%{en_iyi['win_rate']}), {en_kotu['saat']}:00 UTC en kötü (%{en_kotu['win_rate']})",
                "etki": "orta",
                "oneri": f"{en_kotu['saat']}:00 UTC civarında sinyalleri filtrele"
            })
            hipotezler.append({
                "bot": "TÜM BOTLAR",
                "hipotez": f"Saat {en_kotu['saat']}:00-{(en_kotu['saat']+2)%24}:00 UTC arası sinyaller başarısız",
                "test_yontemi": "Bu saatteki sinyalleri tarihi veriye göre backtest et",
                "beklenen_iyilesme": "Düşük performanslı saatler filtrelenince genel win rate artar"
            })

    # ── 3. ÇOKLU BOT TEYİT ANALİZİ
    log = _load(SIGLOG_FILE, {"signals": []})
    sinyaller = log.get("signals", []) if isinstance(log, dict) else log
    degerlendirilmis = [s for s in sinyaller if s.get("outcome") in ["WIN", "LOSS"]]

    teyitli_win = teyitli_tot = 0
    try:
        sirali = sorted(degerlendirilmis, key=lambda x: x.get("time", x.get("logged_at", "")))
        for i, s1 in enumerate(sirali):
            for s2 in sirali[i+1:]:
                try:
                    t1 = datetime.fromisoformat(s1.get("time", s1.get("logged_at", "")))
                    t2 = datetime.fromisoformat(s2.get("time", s2.get("logged_at", "")))
                    if (t2 - t1).total_seconds() > 7200:
                        break
                    if (s1.get("symbol") == s2.get("symbol") and
                            s1.get("direction") == s2.get("direction") and
                            s1.get("bot") != s2.get("bot")):
                        teyitli_tot += 1
                        if s1["outcome"] == "WIN" and s2["outcome"] == "WIN":
                            teyitli_win += 1
                except Exception:
                    pass
    except Exception:
        pass

    if teyitli_tot >= 3:
        twr = teyitli_win / teyitli_tot * 100
        bulgular.append({
            "bot": "KOMBINASYON",
            "bulgu": f"2+ bot aynı sembol+yön 2 saat içinde: %{twr:.0f} win rate ({teyitli_tot} kez)",
            "etki": "yuksek",
            "oneri": "Çoklu bot teyidi olan sinyallere öncelik ver"
        })
        hipotezler.append({
            "bot": "KOMBINASYON",
            "hipotez": "Asia Ekstrem + CVD Scanner aynı anda sinyal verirse başarı artar",
            "test_yontemi": "Her iki botun aynı coin/yönde ±2 saat içinde sinyal verdiği durumları filtrele",
            "beklenen_iyilesme": f"Mevcut %{twr:.0f} teyit oranı → daha yüksek güven skoru"
        })

    # ── 4. BOT SAĞLIK ANALİZİ
    for bot, st in bot_stats.items():
        if st["total"] >= 10 and st["win_rate"] < 40:
            katalog = BOT_KATALOG.get(bot, {})
            bulgular.append({
                "bot": bot,
                "bulgu": f"Düşük performans: %{st['win_rate']} win rate ({st['total']} sinyal)",
                "etki": "kritik",
                "oneri": f"Parametre revizyonu gerekli. Strateji: {katalog.get('strateji', '?')[:80]}"
            })
            hipotezler.append({
                "bot": bot,
                "hipotez": f"{bot} sinyal eşikleri çok düşük veya yanlış piyasa koşulunda çalışıyor",
                "test_yontemi": "Son 30 günlük başarısız sinyallerin ortak özelliklerini incele",
                "beklenen_iyilesme": "Eşik artışı ile false signal azalır"
            })

    # ── Geliştirme önerileri — YALNIZ ölçülen veriden türetilir.
    # (Eskiden 3 adet sabit/hardcoded "feature wishlist" tavsiyesi vardı; veriyle
    #  ilgisiz oldukları için kaldırıldı. Artık her öneri bir istatistiğe dayanır.)
    if bot_stats:
        en_iyi_bot = max(bot_stats, key=lambda b: bot_stats[b]["win_rate"])
        eb = bot_stats[en_iyi_bot]
        oneriler.append(
            f"En güvenilir bot: {en_iyi_bot} (%{eb['win_rate']} WR, n={eb['total']}) — "
            f"{'istatistiksel anlamlı, ağırlık artır' if eb['total'] >= 30 else 'ÖRNEKLEM AZ (n<30), temkinli'}")

        # İstatistiksel yetersiz örneklem (küçük n → WR güveni düşük)
        zayif = [f"{b} (n={s['total']})" for b, s in bot_stats.items() if s["total"] < 20]
        if zayif:
            oneriler.append("Yetersiz örneklem (n<20), WR güvenilmez — daha çok veri: " + ", ".join(zayif[:6]))

        # Yüksek WR + yeterli örneklem → istatistiksel anlamlı edge
        for b, s in bot_stats.items():
            if s["total"] >= 30 and s["win_rate"] >= 60:
                oneriler.append(f"Anlamlı edge: {b} (%{s['win_rate']} WR, n={s['total']}) — canlı ağırlık yükseltilebilir")
    else:
        oneriler.append("Yeterli kapanmış sinyal yok — istatistiksel öneri için veri toplanmalı.")

    sonuc = {
        "tarih": _now(),
        "bulgular": bulgular,
        "hipotezler": hipotezler,
        "oneriler": oneriler,
        "bot_katalog_ozeti": {
            bot: {"strateji_ozet": info["strateji"][:60], "sinyal_tipi": info["sinyal_tipi"]}
            for bot, info in BOT_KATALOG.items()
        }
    }

    # Shared memory'ye kaydet
    memory_yaz("research", "son_analiz", sonuc, kaynak="ResearchAgent")

    # Görev kuyruğuna hipotezleri ekle
    tasks = _load(TASKS_FILE, {"tasks": [], "tamamlanan": []})
    for h in hipotezler:
        # Aynı hipotez zaten varsa ekleme
        varmi = any(t.get("hipotez") == h["hipotez"] for t in tasks["tasks"])
        if not varmi:
            tasks["tasks"].append({
                "id": f"task_{len(tasks['tasks'])+1}",
                "tarih": _now(),
                "durum": "bekliyor",
                **h
            })
    _save(TASKS_FILE, tasks)

    return sonuc


# ── Bot Sağlık Kontrolü ────────────────────────────────────────────────────────
async def bot_saglik_kontrol() -> dict:
    """Her botun Render endpoint'ini kontrol et."""
    bot_url = os.environ.get("BOT_URL", "")
    saglik = {}

    endpoints = ["/", "/signals"]
    async with httpx.AsyncClient(timeout=10) as cl:
        for ep in endpoints:
            try:
                r = await cl.get(f"{bot_url}{ep}")
                saglik[ep] = {
                    "durum": "aktif" if r.status_code == 200 else "hata",
                    "status_code": r.status_code,
                    "tarih": _now()
                }
            except Exception as e:
                saglik[ep] = {"durum": "erisilemez", "hata": str(e)[:80], "tarih": _now()}

    # Sinyal sayısını da ekle
    log = _load(SIGLOG_FILE, {"signals": []})
    sinyaller = log.get("signals", []) if isinstance(log, dict) else log
    saglik["sinyal_istatistik"] = {
        "toplam": len(sinyaller),
        "son_24h": sum(1 for s in sinyaller if _son_24h_mi(s.get("time", s.get("logged_at", "")))),
        "degerlendirilmis": sum(1 for s in sinyaller if s.get("outcome") in ["WIN", "LOSS"])
    }

    _save(HEALTH_FILE, saglik)
    memory_yaz("saglik", "bot_durumu", saglik, kaynak="LiderAgent")
    return saglik


def _son_24h_mi(tarih_str: str) -> bool:
    try:
        t = datetime.fromisoformat(tarih_str)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() < 86400
    except Exception:
        return False


# ── AI Yorumu ──────────────────────────────────────────────────────────────────
async def ai_yorum_uret(backtest: dict, research: dict, saglik: dict, api_key: str, soru: str = "") -> str:
    """Tüm verileri Gemini ile yorumla. Opsiyonel: kullanıcı sorusu."""
    if not api_key:
        return "AI yorum için GEMINI_API_KEY gerekli."

    shared_mem = memory_oku()

    if soru:
        prompt = f"""Sen OAR Premium'un Lider Agent'ısın. Aşağıdaki veriler mevcut:

BACKTEST: {json.dumps(backtest, ensure_ascii=False)[:1500]}
RESEARCH: {json.dumps(research, ensure_ascii=False)[:1000]}
SHARED MEMORY: {json.dumps(shared_mem, ensure_ascii=False)[:500]}

Kullanıcı sorusu: {soru}

Kesin rakamlarla, matematiksel ve bilimsel cevap ver. Türkçe."""
    else:
        # Deribit + OAR backtest bağlamı
        try:
            deribit_ctx = await _deribit_ozet()
        except Exception:
            deribit_ctx = "Deribit verisi alınamadı"
        oar_bt_ctx   = _oar_backtest_ozet()
        oar_kural_ctx = _oar_kural_baglami()

        prompt = f"""Sen OAR Premium'un Lider Agent'ısın. Sabah raporunu oluştur.

BOT KATALOĞU:
{json.dumps({b: i["strateji"] for b,i in BOT_KATALOG.items()}, ensure_ascii=False)}

BACKTEST ANALİZİ (bot sinyalleri):
{json.dumps(backtest, ensure_ascii=False)[:1500]}

OAR TAM BACKTEST (Asia Range Fib sistemi):
{oar_bt_ctx}

OAR KURAL BANKASI (Kullanıcı tanımlı — kesinlikle uy):
{oar_kural_ctx}

DERİBİT OPSİYON BAĞLAMI:
{deribit_ctx}

RESEARCH BULGULARI:
{json.dumps(research, ensure_ascii=False)[:1000]}

GÖREV KUYRUĞU:
{json.dumps(_load(TASKS_FILE, {}), ensure_ascii=False)[:400]}

Şunları içeren kısa sabah raporu yaz (Türkçe, madde madde):
1. Genel performans özeti (WR, en iyi bot, OAR backtest Sharpe)
2. Deribit opsiyon bağlamı (GEX rejimi + Call/Put Wall + DVOL yorumu)
3. Research Agent'tan en kritik 2 bulgu
4. Bugün OAR sisteminde odaklanılacak 1 Fib seviyesi / hipotez
5. Herhangi bir bot veya açık pozisyon için acil uyarı varsa belirt

Kesinlikle tahmin yapma, yalnızca verideki gerçekleri yorumla. Rakamlarla konuş."""

    try:
        url = f"{GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent?key={api_key}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 800}
        }
        async with httpx.AsyncClient(timeout=30) as cl:
            r = await cl.post(url, json=payload)
            if r.status_code == 200:
                data = r.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            # Fallback: Groq
            groq_key = os.environ.get("GROQ_API_KEY", "")
            if groq_key:
                gr = await cl.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}"},
                    json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 800}
                )
                if gr.status_code == 200:
                    return gr.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"AI yorum alınamadı: {str(e)[:100]}"
    return "AI yorum alınamadı."


# ── Ana Rapor ──────────────────────────────────────────────────────────────────
async def rapor_uret(api_key: str = "", soru: str = "") -> dict:
    backtest = backtest_sinyal_analizi()
    research = research_analizi()
    saglik   = await bot_saglik_kontrol()
    ai_yorum = await ai_yorum_uret(backtest, research, saglik, api_key, soru)

    rapor = {
        "tarih": _now(),
        "backtest": backtest,
        "research": research,
        "saglik": saglik,
        "ai_yorum": ai_yorum,
        "ozet": _ozet_uret(backtest, research),
        "gorevler": _load(TASKS_FILE, {"tasks": [], "tamamlanan": []}),
    }

    _save(REPORT_FILE, rapor)
    return rapor


def _ozet_uret(backtest: dict, research: dict) -> dict:
    bot_stats = backtest.get("bot_stats", {})
    toplam    = backtest.get("degerlendirilmis", 0)
    genel_wr  = backtest.get("genel_win_rate", 0)
    en_iyi    = max(bot_stats, key=lambda b: bot_stats[b]["win_rate"]) if bot_stats else None

    return {
        "toplam_sinyal":   backtest.get("toplam_sinyal", 0),
        "degerlendirilmis": toplam,
        "genel_win_rate":  genel_wr,
        "en_iyi_bot":      en_iyi,
        "en_iyi_wr":       bot_stats[en_iyi]["win_rate"] if en_iyi else 0,
        "kritik_uyari":    next((b["bulgu"] for b in research.get("bulgular", []) if b.get("etki") == "kritik"), None),
        "oneriler":        research.get("oneriler", [])[:3],
        "bekleyen_gorev":  len(_load(TASKS_FILE, {"tasks": []}).get("tasks", [])),
    }


def son_rapor() -> dict:
    return _load(REPORT_FILE, {"durum": "henuz_rapor_yok"})

def son_pattern() -> dict:
    return _load(PATTERN_FILE, {"durum": "henuz_analiz_yok"})

def bot_katalog_al() -> dict:
    return BOT_KATALOG


# ── CIO Karar Motoru ───────────────────────────────────────────────────────────
async def lider_karar_uret(sembol: str = "BTCUSDT", api_key: str = "") -> dict:
    """
    Tüm agentları çalıştırarak LONG / SHORT / NO_TRADE kararı üretir.
    Bu fonksiyon pasif rapor yazmaz — yalnızca kanıta dayalı karar verir.
    """
    from confidence_engine import confidence_karar, karar_ozet_metni

    karar_verisi = await confidence_karar(sembol)
    ozet = karar_ozet_metni(karar_verisi)

    # AI ile karar gerekçelendirmesi (opsiyonel)
    ai_aciklama = ""
    if api_key:
        try:
            agent_ozet = "\n".join(
                f"{ad.upper()}: skor={v['skor']:+.0f} yon={v['yon']} → {v['aciklama'][:100]}"
                for ad, v in karar_verisi["agent_skorlar"].items()
            )
            zaman = karar_verisi["zaman_riski"]
            prompt = f"""Sen OAR Premium'un CIO'sun (Chief Investment Officer).
Görevin yalnızca LONG / SHORT / NO_TRADE kararı vermek ve gerekçeyi açıklamak.
Özet yapma. Indikatör tanımı yapma. YALNIZCA karar ve kanıt.

SEMBOL: {sembol}
KARAR: {karar_verisi['karar']}  (Konfidans: {karar_verisi['konfidans']}/100)
AĞIRLIKLI SKOR: {karar_verisi['ham_skor']:+.1f}

AGENT VERİLERİ:
{agent_ozet}

ZAMAN RİSKİ: {zaman['seviye']} ({zaman['risk_skoru']}/100)
{chr(10).join('• ' + e['aciklama'] for e in zaman.get('aktif_etkinlikler', [])[:3])}

OAR SETUP'LAR: {', '.join(karar_verisi.get('agent_skorlar', {}).get('oar', {}).get('aciklama', '').split(' | ')[:3])}

Maksimum 5 madde. Türkçe. Rakamları kullan. "sanırım" yazma."""

            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            payload = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 600}
            }
            async with httpx.AsyncClient(timeout=25) as cl:
                r = await cl.post(url, json=payload)
                if r.status_code == 200:
                    ai_aciklama = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            ai_aciklama = f"AI açıklama alınamadı: {str(e)[:60]}"

    sonuc = {**karar_verisi, "ozet_metin": ozet, "ai_aciklama": ai_aciklama}

    # Kararı memory'e yaz (diğer agentlar okuyabilsin)
    memory_yaz("karar", f"son_karar_{sembol}", {
        "karar": karar_verisi["karar"],
        "konfidans": karar_verisi["konfidans"],
        "tarih": karar_verisi["tarih"]
    }, kaynak="CIO_Engine")

    # Kararı SQLite'a arşivle + paper trade tetikle (forward test)
    try:
        import persistence as _db
        from paper_trade_agent import karardan_trade_ac
        karar_id = _db.karar_kaydet(karar_verisi)
        trade_sonuc = await karardan_trade_ac(karar_verisi, karar_id)
        sonuc["paper_trade"] = trade_sonuc
    except Exception as e:
        sonuc["paper_trade"] = {"acildi": False, "neden": f"hata: {str(e)[:80]}"}

    return sonuc


# ── Sinyal Toplayıcı — bot servisinden sinyalleri çek, OAR diskine yaz ─────────
async def sinyal_toplayici_loop():
    """
    Her 5 dakikada bir bot servisinin /signals endpoint'inden sinyalleri çeker,
    OAR'ın kendi diskindeki SIGLOG_FILE'a merge eder.
    İki servis ayrı disklerde olduğu için bu köprü zorunlu.
    """
    bot_url = os.environ.get("BOT_URL", "").rstrip("/")
    if not bot_url:
        print("[SinyalToplayici] BOT_URL tanımlı değil — sinyal köprüsü pasif. "
              "Railway'de Sinyal-Bot servisinin URL'ini BOT_URL değişkenine ekle.")
        return
    await asyncio.sleep(45)   # RAM serbest — sinyalleri erken çek
    while True:
        try:
            async with httpx.AsyncClient(timeout=15) as cl:
                r = await cl.get(f"{bot_url}/signals?limit=200")
                if r.status_code == 200:
                    gelen = r.json().get("signals", [])
                    if gelen:
                        mevcut = _load(SIGLOG_FILE, {"signals": []})
                        sinyaller = mevcut.get("signals", []) if isinstance(mevcut, dict) else mevcut

                        # Merge: bot+symbol+time anahtar — tekrar ekleme
                        mevcut_keys = {(s.get("bot"), s.get("symbol"), s.get("time")) for s in sinyaller}
                        yeni_sayisi = 0
                        for g in gelen:
                            key = (g.get("bot"), g.get("symbol"), g.get("time"))
                            if key not in mevcut_keys:
                                sinyaller.append(g)
                                mevcut_keys.add(key)
                                yeni_sayisi += 1

                        sinyaller = sinyaller[-300:]  # son 300 sinyal tut (512MB limit)
                        _save(SIGLOG_FILE, {"signals": sinyaller})
                        if yeni_sayisi:
                            print(f"[SinyalToplayici] +{yeni_sayisi} yeni sinyal (toplam {len(sinyaller)})")
        except Exception as e:
            print(f"[SinyalToplayici] Hata: {str(e)[:80]}")
        await asyncio.sleep(300)  # 5 dakika


# ── Sinyal Değerlendirici — WIN/LOSS işaretle ──────────────────────────────────
DEGERLENDIRME_SAAT  = 4      # sinyalden 4 saat sonra değerlendir
WIN_ESIK_PCT        = 0.5    # yönünde %0.5+ hareket = WIN

async def _binance_fiyat(symbol: str, ts_iso: str = None) -> float:
    """Anlık veya geçmiş fiyat (1m kline ile)."""
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            if ts_iso:
                # Geçmiş fiyat: o zamana en yakın 1m kline
                t = datetime.fromisoformat(ts_iso)
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                ms = int(t.timestamp() * 1000)
                r = await cl.get("https://api.binance.com/api/v3/klines",
                                 params={"symbol": symbol, "interval": "1m",
                                         "startTime": ms, "limit": 1})
                d = r.json()
                if isinstance(d, list) and d:
                    return float(d[0][4])
            r = await cl.get("https://api.binance.com/api/v3/ticker/price",
                             params={"symbol": symbol})
            return float(r.json()["price"])
    except Exception:
        return 0.0


async def sinyal_degerlendirici_loop():
    """
    outcome=None olan sinyalleri DEGERLENDIRME_SAAT geçince değerlendirir:
      LONG  → fiyat %WIN_ESIK üstüne çıktıysa WIN, altına indiyse LOSS
      SHORT → tersine
      Diğer tipler (AKUMULASYON, HACIM_PATLAMA vs) → LONG gibi değerlendirilir
    """
    await asyncio.sleep(60)
    while True:
        try:
            mevcut = _load(SIGLOG_FILE, {"signals": []})
            sinyaller = mevcut.get("signals", []) if isinstance(mevcut, dict) else mevcut
            degisti = False
            now = datetime.now(timezone.utc)

            for s in sinyaller:
                if s.get("outcome") is not None:
                    continue
                t_str = s.get("time", s.get("logged_at", ""))
                try:
                    t = datetime.fromisoformat(t_str)
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                gecen_saat = (now - t).total_seconds() / 3600
                if gecen_saat < DEGERLENDIRME_SAAT:
                    continue

                sym = (s.get("symbol") or "BTCUSDT").upper().replace(".P", "")
                if not sym.endswith("USDT"):
                    sym += "USDT"
                giris = float(s.get("price") or 0)
                if giris <= 0:
                    giris = await _binance_fiyat(sym, t_str)
                if giris <= 0:
                    s["outcome"] = "SKIP"
                    degisti = True
                    continue

                guncel = await _binance_fiyat(sym)
                if guncel <= 0:
                    continue

                degisim_pct = (guncel - giris) / giris * 100
                yon = (s.get("direction") or "LONG").upper()
                if yon in ("SHORT", "SATIS"):
                    degisim_pct = -degisim_pct

                if degisim_pct >= WIN_ESIK_PCT:
                    s["outcome"] = "WIN"
                elif degisim_pct <= -WIN_ESIK_PCT:
                    s["outcome"] = "LOSS"
                else:
                    s["outcome"] = "FLAT"
                s["change_pct"] = round(degisim_pct, 2)
                s["evaluated_at"] = _now()
                degisti = True
                await asyncio.sleep(0.3)  # rate limit

            if degisti:
                _save(SIGLOG_FILE, {"signals": sinyaller})
                won  = sum(1 for x in sinyaller if x.get("outcome") == "WIN")
                lost = sum(1 for x in sinyaller if x.get("outcome") == "LOSS")
                print(f"[Degerlendirici] Güncel durum: {won}W / {lost}L")
        except Exception as e:
            print(f"[Degerlendirici] Hata: {str(e)[:80]}")
        await asyncio.sleep(600)  # 10 dakika


# ── Otomatik Sabah Raporu ──────────────────────────────────────────────────────
async def sabah_raporu_loop(api_key: str = ""):
    while True:
        try:
            now    = datetime.now(timezone.utc)
            hedef  = now.replace(hour=7, minute=0, second=0, microsecond=0)
            if now >= hedef:
                hedef += timedelta(days=1)
            await asyncio.sleep((hedef - now).total_seconds())
            print("[LiderAgent] Sabah raporu üretiliyor...")
            await rapor_uret(api_key)
            print("[LiderAgent] ✅ Sabah raporu tamamlandı")
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[LiderAgent] Rapor hatası: {e}")
            await asyncio.sleep(3600)


# ═══════════════════════════════════════════════════════════════════
# SAATLİK RAPOR SİSTEMİ — rapor geçmişi SİLİNMEZ
# ═══════════════════════════════════════════════════════════════════
RAPOR_GECMISI_FILE = DATA_DIR / "rapor_gecmisi.json"   # tüm saatlik raporlar
KOMBO_SINYAL_FILE  = DATA_DIR / "kombo_sinyaller.json" # OAR'ın kendi ürettiği sinyaller

async def _hizli_ai(prompt: str, max_tok: int = 600) -> str:
    """Agent'ların saatlik düşünmesi için kompakt AI çağrısı."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return ""
    try:
        url = f"{GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent?key={api_key}"
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                   "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tok}}
        async with httpx.AsyncClient(timeout=30) as cl:
            r = await cl.post(url, json=payload)
            if r.status_code == 200:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            gk = os.environ.get("GROQ_API_KEY", "")
            if gk:
                gr = await cl.post("https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {gk}"},
                    json={"model": "llama-3.3-70b-versatile",
                          "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tok})
                if gr.status_code == 200:
                    return gr.json()["choices"][0]["message"]["content"]
    except Exception:
        pass
    return ""


def rapor_gecmisi_ekle(tip: str, icerik: dict):
    """Rapor geçmişine ekle — ASLA silinmez, sadece eklenir."""
    gecmis = _load(RAPOR_GECMISI_FILE, {"raporlar": []})
    gecmis["raporlar"].append({
        "tip": tip,           # "lider" | "backtest" | "research"
        "tarih": _now(),
        "icerik": icerik
    })
    # Canlı dosyayı KÜÇÜK tut (512MB instance — her _load tüm dosyayı RAM'e parse eder).
    # Eski raporlar silinmez, ayrı arşiv dosyasına taşınır.
    if len(gecmis["raporlar"]) > 500:
        arsiv_file = DATA_DIR / "rapor_arsiv.json"
        arsiv = _load(arsiv_file, {"raporlar": []})
        tasinacak = len(gecmis["raporlar"]) - 300   # canlıda son 300 kalsın
        arsiv["raporlar"].extend(gecmis["raporlar"][:tasinacak])
        _save(arsiv_file, arsiv)
        gecmis["raporlar"] = gecmis["raporlar"][tasinacak:]
        del arsiv
    _save(RAPOR_GECMISI_FILE, gecmis)
    del gecmis
    import gc; gc.collect()

def rapor_gecmisi_al(tip: str = None, limit: int = 24) -> list:
    gecmis = _load(RAPOR_GECMISI_FILE, {"raporlar": []})
    raporlar = gecmis["raporlar"]
    if tip:
        raporlar = [r for r in raporlar if r["tip"] == tip]
    return raporlar[-limit:]


# ── Sistem Denetimi (GitHub + Render + Vercel) ─────────────────────────────────
async def sistem_denetimi() -> dict:
    """Tüm dış bağlantıları tek tek kontrol et."""
    sonuc = {"tarih": _now(), "servisler": {}}
    
    kontroller = [
        ("render_bot",     os.environ.get("BOT_URL", "") + "/"),
        ("render_bot_signals", os.environ.get("BOT_URL", "") + "/signals?limit=1"),
        ("vercel_levels",  None),  # config'den okunacak
        ("vercel_macro",   None),
        ("binance",        "https://api.binance.com/api/v3/ping"),
        ("deribit",        "https://www.deribit.com/api/v2/public/test"),
    ]
    
    # Vercel URL'ini config'den al
    cfg_file = DATA_DIR / "config.json"
    cfg = _load(cfg_file, {})
    vercel = cfg.get("vercel_url", os.environ.get("VERCEL_URL", "https://project-vtcqr.vercel.app"))
    
    kontroller[2] = ("vercel_levels", f"{vercel}/api/alarm-levels")
    kontroller[3] = ("vercel_macro",  f"{vercel}/api/macro")
    
    async with httpx.AsyncClient(timeout=12) as cl:
        for ad, url in kontroller:
            try:
                r = await cl.get(url)
                sonuc["servisler"][ad] = {
                    "durum": "ok" if r.status_code == 200 else "hata",
                    "kod": r.status_code
                }
            except Exception as e:
                sonuc["servisler"][ad] = {"durum": "erisilemez", "hata": str(e)[:60]}
    
    aktif = sum(1 for s in sonuc["servisler"].values() if s["durum"] == "ok")
    sonuc["ozet"] = f"{aktif}/{len(kontroller)} servis aktif"
    return sonuc


# ── Çakışan Bot Analizi ────────────────────────────────────────────────────────
def cakisan_bot_analizi() -> list:
    """Aynı sembol + zıt yön sinyali veren botları bul (±2 saat)."""
    log = _load(SIGLOG_FILE, {"signals": []})
    sinyaller = log.get("signals", []) if isinstance(log, dict) else log
    cakismalar = []
    
    sirali = sorted(sinyaller, key=lambda x: x.get("time", ""))
    for i, s1 in enumerate(sirali):
        for s2 in sirali[i+1:]:
            try:
                t1 = datetime.fromisoformat(s1.get("time", "").replace(" ", "T"))
                t2 = datetime.fromisoformat(s2.get("time", "").replace(" ", "T"))
                if (t2 - t1).total_seconds() > 7200:
                    break
                d1 = (s1.get("direction") or "").upper()
                d2 = (s2.get("direction") or "").upper()
                zit = (d1 in ("LONG","ALIS") and d2 in ("SHORT","SATIS")) or \
                      (d1 in ("SHORT","SATIS") and d2 in ("LONG","ALIS"))
                if s1.get("symbol") == s2.get("symbol") and zit and s1.get("bot") != s2.get("bot"):
                    cakismalar.append({
                        "sembol": s1.get("symbol"),
                        "bot1": s1.get("bot"), "yon1": d1,
                        "bot2": s2.get("bot"), "yon2": d2,
                        "zaman": s1.get("time")
                    })
            except Exception:
                pass
    return cakismalar[-10:]


# ── AKILLI KOMBO SİNYAL MOTORU (Backtest Agent'ın öğrenen kısmı) ──────────────
async def kombo_sinyal_tara() -> list:
    """
    Canlı verilerden kombo pattern tara:
      1. CVD + OI birlikte yükseliyor → LONG kombo sinyali
      2. CVD düşüyor + OI yükseliyor → DAGITIM uyarısı (SHORT)
      3. Funding negatif + fiyat yatay + OI artıyor → squeeze hazırlığı
    Sinyaller KOMBO_SINYAL_FILE'a kaydedilir ve değerlendirici WIN/LOSS işaretler.
    """
    yeni_sinyaller = []
    semboller = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    
    async with httpx.AsyncClient(timeout=15) as cl:
        for sym in semboller:
            try:
                # CVD: son 12 x 5m mum (1 saat)
                kr = await cl.get("https://fapi.binance.com/fapi/v1/klines",
                                  params={"symbol": sym, "interval": "5m", "limit": 12})
                klines = kr.json()
                if not isinstance(klines, list) or len(klines) < 12:
                    continue
                
                cvd = 0
                cvd_seri = []
                for k in klines:
                    vol = float(k[5]); tbv = float(k[9])
                    cvd += tbv - (vol - tbv)
                    cvd_seri.append(cvd)
                cvd_egim = cvd_seri[-1] - cvd_seri[-6]  # son 30dk eğim
                
                # OI: son 2 x 30dk
                oir = await cl.get("https://fapi.binance.com/futures/data/openInterestHist",
                                   params={"symbol": sym, "period": "30m", "limit": 2})
                oid = oir.json()
                if not isinstance(oid, list) or len(oid) < 2:
                    continue
                oi_onceki = float(oid[0]["sumOpenInterestValue"])
                oi_simdi  = float(oid[1]["sumOpenInterestValue"])
                oi_degisim_pct = (oi_simdi - oi_onceki) / oi_onceki * 100 if oi_onceki else 0
                
                # Funding
                fr = await cl.get("https://fapi.binance.com/fapi/v1/premiumIndex",
                                  params={"symbol": sym})
                funding = float(fr.json().get("lastFundingRate", 0)) * 100
                
                fiyat = float(klines[-1][4])
                
                # ── BİLGİ BOTU TEYİDİ — akıllı bağlam ──────────────────
                # Son 2 saatte Balina ALIS bildirimi var mı? (BTC için)
                balina_alis = balina_satis = False
                korelasyon_riskoff = False
                try:
                    ana_log = _load(SIGLOG_FILE, {"signals": []})
                    ana_s = ana_log.get("signals", []) if isinstance(ana_log, dict) else ana_log
                    simdi_dt = datetime.now(timezone.utc)
                    for es in ana_s[-100:]:
                        bot_adi = (es.get("bot") or "").lower()
                        try:
                            et = datetime.fromisoformat(es.get("time", "").replace(" ", "T"))
                            if et.tzinfo is None:
                                et = et.replace(tzinfo=timezone.utc)
                            if (simdi_dt - et).total_seconds() > 7200:
                                continue
                        except Exception:
                            continue
                        if "balina" in bot_adi and sym == "BTCUSDT":
                            d = (es.get("direction") or "").upper()
                            if d in ("ALIS", "LONG"): balina_alis = True
                            if d in ("SATIS", "SHORT"): balina_satis = True
                        if "korelasyon" in bot_adi and "RISK_OFF" in str(es.get("detail", "")).upper():
                            korelasyon_riskoff = True
                except Exception:
                    pass

                # ── PATTERN 1: CVD + OI birlikte yükseliyor → LONG
                if cvd_egim > 0 and oi_degisim_pct > 0.5:
                    guven = "NORMAL"
                    teyitler = []
                    if balina_alis:
                        guven = "YUKSEK"
                        teyitler.append("Balina ALIS teyidi")
                    if korelasyon_riskoff:
                        guven = "DUSUK"
                        teyitler.append("⚠ Risk-off rejim (dikkat)")
                    yeni_sinyaller.append({
                        "bot": "OAR Kombo",
                        "symbol": sym,
                        "direction": "LONG",
                        "price": fiyat,
                        "detail": f"CVD↑ + OI +%{oi_degisim_pct:.1f}" + (f" | {' · '.join(teyitler)}" if teyitler else ""),
                        "pattern": "CVD_OI_YUKSELIS",
                        "guven": guven,
                        "time": _now(),
                        "outcome": None
                    })
                
                # ── PATTERN 2: CVD düşüyor + OI yükseliyor → DAGITIM (SHORT)
                elif cvd_egim < 0 and oi_degisim_pct > 0.5:
                    yeni_sinyaller.append({
                        "bot": "OAR Kombo",
                        "symbol": sym,
                        "direction": "SHORT",
                        "price": fiyat,
                        "detail": f"CVD↓ + OI +%{oi_degisim_pct:.1f} (dağıtım: short pozisyon birikiyor)",
                        "pattern": "DAGITIM",
                        "time": _now(),
                        "outcome": None
                    })
                
                # ── PATTERN 3: Funding negatif + OI artıyor → short squeeze hazırlığı
                if funding < -0.01 and oi_degisim_pct > 1.0:
                    yeni_sinyaller.append({
                        "bot": "OAR Kombo",
                        "symbol": sym,
                        "direction": "LONG",
                        "price": fiyat,
                        "detail": f"Funding %{funding:.3f} + OI +%{oi_degisim_pct:.1f} (short squeeze riski)",
                        "pattern": "SQUEEZE_HAZIRLIK",
                        "time": _now(),
                        "outcome": None
                    })
                
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"[Kombo] {sym} hata: {str(e)[:60]}")
    
    # Dedup: aynı sembol+pattern son 1 saatte verilmişse tekrar verme
    if yeni_sinyaller:
        mevcut = _load(KOMBO_SINYAL_FILE, {"signals": []})
        eski = mevcut.get("signals", [])
        simdi = datetime.now(timezone.utc)
        filtreli = []
        for ys in yeni_sinyaller:
            tekrar = False
            for es in eski[-50:]:
                if es.get("symbol") == ys["symbol"] and es.get("pattern") == ys["pattern"]:
                    try:
                        et = datetime.fromisoformat(es["time"])
                        if (simdi - et).total_seconds() < 3600:
                            tekrar = True
                            break
                    except Exception:
                        pass
            if not tekrar:
                filtreli.append(ys)
        
        if filtreli:
            eski.extend(filtreli)
            _save(KOMBO_SINYAL_FILE, {"signals": eski[-500:]})
            # Ana sinyal dosyasına da ekle (backtest değerlendirsin)
            ana = _load(SIGLOG_FILE, {"signals": []})
            ana_s = ana.get("signals", []) if isinstance(ana, dict) else ana
            ana_s.extend(filtreli)
            _save(SIGLOG_FILE, {"signals": ana_s[-1000:]})
            print(f"[Kombo] {len(filtreli)} yeni kombo sinyal üretildi")
    
    return yeni_sinyaller


# ── Research: Piyasa Yenilikleri ───────────────────────────────────────────────
async def piyasa_yenilikleri() -> dict:
    """Trend coinler + korku endeksi + piyasa durumu."""
    sonuc = {"tarih": _now()}
    async with httpx.AsyncClient(timeout=15) as cl:
        try:
            r = await cl.get("https://api.coingecko.com/api/v3/search/trending")
            if r.status_code == 200:
                coins = r.json().get("coins", [])[:5]
                sonuc["trend_coinler"] = [c["item"]["symbol"].upper() for c in coins]
        except Exception:
            pass
        try:
            r = await cl.get("https://api.alternative.me/fng/")
            if r.status_code == 200:
                d = r.json()["data"][0]
                sonuc["korku_endeksi"] = {"deger": d["value"], "durum": d["value_classification"]}
        except Exception:
            pass
        try:
            r = await cl.get("https://api.coingecko.com/api/v3/global")
            if r.status_code == 200:
                g = r.json()["data"]
                sonuc["btc_dominans"] = round(g["market_cap_percentage"]["btc"], 1)
                sonuc["piyasa_degisim_24h"] = round(g["market_cap_change_percentage_24h_usd"], 2)
        except Exception:
            pass
    return sonuc


# ── SAATLİK RAPOR LOOP'LARI ────────────────────────────────────────────────────
async def saatlik_lider_raporu_loop(api_key: str = ""):
    """Her saat başı: sistem denetimi + bot sağlığı + çakışma + özet rapor."""
    await asyncio.sleep(900)  # 15 dk — startup spike'ından kaçın
    while True:
        try:
            denetim   = await sistem_denetimi()
            backtest  = backtest_sinyal_analizi()
            cakisma   = cakisan_bot_analizi()
            
            rapor_metni = f"""📋 LİDER SAATLİK RAPOR
Sistem: {denetim['ozet']}
Sinyal: {backtest.get('toplam_sinyal',0)} toplam, {backtest.get('degerlendirilmis',0)} değerlendirildi
Win Rate: %{backtest.get('genel_win_rate',0)}
Çakışan sinyal: {len(cakisma)}"""
            
            servis_detay = []
            for ad, s in denetim["servisler"].items():
                emoji = "✅" if s["durum"] == "ok" else "❌"
                servis_detay.append(f"{emoji} {ad}")
            
            icerik = {
                "metin": rapor_metni,
                "denetim": denetim,
                "cakismalar": cakisma,
                "servis_detay": servis_detay,
                "win_rate": backtest.get("genel_win_rate", 0),
                "sinyal_sayisi": backtest.get("toplam_sinyal", 0),
            }
            # AI DÜŞÜNCESİ — Lider gerçekten analiz eder
            ai = await _hizli_ai(
                f"Sen kripto trading sisteminin Lider Agent'ısın. Saatlik durum:\n"
                f"Sistem: {denetim['ozet']}\nSinyal: {backtest.get('toplam_sinyal',0)} "
                f"({backtest.get('degerlendirilmis',0)} değerlendirildi, WR %{backtest.get('genel_win_rate',0)})\n"
                f"Bot stats: {json.dumps(backtest.get('bot_stats',{}), ensure_ascii=False)[:600]}\n"
                f"Çakışma: {len(cakisma)}\n"
                f"4-6 cümlede tam bir durum değerlendirmesi yap: (1) sistemin genel sağlığı, "
                f"(2) en dikkat çeken bot/sinyal gözlemi rakamlarla, (3) çakışmalar bir risk mi, "
                f"(4) bugün için somut aksiyon önerisi. Veri azsa onu açıkça söyle, uydurma. Türkçe, net.")
            if ai:
                icerik["ai_dusunce"] = ai
                icerik["metin"] += f"\n\n🤖 {ai}"
            rapor_gecmisi_ekle("lider", icerik)
            print(f"[LiderSaatlik] ✅ {denetim['ozet']}")
        except Exception as e:
            print(f"[LiderSaatlik] Hata: {str(e)[:80]}")
        await asyncio.sleep(3600)


async def saatlik_backtest_loop():
    """Her saat: kombo sinyal tara + sinyal analizini güncelle."""
    await asyncio.sleep(1800)  # 30 dk — startup spike'ından kaçın
    while True:
        try:
            kombolar = await kombo_sinyal_tara()
            backtest = backtest_sinyal_analizi()

            # Sinyal yoksa rapor üretme — Telegram'a "sinyal yok" mesajı gitmesin
            if not kombolar:
                print(f"[BacktestSaatlik] kombo sinyal yok, rapor atlandı")
            else:
                icerik = {
                    "metin": f"BACKTEST: {len(kombolar)} kombo sinyal | WR %{backtest.get('genel_win_rate',0)}",
                    "yeni_kombolar": [{"sembol": k["symbol"], "yon": k["direction"],
                                       "pattern": k["pattern"]} for k in kombolar],
                    "bot_stats": backtest.get("bot_stats", {}),
                }
                ai = await _hizli_ai(
                    f"Sen Backtest Agent'sın. Bu saat {len(kombolar)} kombo sinyal üretildi: "
                    f"{json.dumps([{'s':k['symbol'],'y':k['direction'],'p':k['pattern']} for k in kombolar], ensure_ascii=False)[:300]}\n"
                    f"Bot performansları: {json.dumps(backtest.get('bot_stats',{}), ensure_ascii=False)[:500]}\n"
                    f"3-5 cümlede yorum: hangi sinyaller öne çıkıyor, hangi bot geride, dikkat noktası. "
                    f"Türkçe, rakamlarla, uydurma.")
                if ai:
                    icerik["ai_dusunce"] = ai
                rapor_gecmisi_ekle("backtest", icerik)
                print(f"[BacktestSaatlik] ✅ {len(kombolar)} kombo")
                del icerik
            del kombolar, backtest
        except Exception as e:
            print(f"[BacktestSaatlik] Hata: {str(e)[:80]}")
        import gc; gc.collect()
        await asyncio.sleep(3600)


async def saatlik_research_loop():
    """Her saat: piyasa yenilikleri + research analizi."""
    await asyncio.sleep(2100)  # 35 dk — startup spike'ından kaçın
    while True:
        try:
            yenilik  = await piyasa_yenilikleri()
            research = research_analizi()
            
            trend = ", ".join(yenilik.get("trend_coinler", [])[:5]) or "—"
            fg = yenilik.get("korku_endeksi", {})
            
            icerik = {
                "metin": f"🔍 RESEARCH SAATLİK\nTrend: {trend}\nKorku Endeksi: {fg.get('deger','—')} ({fg.get('durum','—')})\nBTC Dominans: %{yenilik.get('btc_dominans','—')}",
                "yenilikler": yenilik,
                "bulgular": research.get("bulgular", [])[:3],
                "hipotezler": research.get("hipotezler", [])[:2],
            }
            ai = await _hizli_ai(
                f"Sen Research Agent'sın. Piyasa: {json.dumps(yenilik, ensure_ascii=False)[:400]}\n"
                f"Bulgular: {json.dumps(research.get('bulgular',[])[:2], ensure_ascii=False)[:300]}\n"
                f"4-6 cümlede tam analiz: (1) trend coinler + korku endeksi + BTC dominans birlikte hangi "
                f"piyasa rejimini gösteriyor, (2) bu rejim risk-on mu risk-off mu, (3) en kritik research bulgusu, "
                f"(4) botlarımız için 1-2 somut öneri. Türkçe, rakamlarla, uydurma.")
            if ai:
                icerik["ai_dusunce"] = ai
                icerik["metin"] += f"\n\n🤖 {ai}"
                # Öneri motoru: ÖNERİ cümlelerini Telegram onayına gönder
                try:
                    from oneri_motoru import oneri_tara_ve_gonder
                    await oneri_tara_ve_gonder(ai, "research")
                except Exception as e:
                    print(f"[ResearchSaatlik] oneri hata: {str(e)[:60]}")
            rapor_gecmisi_ekle("research", icerik)
            print("[ResearchSaatlik] ✅")
            del yenilik, research, icerik
        except Exception as e:
            print(f"[ResearchSaatlik] Hata: {str(e)[:80]}")
        import gc; gc.collect()
        await asyncio.sleep(3600)


# ─── Otonom Backtest Entegrasyonu ────────────────────────────────────────────
async def otonom_bt_loop():
    """Leader Agent kontrolünde otonom backtest döngüsü."""
    try:
        from oar_autonomous_backtest import otonom_backtest_loop
        await otonom_backtest_loop()
    except Exception as e:
        print(f"[OtonomBT Loop] Başlatma hatası: {e}")
