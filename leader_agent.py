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
        "sembol": "ETHUSDT",
        "strateji": "xATR Trailing Stop + STC osilatör + RSI filtresi. BTC yön teyidi gerektirir.",
        "sinyal_tipi": ["LONG", "SHORT"],
        "veriler": ["Bybit OHLCV", "xATR", "STC", "RSI"],
        "zaman_dilimleri": ["5m"],
        "kritik_parametreler": {"leverage": 100, "daily_tp": 10, "daily_sl": 10},
    },
    "MA Scanner": {
        "sembol": "Top 100 futures",
        "strateji": "MA temas (±%0.50 tolerans) + Whale/Retail filtresi (7 günlük).",
        "sinyal_tipi": ["LONG", "SHORT"],
        "veriler": ["Binance futures klines", "analiz_bot whale/retail"],
        "zaman_dilimleri": ["1d"],
        "kritik_parametreler": {"temas_tolerans_pct": 0.50, "tarama_aralik_saat": 1},
    },
    "CVD Scanner": {
        "sembol": "Top 100 futures",
        "strateji": "CVD (10m/1H/24H) + OI artış + hacim patlaması. Skor ≥65 sinyal.",
        "sinyal_tipi": ["AKUMULASYON", "GUCLU_PUMP", "ZAYIF_PUMP", "DAGITIM"],
        "veriler": ["Binance klines", "aggTrades", "openInterestHist", "funding"],
        "zaman_dilimleri": ["10m"],
        "kritik_parametreler": {"min_skor": 65, "tarama_aralik_dk": 10},
    },
    "Asia Ekstrem": {
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
        "sembol": "BTCUSDT",
        "strateji": "Tek aggTrade ≥1000 BTC taker alış/satış tespiti.",
        "sinyal_tipi": ["ALIS", "SATIS"],
        "veriler": ["Binance Futures aggTrades"],
        "zaman_dilimleri": ["anlık"],
        "kritik_parametreler": {"esik_btc": 1000, "tarama_aralik_sn": 5},
    },
    "Volume Bot": {
        "sembol": "Top 100 futures",
        "strateji": "Hacim 1h ≥%5 + OI 1h ≥%5 + fiyat VWAP/MA20 üstü + whale long filtresi.",
        "sinyal_tipi": ["HACIM_PATLAMA"],
        "veriler": ["Binance 1h klines", "openInterestHist", "analiz_bot"],
        "zaman_dilimleri": ["1h"],
        "kritik_parametreler": {"hacim_min_pct": 5, "oi_min_pct": 5, "tarama_aralik_dk": 15},
    },
    "Korelasyon": {
        "sembol": "BTC + QQQ/DXY/GLD/TNX",
        "strateji": "14/30 günlük Pearson korelasyon + beta. Rejim: risk-on/risk-off.",
        "sinyal_tipi": ["RISK_ON", "RISK_OFF"],
        "veriler": ["Binance daily", "yfinance (QQQ/DXY/GLD/TNX)"],
        "zaman_dilimleri": ["1d"],
        "kritik_parametreler": {"pencereler": [14, 30], "gonderim_saati": "09:00 TR"},
    },
    "Whale Tracker": {
        "sembol": "BTC/ETH on-chain",
        "strateji": "BlackRock/MicroStrategy cüzdan hareketi + ETF akış takibi.",
        "sinyal_tipi": ["KURUMSAL_HAREKET"],
        "veriler": ["Blockchain.info", "Etherscan", "yfinance ETF"],
        "zaman_dilimleri": ["anlık"],
        "kritik_parametreler": {"min_btc": 50, "min_eth": 500},
    },
    "Makro Alarm": {
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

    degerlendirilmis = [s for s in sinyaller if s.get("outcome") in ["WIN", "LOSS"]]

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

    # ── Geliştirme önerileri
    if len(bot_stats) > 0:
        en_iyi_bot = max(bot_stats, key=lambda b: bot_stats[b]["win_rate"]) if bot_stats else None
        if en_iyi_bot:
            oneriler.append(f"En güvenilir bot: {en_iyi_bot} (%{bot_stats[en_iyi_bot]['win_rate']} win rate) — bu botun sinyallerine ağırlık ver")

    oneriler.append("CVD Scanner + Asia Ekstrem kombinasyonu test edilmeli: aynı coin/yön teyidi")
    oneriler.append("Witching Day (vadeli işlem son gün) etkisi analiz edilmeli — max pain seviyesiyle birleştir")
    oneriler.append("Korelasyon botu risk-off rejim bildirdiğinde diğer botların LONG sinyallerini filtrele")

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
    bot_url = os.environ.get("BOT_URL", "https://oar-sinyal-bot.onrender.com")
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
        prompt = f"""Sen OAR Premium'un Lider Agent'ısın. Sabah raporunu oluştur.

BOT KATALOĞU:
{json.dumps({b: i["strateji"] for b,i in BOT_KATALOG.items()}, ensure_ascii=False)}

BACKTEST ANALİZİ:
{json.dumps(backtest, ensure_ascii=False)[:2000]}

RESEARCH BULGULARI:
{json.dumps(research, ensure_ascii=False)[:1500]}

GÖREV KUYRUĞU:
{json.dumps(_load(TASKS_FILE, {}), ensure_ascii=False)[:500]}

Şunları içeren kısa sabah raporu yaz (Türkçe, madde madde):
1. Genel performans özeti (genel win rate, en iyi bot)
2. Research Agent'tan en kritik 2 bulgu
3. Bugün odaklanılacak 1 hipotez/test
4. Herhangi bir bot için acil uyarı varsa belirt
5. Genel tavsiye (1 cümle)

Tahmin yapma, yalnızca verideki gerçekleri yorumla."""

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


# ── Sinyal Toplayıcı — bot servisinden sinyalleri çek, OAR diskine yaz ─────────
async def sinyal_toplayici_loop():
    """
    Her 5 dakikada bir bot servisinin /signals endpoint'inden sinyalleri çeker,
    OAR'ın kendi diskindeki SIGLOG_FILE'a merge eder.
    İki servis ayrı disklerde olduğu için bu köprü zorunlu.
    """
    bot_url = os.environ.get("BOT_URL", "https://oar-sinyal-bot.onrender.com")
    await asyncio.sleep(15)  # başlangıçta bekle
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

                        sinyaller = sinyaller[-1000:]  # son 1000 sinyal tut
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
