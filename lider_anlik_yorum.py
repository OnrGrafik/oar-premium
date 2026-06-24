"""
Lider Anlık Yorum — OAR Premium
══════════════════════════════════════════════════════════════════
BTC ve ETH için opsiyon, order flow, piyasa yapısı ve seans
verilerini birleştirerek anlamlı bir değişim tespit edildiğinde
Telegram'a anlık AI yorumu gönderir.

Tetikleyiciler (en az biri geçmeli):
  • Fiyat 15 dakikada ≥%1.0 hareket
  • OI 30 dakikada ≥%2.0 değişim
  • Funding rate ±0.03% eşik geçişi
  • GEX rejim değişimi (POZİTİF ↔ NEGATİF)
  • CVD yönü tersine dönüşü
  • Coinbase premium işaret değişimi (pozitif → negatif veya tersi)
  • OAR Score 10+ puan sıçraması

Her tetikleme → tüm veriler toplanır → Gemini yorumu üretilir → Telegram.
Aynı symbol için yorumlar arası minimum 20 dakika bekletme vardır.
"""

import asyncio
import json
import os
import hashlib
import httpx
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DURUM_FILE = DATA_DIR / "lider_anlik_durum.json"

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Minimum gönderim aralığı (dakika) — aynı sembol için
MIN_ARALIK_DK = 20

# Tetikleyici eşikler
ESIK = {
    "fiyat_pct":    1.0,    # 15 dakikada %1
    "oi_pct":       2.0,    # 30 dakikada %2
    "funding_abs":  0.03,   # ±%0.03 eşiği (ondalık: 0.0003)
    "oar_skor":     10,     # puan farkı
}

SEMBOLLER = [
    {"sembol": "BTCUSDT", "kok": "BTC"},
    {"sembol": "ETHUSDT", "kok": "ETH"},
]


# ─── Durum dosyası ────────────────────────────────────────────────

def _durum_yukle() -> dict:
    try:
        if DURUM_FILE.exists():
            return json.loads(DURUM_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _durum_kaydet(d: dict):
    try:
        DURUM_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


def _son_gonderi_gecti_mi(durum: dict, kok: str) -> bool:
    """Son gönderimden MIN_ARALIK_DK geçti mi?"""
    anahtar = f"son_gonderi_{kok}"
    son = durum.get(anahtar)
    if not son:
        return True
    try:
        son_zaman = datetime.fromisoformat(son)
        return (_simdi() - son_zaman).total_seconds() >= MIN_ARALIK_DK * 60
    except Exception:
        return True


# ─── Veri toplama ─────────────────────────────────────────────────

async def _veri_topla(sembol: str, kok: str) -> dict:
    """BTC veya ETH için tüm kaynakları birleştirir."""
    veri = {"sembol": sembol, "kok": kok, "tarih": _simdi().isoformat()}
    hata_listesi = []

    # 1. Fiyat + OI + Funding (exchange_client)
    try:
        from exchange_client import ticker_price, open_interest, funding_rate
        fiyat, oi_gecmis, fund = await asyncio.gather(
            ticker_price(sembol, futures=True),
            open_interest(sembol, interval="5m", limit=6),
            funding_rate(sembol),
            return_exceptions=True,
        )
        veri["fiyat"] = fiyat if isinstance(fiyat, float) else 0.0
        veri["funding_pct"] = (fund * 100) if isinstance(fund, float) else 0.0

        if isinstance(oi_gecmis, list) and len(oi_gecmis) >= 2:
            oi_ilk = oi_gecmis[0]["oi"]
            oi_son = oi_gecmis[-1]["oi"]
            veri["oi_degisim_pct"] = round(
                (oi_son - oi_ilk) / oi_ilk * 100, 3) if oi_ilk else 0.0
            veri["oi_son"] = round(oi_son, 0)
        else:
            veri["oi_degisim_pct"] = 0.0
    except Exception as e:
        hata_listesi.append(f"exchange: {str(e)[:60]}")

    # 2. Order Flow (CVD + Coinbase Premium)
    try:
        from order_flow_agent import order_flow_analiz
        flow = await order_flow_analiz(sembol, "5m", 30)
        veri["flow"] = {
            "karar":      flow.get("karar", "NEUTRAL_FLOW"),
            "puan":       flow.get("puan", 0),
            "cvd_yon":    flow.get("cvd", {}).get("yon", "NOTR"),
            "cb_premium": flow.get("cb_premium", {}).get("yon", "NOTR"),
            "cb_pct":     flow.get("cb_premium", {}).get("premium_pct", 0.0),
            "oi_yon":     flow.get("oi", {}).get("yon", "NOTR"),
        }
    except Exception as e:
        veri["flow"] = {}
        hata_listesi.append(f"orderflow: {str(e)[:60]}")

    # 3. Piyasa Yapısı (1h)
    try:
        from market_structure_agent import market_structure_analiz
        yapi = await market_structure_analiz(sembol, "1h", 60)
        veri["yapi"] = {
            "yapi":   yapi.get("yapi", "UNKNOWN"),
            "adx":    yapi.get("adx", 0),
            "bos":    yapi.get("bos", "YOK"),
            "choch":  yapi.get("choch", "YOK"),
            "zincir": yapi.get("zincir", "BELIRSIZ"),
        }
    except Exception as e:
        veri["yapi"] = {}
        hata_listesi.append(f"structure: {str(e)[:60]}")

    # 4. Opsiyon Verisi (sadece BTC ve ETH)
    try:
        from options_engine import gex_ozet, alarm_levels
        gex, alarlar = await asyncio.gather(
            gex_ozet(kok),
            alarm_levels(kok),
            return_exceptions=True,
        )
        if isinstance(gex, dict) and not gex.get("error"):
            genel = alarlar.get("genel", {}) if isinstance(alarlar, dict) else {}
            kisa = alarlar.get("kisa", {}) if isinstance(alarlar, dict) else {}
            veri["opsiyonlar"] = {
                "gamma_rejim": gex.get("gamma_rejim", "—"),
                "spot":        gex.get("spot", 0),
                "call_wall":   gex.get("call_wall"),
                "put_wall":    gex.get("put_wall"),
                "zero_gamma":  gex.get("zero_gamma"),
                "max_pain":    genel.get("max_pain"),
                "kisa_cw":     kisa.get("call_wall"),
                "kisa_pw":     kisa.get("put_wall"),
            }
        else:
            veri["opsiyonlar"] = {}
    except Exception as e:
        veri["opsiyonlar"] = {}
        hata_listesi.append(f"opsiyonlar: {str(e)[:60]}")

    # 5. Seans Bağlamı
    try:
        from session_agent import session_analiz
        seans = await session_analiz(sembol)
        veri["seans"] = {
            "aktif":          seans.get("aktif_seans", "BILINMIYOR"),
            "yonlendirme":    seans.get("trade_yonlendirme", "BEKLE"),
            "asia_yon":       seans.get("asia", {}).get("yon", "BILINMIYOR"),
            "london_yon":     seans.get("london", {}).get("yon", "BILINMIYOR"),
            "ny_yon":         seans.get("ny", {}).get("yon", "BILINMIYOR"),
        }
    except Exception as e:
        veri["seans"] = {}
        hata_listesi.append(f"seans: {str(e)[:60]}")

    # 6. OAR Skoru (market_context)
    try:
        from market_context import baglam_guncelle
        ctx = await baglam_guncelle(sembol)
        veri["oar_skor"] = ctx.get("oar_score", {}).get("skor", 0)
        veri["market_rejim"] = ctx.get("regime", {}).get("rejim", "BILINMIYOR")
        veri["move_source"] = ctx.get("move_source", {}).get("kaynak", "BILINMIYOR")
    except Exception as e:
        veri["oar_skor"] = 0
        veri["market_rejim"] = "BILINMIYOR"
        hata_listesi.append(f"market_ctx: {str(e)[:60]}")

    if hata_listesi:
        veri["_hatalar"] = hata_listesi

    return veri


# ─── Değişim tespiti ──────────────────────────────────────────────

def _degisim_tespit(yeni: dict, onceki: dict, durum: dict, kok: str) -> list:
    """
    Tetikleyicileri kontrol eder.
    Döner: [tetikleyici_aciklama, ...] — boşsa değişim yok.
    """
    tetikler = []

    # Fiyat değişimi (önceki fiyatla karşılaştır)
    onceki_fiyat = onceki.get("fiyat", 0)
    yeni_fiyat = yeni.get("fiyat", 0)
    if onceki_fiyat and yeni_fiyat:
        fiyat_pct = abs(yeni_fiyat - onceki_fiyat) / onceki_fiyat * 100
        if fiyat_pct >= ESIK["fiyat_pct"]:
            yon = "yukari" if yeni_fiyat > onceki_fiyat else "asagi"
            tetikler.append(f"Fiyat %{fiyat_pct:.2f} {yon}: ${yeni_fiyat:,.0f}")

    # OI değişimi
    oi_pct = abs(yeni.get("oi_degisim_pct", 0))
    if oi_pct >= ESIK["oi_pct"]:
        tetikler.append(f"OI {yeni['oi_degisim_pct']:+.2f}% ({yeni.get('oi_son',0):,.0f})")

    # Funding eşik geçişi
    yeni_fund = yeni.get("funding_pct", 0)
    onceki_fund = onceki.get("funding_pct", 0)
    if abs(yeni_fund) >= ESIK["funding_abs"] and abs(onceki_fund) < ESIK["funding_abs"]:
        tetikler.append(f"Funding esigi: %{yeni_fund:+.4f}")
    # Funding işaret değişimi
    elif onceki_fund != 0 and yeni_fund * onceki_fund < 0:
        tetikler.append(f"Funding isaret degisti: %{onceki_fund:+.4f} → %{yeni_fund:+.4f}")

    # GEX rejim değişimi
    yeni_gex = yeni.get("opsiyonlar", {}).get("gamma_rejim", "")
    onceki_gex = onceki.get("opsiyonlar", {}).get("gamma_rejim", "")
    if yeni_gex and onceki_gex and yeni_gex != onceki_gex and onceki_gex != "—":
        tetikler.append(f"GEX rejim degisti: {onceki_gex} → {yeni_gex}")

    # CVD yön değişimi
    yeni_cvd = yeni.get("flow", {}).get("cvd_yon", "")
    onceki_cvd = onceki.get("flow", {}).get("cvd_yon", "")
    if yeni_cvd and onceki_cvd and yeni_cvd != onceki_cvd and onceki_cvd != "NOTR":
        tetikler.append(f"CVD yon degisti: {onceki_cvd} → {yeni_cvd}")

    # Coinbase Premium işaret değişimi
    yeni_cb = yeni.get("flow", {}).get("cb_premium", "NOTR")
    onceki_cb = onceki.get("flow", {}).get("cb_premium", "NOTR")
    if yeni_cb != onceki_cb and "NOTR" not in (yeni_cb, onceki_cb):
        tetikler.append(f"CB Premium: {onceki_cb} → {yeni_cb} (%{yeni.get('flow',{}).get('cb_pct',0):+.3f})")

    # OAR Skor sıçraması
    yeni_skor = yeni.get("oar_skor", 0)
    onceki_skor = onceki.get("oar_skor", 0)
    if abs(yeni_skor - onceki_skor) >= ESIK["oar_skor"]:
        tetikler.append(f"OAR Skor: {onceki_skor} → {yeni_skor}")

    return tetikler


# ─── AI Yorumu ────────────────────────────────────────────────────

async def _ai_yorum(veri: dict, tetikler: list) -> str:
    """Gemini'ye tüm verileri verip kısa bir yorum ürettir."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return ""

    opsiyonlar = veri.get("opsiyonlar", {})
    flow = veri.get("flow", {})
    yapi = veri.get("yapi", {})
    seans = veri.get("seans", {})

    prompt = f"""Sen BTC/ETH kripto piyasası Lider Analist Agent'sın.
Aşağıdaki anlık piyasa verisine göre 3-5 cümlelik sert ve net bir yorum yap.
Gereksiz giriş cümlesi yok. Doğrudan verilere değin.

Sembol: {veri['sembol']}
Fiyat: ${veri.get('fiyat', 0):,.0f}
Tetikleyen değişimler: {' | '.join(tetikler)}

--- VERİ ---
Piyasa Yapısı (1h): {yapi.get('yapi','?')} | ADX: {yapi.get('adx',0)} | BOS: {yapi.get('bos','?')} | CHoCH: {yapi.get('choch','?')} | Zincir: {yapi.get('zincir','?')}
Order Flow: {flow.get('karar','?')} (puan: {flow.get('puan',0):+d}) | CVD: {flow.get('cvd_yon','?')} | CB Premium: {flow.get('cb_premium','?')} (%{flow.get('cb_pct',0):+.3f})
OI Değişim: %{veri.get('oi_degisim_pct',0):+.2f} | Funding: %{veri.get('funding_pct',0):+.4f}
Opsiyon: {opsiyonlar.get('gamma_rejim','veri yok')} | Call Wall: ${opsiyonlar.get('call_wall') or '?':} | Put Wall: ${opsiyonlar.get('put_wall') or '?':} | Zero Gamma: ${opsiyonlar.get('zero_gamma') or '?':} | Max Pain: ${opsiyonlar.get('max_pain') or '?':}
Kısa vadeli (0-7g): CW ${opsiyonlar.get('kisa_cw') or '?':} | PW ${opsiyonlar.get('kisa_pw') or '?':}
Market Rejim: {veri.get('market_rejim','?')} | Move Source: {veri.get('move_source','?')} | OAR Skor: {veri.get('oar_skor',0)}/100
Seans: {seans.get('aktif','?')} | Asia={seans.get('asia_yon','?')} London={seans.get('london_yon','?')} NY={seans.get('ny_yon','?')} | Yönlendirme: {seans.get('yonlendirme','?')}

Yorumunda şunlara değin (uygunsa):
1. Bu değişimin ne anlama geldiği (momentum mu, trap mı, rejim değişimi mi?)
2. Opsiyon seviyeleri fiyatı nereye çekiyor/itiyor?
3. CB Premium veya CVD bir yön teyidi veriyor mu yoksa çelişiyor mu?
4. Kısa vadeli için dikkat edilecek en kritik seviye veya senario

Türkçe, rakamlarla, uydurma."""

    try:
        url = f"{GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent?key={api_key}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.25, "maxOutputTokens": 500},
        }
        async with httpx.AsyncClient(timeout=30) as cl:
            r = await cl.post(url, json=payload)
            if r.status_code == 200:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"[LiderYorum] AI hatası: {str(e)[:80]}")
    return ""


# ─── Telegram mesajı ──────────────────────────────────────────────

def _telegram_mesaj_olustur(veri: dict, tetikler: list, ai_yorum: str) -> str:
    kok = veri["kok"]
    fiyat = veri.get("fiyat", 0)
    flow = veri.get("flow", {})
    opsiyonlar = veri.get("opsiyonlar", {})
    seans = veri.get("seans", {})

    flow_emoji = {"BULLISH_FLOW": "Alici", "BEARISH_FLOW": "Satici", "NEUTRAL_FLOW": "Notr"}.get(
        flow.get("karar", ""), "?")
    gex = opsiyonlar.get("gamma_rejim", "—")

    satirlar = [
        f"OAR — {kok} Anlik Yorum",
        f"${fiyat:,.0f}  |  Seans: {seans.get('aktif','?')}  |  Flow: {flow_emoji}",
        "",
        f"Tetikleyen: {' | '.join(tetikler)}",
    ]

    if opsiyonlar:
        cw = opsiyonlar.get("call_wall")
        pw = opsiyonlar.get("put_wall")
        zg = opsiyonlar.get("zero_gamma")
        mp = opsiyonlar.get("max_pain")
        opt_satirlar = [f"Opsiyonlar: {gex}"]
        if cw: opt_satirlar.append(f"  CW ${cw:,.0f}")
        if pw: opt_satirlar.append(f"  PW ${pw:,.0f}")
        if zg: opt_satirlar.append(f"  Zero Gamma ${zg:,.0f}")
        if mp: opt_satirlar.append(f"  Max Pain ${mp:,.0f}")
        satirlar.append("\n".join(opt_satirlar))

    if flow.get("cb_premium") not in ("NOTR", "BILINMIYOR", None, ""):
        satirlar.append(
            f"CB Premium: {flow['cb_premium']} (%{flow.get('cb_pct',0):+.3f})")

    if ai_yorum:
        satirlar.append("")
        satirlar.append(ai_yorum)

    satirlar.append(f"\n{veri['tarih'][:16].replace('T',' ')} UTC")
    return "\n".join(satirlar)


# ─── Ana döngü ────────────────────────────────────────────────────

async def lider_anlik_yorum_loop():
    """
    Her 5 dakikada BTC ve ETH kontrol eder.
    Anlamlı değişim varsa AI yorumu üretip Telegram'a gönderir.
    """
    await asyncio.sleep(120)  # startup spike'ından kaçın

    durum = _durum_yukle()
    print("[LiderYorum] anlık yorum döngüsü başladı")

    while True:
        for bilgi in SEMBOLLER:
            sembol = bilgi["sembol"]
            kok = bilgi["kok"]
            try:
                yeni = await _veri_topla(sembol, kok)
                onceki = durum.get(f"onceki_{kok}", {})

                tetikler = _degisim_tespit(yeni, onceki, durum, kok)

                if tetikler and _son_gonderi_gecti_mi(durum, kok):
                    ai = await _ai_yorum(yeni, tetikler)
                    mesaj = _telegram_mesaj_olustur(yeni, tetikler, ai)

                    # Telegram'a gönder
                    from main import _telegram_gonder  # type: ignore
                    await _telegram_gonder(mesaj)

                    durum[f"son_gonderi_{kok}"] = _simdi().isoformat()
                    print(f"[LiderYorum] {kok} yorum gönderildi: {' | '.join(tetikler)}")
                else:
                    if tetikler:
                        print(f"[LiderYorum] {kok} tetiklendi ama min aralik bekleniyor")

                # Her durumda mevcut durumu kaydet
                durum[f"onceki_{kok}"] = {
                    "fiyat": yeni.get("fiyat", 0),
                    "oi_degisim_pct": yeni.get("oi_degisim_pct", 0),
                    "funding_pct": yeni.get("funding_pct", 0),
                    "oar_skor": yeni.get("oar_skor", 0),
                    "opsiyonlar": {"gamma_rejim": yeni.get("opsiyonlar", {}).get("gamma_rejim", "")},
                    "flow": {
                        "cvd_yon": yeni.get("flow", {}).get("cvd_yon", ""),
                        "cb_premium": yeni.get("flow", {}).get("cb_premium", "NOTR"),
                        "cb_pct": yeni.get("flow", {}).get("cb_pct", 0.0),
                    },
                }
                _durum_kaydet(durum)

            except Exception as e:
                print(f"[LiderYorum] {kok} hata: {str(e)[:100]}")

            # BTC ve ETH arasında 30s bekleme — API rate limit
            await asyncio.sleep(30)

        await asyncio.sleep(270)  # toplam ~5 dakika (30+30+270)
