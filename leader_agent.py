"""
Lider Agent — OAR Premium
════════════════════════════════════════════════════════
Render'da 7/24 çalışır. Tüm botların sinyal geçmişini
izler, backtest yapar, sabah raporu üretir.

Görevler:
  1. Backtest Agent  — bot sinyallerini geçmişe dönük değerlendir
  2. Research Agent  — başarısız/başarılı pattern'ları bul
  3. Rapor          — sohbet arayüzüne ve /api/leader/* endpoint'lerine yaz

Kullanım:
  OAR'ın main.py'si bu modülü import eder.
  /api/leader/report → son raporu döndürür
  /api/leader/backtest → backtest başlatır
  /api/leader/patterns → başarılı pattern'lar
"""

import os, json, asyncio, httpx
from pathlib import Path
from datetime import datetime, timezone, timedelta

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

REPORT_FILE  = DATA_DIR / "leader_report.json"
PATTERN_FILE = DATA_DIR / "leader_patterns.json"
SIGLOG_FILE  = DATA_DIR / "bot_signals_log.json"

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODEL = "gemini-2.5-flash"

def _load(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Backtest Agent ─────────────────────────────────────────────────────────────
def backtest_sinyal_analizi() -> dict:
    """
    Tüm bot sinyallerini analiz et:
    - Haftalık başarı oranı (bot bazında)
    - En iyi saatler (sinyalin verildiği saat → sonuç)
    - Sembol bazında başarı
    - Win/Loss streak'ler
    """
    log = _load(SIGLOG_FILE, {"signals": []})
    sinyaller = log.get("signals", [])

    if not sinyaller:
        return {"durum": "sinyal_yok", "mesaj": "Henüz değerlendirilen sinyal yok."}

    now = datetime.now(timezone.utc)
    
    # ── Bot bazında istatistik
    bot_stats = {}
    saat_stats = {}   # {saat: {"win":0, "loss":0}}
    sembol_stats = {}
    gunluk_stats = {}  # son 30 gün

    for s in sinyaller:
        if not s.get("evaluated"):
            continue
        
        bot = s["bot"]
        win = s.get("outcome") == "WIN"
        
        # Bot stats
        if bot not in bot_stats:
            bot_stats[bot] = {
                "total": 0, "win": 0, "loss": 0,
                "win_rate": 0, "avg_change": 0, "changes": [],
                "by_direction": {"LONG": {"w":0,"l":0}, "SHORT": {"w":0,"l":0}},
                "streak": 0, "max_streak": 0, "cur_streak_win": True
            }
        st = bot_stats[bot]
        st["total"] += 1
        if win:
            st["win"] += 1
            if st["cur_streak_win"]:
                st["streak"] += 1
            else:
                st["streak"] = 1
                st["cur_streak_win"] = True
        else:
            st["loss"] += 1
            if not st["cur_streak_win"]:
                st["streak"] += 1
            else:
                st["streak"] = 1
                st["cur_streak_win"] = False
        st["max_streak"] = max(st["max_streak"], st["streak"])
        
        if s.get("change_pct") is not None:
            st["changes"].append(s["change_pct"])
        
        d = s.get("direction", "LONG")
        if win:
            st["by_direction"][d]["w"] += 1
        else:
            st["by_direction"][d]["l"] += 1

        # Saat stats
        try:
            t = datetime.fromisoformat(s["logged_at"])
            saat = t.hour
            if saat not in saat_stats:
                saat_stats[saat] = {"win": 0, "loss": 0}
            saat_stats[saat]["win" if win else "loss"] += 1
        except Exception:
            pass

        # Sembol stats
        sym = s.get("symbol", "BTCUSDT")
        if sym not in sembol_stats:
            sembol_stats[sym] = {"win": 0, "loss": 0}
        sembol_stats[sym]["win" if win else "loss"] += 1

        # Günlük
        try:
            gun = datetime.fromisoformat(s["logged_at"]).strftime("%Y-%m-%d")
            if gun not in gunluk_stats:
                gunluk_stats[gun] = {"win": 0, "loss": 0}
            gunluk_stats[gun]["win" if win else "loss"] += 1
        except Exception:
            pass

    # Win rate hesapla
    for bot, st in bot_stats.items():
        if st["total"] > 0:
            st["win_rate"] = round(st["win"] / st["total"] * 100, 1)
        if st["changes"]:
            st["avg_change"] = round(sum(st["changes"]) / len(st["changes"]), 2)
        del st["changes"]  # JSON'a gerek yok

    # En iyi saatler
    en_iyi_saatler = sorted(
        [(s, d["win"]/(d["win"]+d["loss"])*100 if (d["win"]+d["loss"])>0 else 0, d["win"]+d["loss"])
         for s, d in saat_stats.items()],
        key=lambda x: x[1], reverse=True
    )[:5]

    # En başarılı semboller
    en_iyi_semboller = sorted(
        [(sym, d["win"]/(d["win"]+d["loss"])*100 if (d["win"]+d["loss"])>0 else 0, d["win"]+d["loss"])
         for sym, d in sembol_stats.items() if (d["win"]+d["loss"]) >= 3],
        key=lambda x: x[1], reverse=True
    )[:5]

    return {
        "tarih": now.isoformat(),
        "toplam_sinyal": len([s for s in sinyaller if s.get("evaluated")]),
        "bot_stats": bot_stats,
        "en_iyi_saatler": [{"saat": s, "win_rate": round(r,1), "total": t} for s,r,t in en_iyi_saatler],
        "en_iyi_semboller": [{"sembol": s, "win_rate": round(r,1), "total": t} for s,r,t in en_iyi_semboller],
        "gunluk_son7": {g: v for g, v in sorted(gunluk_stats.items())[-7:]},
    }


# ── Research Agent ──────────────────────────────────────────────────────────────
def pattern_analizi() -> dict:
    """
    Başarılı sinyallerde ortak pattern'ları bul:
    - Hangi yön (LONG/SHORT) daha başarılı
    - Hangi saat dilimi
    - Hangi semboller
    - Hangi botların kombinasyonu güçlü
    """
    log = _load(SIGLOG_FILE, {"signals": []})
    sinyaller = [s for s in log.get("signals", []) if s.get("evaluated")]
    
    if len(sinyaller) < 5:
        return {"durum": "yetersiz_veri", "mesaj": f"Değerlendirilen {len(sinyaller)} sinyal var. Pattern için en az 5 gerekli."}

    # Yön analizi
    long_win  = sum(1 for s in sinyaller if s["direction"]=="LONG"  and s["outcome"]=="WIN")
    long_tot  = sum(1 for s in sinyaller if s["direction"]=="LONG")
    short_win = sum(1 for s in sinyaller if s["direction"]=="SHORT" and s["outcome"]=="WIN")
    short_tot = sum(1 for s in sinyaller if s["direction"]=="SHORT")

    # Saat dilimi: gece/sabah/öğle/akşam
    dilimler = {"gece(0-6)": [], "sabah(7-11)": [], "oglen(12-17)": [], "aksam(18-23)": []}
    for s in sinyaller:
        try:
            h = datetime.fromisoformat(s["logged_at"]).hour
            if h < 7:     dilimler["gece(0-6)"].append(s["outcome"]=="WIN")
            elif h < 12:  dilimler["sabah(7-11)"].append(s["outcome"]=="WIN")
            elif h < 18:  dilimler["oglen(12-17)"].append(s["outcome"]=="WIN")
            else:         dilimler["aksam(18-23)"].append(s["outcome"]=="WIN")
        except Exception:
            pass

    dilim_sonuc = {}
    for dilim, outcomes in dilimler.items():
        if outcomes:
            wr = sum(outcomes)/len(outcomes)*100
            dilim_sonuc[dilim] = {"win_rate": round(wr,1), "total": len(outcomes)}

    # Kombinasyon analizi: aynı sembolde birden fazla bot sinyal verdi mi
    # Zaman penceresi: 2 saat içinde aynı sembol + aynı yön → "teyitli sinyal"
    teyitli_win = teyitli_tot = 0
    sinyaller_sorted = sorted(sinyaller, key=lambda x: x.get("logged_at",""))
    for i, s1 in enumerate(sinyaller_sorted):
        for s2 in sinyaller_sorted[i+1:]:
            try:
                t1 = datetime.fromisoformat(s1["logged_at"])
                t2 = datetime.fromisoformat(s2["logged_at"])
                if (t2-t1).total_seconds() > 7200: break
                if s1["symbol"]==s2["symbol"] and s1["direction"]==s2["direction"] and s1["bot"]!=s2["bot"]:
                    teyitli_tot += 1
                    if s1["outcome"]=="WIN" and s2["outcome"]=="WIN":
                        teyitli_win += 1
            except Exception:
                pass

    oneriler = []
    
    # Yön önerisi
    if long_tot >= 3 and short_tot >= 3:
        lwr = long_win/long_tot*100
        swr = short_win/short_tot*100 if short_tot else 0
        if abs(lwr-swr) > 15:
            guclu = "LONG" if lwr>swr else "SHORT"
            oneriler.append(f"{'LONG' if lwr>swr else 'SHORT'} sinyaller daha başarılı (%{max(lwr,swr):.0f} vs %{min(lwr,swr):.0f}) — {guclu} yönüne odaklan")

    # Saat dilimi önerisi
    if dilim_sonuc:
        en_iyi_dilim = max(dilim_sonuc, key=lambda x: dilim_sonuc[x]["win_rate"])
        en_kotu_dilim = min(dilim_sonuc, key=lambda x: dilim_sonuc[x]["win_rate"])
        if dilim_sonuc[en_iyi_dilim]["win_rate"] - dilim_sonuc[en_kotu_dilim]["win_rate"] > 20:
            oneriler.append(f"{en_iyi_dilim} en başarılı saat dilimi (%{dilim_sonuc[en_iyi_dilim]['win_rate']:.0f}) — bu saatlerdeki sinyallere öncelik ver")

    # Teyitli sinyal önerisi
    if teyitli_tot >= 3:
        twr = teyitli_win/teyitli_tot*100
        oneriler.append(f"Birden fazla bot aynı anda sinyal verince başarı oranı %{twr:.0f} — çoklu teyit bekle")

    return {
        "yon_analizi": {
            "LONG":  {"win_rate": round(long_win/long_tot*100,1) if long_tot else 0, "total": long_tot},
            "SHORT": {"win_rate": round(short_win/short_tot*100,1) if short_tot else 0, "total": short_tot},
        },
        "saat_dilimi": dilim_sonuc,
        "teyitli_sinyaller": {"total": teyitli_tot, "win_rate": round(teyitli_win/teyitli_tot*100,1) if teyitli_tot else 0},
        "oneriler": oneriler,
        "tarih": datetime.now(timezone.utc).isoformat(),
    }


# ── AI Yorumu (Gemini ile) ──────────────────────────────────────────────────────
async def ai_yorum_uret(backtest: dict, patterns: dict, api_key: str) -> str:
    """Backtest + pattern verilerini Gemini ile yorumla."""
    if not api_key:
        return "AI yorum için GEMINI_API_KEY gerekli."

    prompt = f"""Sen bir kripto trading analisti yardımcısısın.
Aşağıdaki bot sinyal backtest verilerini analiz et ve kısa, net öneriler sun.

BACKTEST VERİSİ:
{json.dumps(backtest, ensure_ascii=False, indent=2)[:2000]}

PATTERN ANALİZİ:
{json.dumps(patterns, ensure_ascii=False, indent=2)[:1000]}

Şunları söyle (madde madde, Türkçe):
1. En güvenilir bot hangisi ve neden
2. Kaçınılması gereken durum var mı
3. Bu haftanın en önemli 1 önerisi
4. Genel performans değerlendirmesi (1-2 cümle)

Kesin rakamlarla konuş, tahmin yapma."""

    try:
        url = f"{GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent?key={api_key}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 600}
        }
        async with httpx.AsyncClient(timeout=30) as cl:
            r = await cl.post(url, json=payload)
            if r.status_code == 200:
                data = r.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"AI yorum alınamadı: {str(e)[:100]}"
    return "AI yorum alınamadı."


# ── Ana Rapor Üretici ───────────────────────────────────────────────────────────
async def rapor_uret(api_key: str = "") -> dict:
    """
    Lider Agent'ın ana raporu.
    Backtest + Pattern + AI yorum bir arada.
    """
    backtest = backtest_sinyal_analizi()
    patterns = pattern_analizi()

    ai_yorum = ""
    if api_key and backtest.get("toplam_sinyal", 0) > 0:
        ai_yorum = await ai_yorum_uret(backtest, patterns, api_key)

    rapor = {
        "tarih": datetime.now(timezone.utc).isoformat(),
        "backtest": backtest,
        "patterns": patterns,
        "ai_yorum": ai_yorum,
        "ozet": _ozet_uret(backtest, patterns),
    }

    _save(REPORT_FILE, rapor)
    _save(PATTERN_FILE, patterns)
    return rapor


def _ozet_uret(backtest: dict, patterns: dict) -> dict:
    """Hızlı bakış için sayısal özet."""
    bot_stats = backtest.get("bot_stats", {})
    if not bot_stats:
        return {"durum": "veri_yok"}
    
    en_iyi = max(bot_stats, key=lambda b: bot_stats[b]["win_rate"]) if bot_stats else None
    toplam = sum(st["total"] for st in bot_stats.values())
    toplam_win = sum(st["win"] for st in bot_stats.values())
    
    return {
        "toplam_sinyal": toplam,
        "genel_win_rate": round(toplam_win/toplam*100, 1) if toplam else 0,
        "en_iyi_bot": en_iyi,
        "en_iyi_bot_win_rate": bot_stats[en_iyi]["win_rate"] if en_iyi else 0,
        "oneriler": patterns.get("oneriler", []),
    }


def son_rapor() -> dict:
    """Kaydedilmiş son raporu döndür."""
    return _load(REPORT_FILE, {"durum": "henuz_rapor_yok"})


def son_pattern() -> dict:
    """Kaydedilmiş son pattern analizini döndür."""
    return _load(PATTERN_FILE, {"durum": "henuz_analiz_yok"})


# ── Otomatik Sabah Raporu (Render'da arka planda çalışır) ──────────────────────
async def sabah_raporu_loop(api_key: str = ""):
    """Her sabah 07:00 UTC'de rapor üret."""
    import asyncio
    while True:
        try:
            now = datetime.now(timezone.utc)
            # Saat 07:00 UTC bekle
            hedef = now.replace(hour=7, minute=0, second=0, microsecond=0)
            if now >= hedef:
                hedef += timedelta(days=1)
            bekle = (hedef - now).total_seconds()
            await asyncio.sleep(bekle)
            print("[LiderAgent] Sabah raporu üretiliyor...")
            await rapor_uret(api_key)
            print("[LiderAgent] ✅ Sabah raporu tamamlandı")
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[LiderAgent] Rapor hatası: {e}")
            await asyncio.sleep(3600)  # Hata olursa 1 saat sonra tekrar
