"""
Confidence Engine — OAR Premium
════════════════════════════════════════════════════════════════════
Tüm agentlardan skor toplar, ağırlıklandırır ve
LONG / SHORT / NO_TRADE kararı üretir.

Ağırlıklar:
  OAR Session   30%  ← sistemin özü
  Footprint/VP  20%  ← Kiyotaka VPFR + TPO
  Orderflow     15%  ← CVD, OI, Funding, Taker
  Volume Ind.   10%  ← RSI, MACD, CMF, kompozit
  Options       10%  ← GEX rejimi, CW/PW/ZG konumu
  Macro         10%  ← DXY, VIX, risk-on/off
  Backtest       5%  ← geçmiş sinyal win rate

Karar eşikleri:
  konfidans < 60     → NO_TRADE
  konfidans 60–85    → Normal giriş
  konfidans > 85     → HIGH CONVICTION giriş

Çakışma kuralı:
  Güçlü zıt ajanlar varsa konfidans otomatik düşer.
  Zaman riski yüksekse (FED, Triple Witching) ek ceza uygulanır.
"""

import asyncio, httpx, os
from datetime import datetime, timezone

AGIRLIKLAR = {
    "oar":       0.30,
    "footprint": 0.20,
    "orderflow": 0.15,
    "volume":    0.10,
    "options":   0.10,
    "macro":     0.10,
    "backtest":  0.05,
}

KONFIDANS_ESIKLERI = {"high_conviction": 85, "trade": 60}


# ─────────────────────────────────────────────────────────────────
# ALT AJAN SKORLARI
# ─────────────────────────────────────────────────────────────────

async def _oar_skoru(sembol: str) -> dict:
    try:
        from oar_session_agent import oar_analiz
        return await oar_analiz(sembol)
    except Exception as e:
        return {"skor": 0, "yon": "NEUTRAL", "aciklama": f"OAR hatası: {e}", "guvenis": 0}


async def _footprint_skoru(sembol: str) -> dict:
    """Kiyotaka VPFR + TPO — Volume Profile Agent."""
    try:
        from kiyotaka_engine import get_volume_profile, get_tpo
        import time
        ts = int(time.time()) - 86400
        vp = await get_volume_profile(sembol, ts)
        if vp.get("error"):
            return {"skor": 0, "yon": "NEUTRAL", "aciklama": f"VP: {vp['error']}", "guvenis": 15}

        async with httpx.AsyncClient(timeout=6) as cl:
            r = await cl.get(f"https://api.binance.com/api/v3/ticker/price?symbol={sembol}")
            fiyat = float(r.json()["price"])

        poc, vah, val = vp["poc"], vp["vah"], vp["val"]
        skor = 0
        nedenler = []

        # POC konumu
        if fiyat > poc:
            skor += 30
            nedenler.append(f"Fiyat POC üstünde (POC ${poc:,.0f}) — value içinde yukarı")
        else:
            skor -= 30
            nedenler.append(f"Fiyat POC altında (POC ${poc:,.0f}) — value içinde aşağı")

        # Value Area konumu
        va_aralik = vah - val
        if va_aralik > 0:
            va_pct = (fiyat - val) / va_aralik
            if fiyat > vah:
                skor += 35
                nedenler.append(f"Fiyat VAH üstünde (${vah:,.0f}) — value dışı kabul")
            elif fiyat < val:
                skor -= 35
                nedenler.append(f"Fiyat VAL altında (${val:,.0f}) — value dışı red")
            elif va_pct > 0.65:
                skor += 15
                nedenler.append(f"VA üst bölgesi (%{va_pct*100:.0f}) — alıcı baskısı")
            elif va_pct < 0.35:
                skor -= 15
                nedenler.append(f"VA alt bölgesi (%{va_pct*100:.0f}) — satıcı baskısı")
            else:
                nedenler.append(f"VA orta bölgesi (%{va_pct*100:.0f}) — nötr")

        # TPO
        try:
            tpo = await get_tpo(sembol, ts)
            if not tpo.get("error"):
                if tpo.get("poor_high"):
                    skor += 10
                    nedenler.append(f"Poor High mevcut — yukarı çekim")
                if tpo.get("poor_low"):
                    skor -= 10
                    nedenler.append(f"Poor Low mevcut — aşağı çekim")
                if tpo.get("single_print"):
                    nedenler.append("Single Print var — gap fill hedefi")
        except Exception:
            pass

        skor = max(-100, min(100, skor))
        return {
            "skor": skor,
            "yon": "LONG" if skor > 15 else "SHORT" if skor < -15 else "NEUTRAL",
            "aciklama": " | ".join(nedenler),
            "detay": {"poc": poc, "vah": vah, "val": val, "fiyat": fiyat},
            "guvenis": 80
        }
    except Exception as e:
        return {"skor": 0, "yon": "NEUTRAL", "aciklama": f"Footprint hatası: {e}", "guvenis": 0}


async def _orderflow_skoru(sembol: str) -> dict:
    """Funding, OI trendi, Taker oranı — Orderflow Agent."""
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            fr = await cl.get("https://fapi.binance.com/fapi/v1/fundingRate",
                              params={"symbol": sembol, "limit": 1})
            funding = float(fr.json()[0]["fundingRate"]) if fr.status_code == 200 and fr.json() else 0.0

            oi_r = await cl.get("https://fapi.binance.com/fapi/v1/openInterest",
                                params={"symbol": sembol})
            oi_now = float(oi_r.json()["openInterest"]) if oi_r.status_code == 200 else 0

            oi_hist = await cl.get("https://fapi.binance.com/futures/data/openInterestHist",
                                   params={"symbol": sembol, "period": "1d", "limit": 7})
            oi_vals = [float(x["sumOpenInterest"])
                       for x in (oi_hist.json() if oi_hist.status_code == 200 else [])]

            taker_r = await cl.get("https://fapi.binance.com/futures/data/takerlongshortRatio",
                                   params={"symbol": sembol, "period": "1h", "limit": 4})
            taker_data = taker_r.json() if taker_r.status_code == 200 else []

        skor = 0
        nedenler = []

        # Funding
        if funding < -0.005:
            skor += 30
            nedenler.append(f"Aşırı negatif funding ({funding:.4f}) — short squeeze yakın → LONG")
        elif funding < -0.001:
            skor += 12
            nedenler.append(f"Negatif funding ({funding:.4f}) — short ağır")
        elif funding > 0.005:
            skor -= 30
            nedenler.append(f"Aşırı pozitif funding ({funding:.4f}) — long squeeze yakın → SHORT")
        elif funding > 0.001:
            skor -= 12
            nedenler.append(f"Pozitif funding ({funding:.4f}) — long ağır")
        else:
            nedenler.append(f"Funding nötr ({funding:.4f})")

        # OI değişimi
        if len(oi_vals) >= 2 and oi_vals[0] > 0:
            oi_pct = (oi_vals[-1] - oi_vals[0]) / oi_vals[0] * 100
            if oi_pct > 8:
                skor += 18
                nedenler.append(f"OI %{oi_pct:.1f} arttı — güçlü pozisyon birikimi")
            elif oi_pct > 3:
                skor += 8
                nedenler.append(f"OI %{oi_pct:.1f} arttı — pozisyon artışı")
            elif oi_pct < -8:
                skor -= 18
                nedenler.append(f"OI %{oi_pct:.1f} azaldı — toplu kapatma")
            elif oi_pct < -3:
                skor -= 8
                nedenler.append(f"OI %{oi_pct:.1f} azaldı — pozisyon azalışı")

        # Taker oranı
        if taker_data:
            avg_ratio = sum(float(x.get("buySellRatio", 0.5)) for x in taker_data) / len(taker_data)
            if avg_ratio > 0.58:
                skor += 20
                nedenler.append(f"Taker alıcı dominant (%{avg_ratio*100:.1f})")
            elif avg_ratio > 0.52:
                skor += 8
                nedenler.append(f"Hafif taker alıcı (%{avg_ratio*100:.1f})")
            elif avg_ratio < 0.42:
                skor -= 20
                nedenler.append(f"Taker satıcı dominant (%{avg_ratio*100:.1f})")
            elif avg_ratio < 0.48:
                skor -= 8
                nedenler.append(f"Hafif taker satıcı (%{avg_ratio*100:.1f})")

        skor = max(-100, min(100, skor))
        return {
            "skor": skor,
            "yon": "LONG" if skor > 15 else "SHORT" if skor < -15 else "NEUTRAL",
            "aciklama": " | ".join(nedenler),
            "detay": {"funding": round(funding, 6), "oi_now": round(oi_now, 2)},
            "guvenis": 78
        }
    except Exception as e:
        return {"skor": 0, "yon": "NEUTRAL", "aciklama": f"Orderflow hatası: {e}", "guvenis": 0}


async def _volume_skoru(sembol: str) -> dict:
    """RSI, MACD, CMF, kompozit indikatör skoru."""
    try:
        from indicator_engine import analiz
        ind = await analiz(sembol)
        if ind.get("hata"):
            return {"skor": 0, "yon": "NEUTRAL", "aciklama": ind["hata"], "guvenis": 0}

        skor = 0
        nedenler = []

        rsi = ind.get("RSI", {}).get("deger", 50)
        if rsi > 72:
            skor -= 22
            nedenler.append(f"RSI aşırı alım ({rsi:.1f})")
        elif rsi < 28:
            skor += 22
            nedenler.append(f"RSI aşırı satım ({rsi:.1f})")
        elif rsi > 55:
            skor += 12
            nedenler.append(f"RSI yükseliş bölgesi ({rsi:.1f})")
        elif rsi < 45:
            skor -= 12
            nedenler.append(f"RSI düşüş bölgesi ({rsi:.1f})")
        else:
            nedenler.append(f"RSI nötr ({rsi:.1f})")

        macd_h = ind.get("MACD", {}).get("histogram", 0)
        if macd_h > 0:
            skor += 15
            nedenler.append(f"MACD pozitif histogram ({macd_h:+.5f})")
        elif macd_h < 0:
            skor -= 15
            nedenler.append(f"MACD negatif histogram ({macd_h:+.5f})")

        cmf = ind.get("CMF", {}).get("deger", 0)
        if cmf > 0.05:
            skor += 20
            nedenler.append(f"CMF güçlü para girişi ({cmf:+.3f})")
        elif cmf < -0.05:
            skor -= 20
            nedenler.append(f"CMF güçlü para çıkışı ({cmf:+.3f})")

        komp = ind.get("kompozit_skor_5m", 0)
        if abs(komp) > 20:
            skor += komp * 0.25
            nedenler.append(f"Kompozit skor: {komp:+.0f}")

        skor = max(-100, min(100, skor))
        return {
            "skor": skor,
            "yon": "LONG" if skor > 15 else "SHORT" if skor < -15 else "NEUTRAL",
            "aciklama": " | ".join(nedenler),
            "guvenis": 72
        }
    except Exception as e:
        return {"skor": 0, "yon": "NEUTRAL", "aciklama": f"Volume hatası: {e}", "guvenis": 0}


async def _options_skoru(sembol_kisa: str) -> dict:
    """GEX rejimi, Call/Put Wall, Zero Gamma — Options Agent."""
    try:
        from options_engine import gex_ozet, alarm_levels
        gex, levels = await asyncio.gather(
            gex_ozet(sembol_kisa),
            alarm_levels(sembol_kisa),
            return_exceptions=True
        )

        skor = 0
        nedenler = []

        if isinstance(gex, dict) and not gex.get("error"):
            rejim = (gex.get("gamma_rejim") or "").lower()
            if "pozitif" in rejim:
                skor += 28
                nedenler.append(f"Pozitif GEX: Dealer stabilize eder — düşük vol")
            elif "negatif" in rejim:
                skor -= 28
                nedenler.append(f"Negatif GEX: Dealer volatilite artırır — yüksek vol")

        if isinstance(levels, dict) and not levels.get("error"):
            g = levels.get("genel", levels)
            cw = g.get("call_wall") or g.get("CW")
            pw = g.get("put_wall") or g.get("PW")
            zg = g.get("zero_gamma") or g.get("ZG")

            if cw and zg:
                try:
                    async with httpx.AsyncClient(timeout=5) as cl:
                        r = await cl.get(
                            f"https://api.binance.com/api/v3/ticker/price?symbol={sembol_kisa}USDT")
                        fiyat = float(r.json()["price"])

                    if fiyat > zg:
                        skor += 20
                        nedenler.append(f"Fiyat Zero Gamma üstünde (${zg:,.0f}) — dealer LONG gamma")
                    else:
                        skor -= 20
                        nedenler.append(f"Fiyat Zero Gamma altında (${zg:,.0f}) — dealer SHORT gamma")

                    if cw:
                        mesafe_cw = (cw - fiyat) / fiyat * 100
                        if mesafe_cw < 1.5:
                            skor -= 12
                            nedenler.append(f"Call Wall çok yakın %{mesafe_cw:.1f} (${cw:,.0f}) — güçlü direnç")
                    if pw:
                        mesafe_pw = (fiyat - pw) / fiyat * 100
                        if mesafe_pw < 1.5:
                            skor += 12
                            nedenler.append(f"Put Wall çok yakın %{mesafe_pw:.1f} (${pw:,.0f}) — güçlü destek")

                    nedenler.append(f"CW:${cw:,.0f} | PW:${pw:,.0f} | ZG:${zg:,.0f}")
                except Exception:
                    pass

        skor = max(-100, min(100, skor))
        return {
            "skor": skor,
            "yon": "LONG" if skor > 15 else "SHORT" if skor < -15 else "NEUTRAL",
            "aciklama": " | ".join(nedenler),
            "guvenis": 75
        }
    except Exception as e:
        return {"skor": 0, "yon": "NEUTRAL", "aciklama": f"Options hatası: {e}", "guvenis": 0}


async def _macro_skoru() -> dict:
    """Macro Engine — DXY, VIX, risk-on/off."""
    try:
        from macro_engine import macro_ozet
        macro = await macro_ozet() if asyncio.iscoroutinefunction(
            __import__("macro_engine", fromlist=["macro_ozet"]).macro_ozet
        ) else __import__("macro_engine", fromlist=["macro_ozet"]).macro_ozet()
        if isinstance(macro, dict) and not macro.get("error"):
            skor = 0
            nedenler = []
            vix = macro.get("vix_level") or macro.get("VIX")
            if vix:
                v = float(vix)
                if v > 35:
                    skor -= 28
                    nedenler.append(f"VIX kritik ({v:.1f}) — risk-off panik")
                elif v > 25:
                    skor -= 15
                    nedenler.append(f"VIX yüksek ({v:.1f}) — risk-off")
                elif v < 14:
                    skor += 15
                    nedenler.append(f"VIX düşük ({v:.1f}) — risk-on")
                else:
                    nedenler.append(f"VIX normal ({v:.1f})")
            dxy = macro.get("dxy_change_pct") or macro.get("DXY_change")
            if dxy is not None:
                d = float(dxy)
                if d > 0.4:
                    skor -= 18
                    nedenler.append(f"DXY güçleniyor (%{d:+.2f}) — crypto baskı")
                elif d < -0.4:
                    skor += 18
                    nedenler.append(f"DXY zayıflıyor (%{d:+.2f}) — crypto destek")
            skor = max(-100, min(100, skor))
            return {
                "skor": skor,
                "yon": "LONG" if skor > 15 else "SHORT" if skor < -15 else "NEUTRAL",
                "aciklama": " | ".join(nedenler) if nedenler else "Macro veri bekleniyor",
                "guvenis": 55
            }
    except Exception as e:
        pass
    return {"skor": 0, "yon": "NEUTRAL", "aciklama": "Macro verisi alınamadı", "guvenis": 0}


def _backtest_skoru() -> dict:
    try:
        from leader_agent import backtest_sinyal_analizi
        bt = backtest_sinyal_analizi()
        wr = bt.get("genel_win_rate", 50)
        skor = max(-100, min(100, (wr - 50) * 2))
        return {
            "skor": skor,
            "yon": "LONG" if skor > 10 else "SHORT" if skor < -10 else "NEUTRAL",
            "aciklama": f"Genel WR %{wr:.1f} | {bt.get('toplam_sinyal', 0)} sinyal",
            "guvenis": 60
        }
    except Exception as e:
        return {"skor": 0, "yon": "NEUTRAL", "aciklama": f"Backtest hatası: {e}", "guvenis": 0}


# ─────────────────────────────────────────────────────────────────
# ANA CONFIDENCE ENGINE
# ─────────────────────────────────────────────────────────────────

async def confidence_karar(sembol: str = "BTCUSDT") -> dict:
    """
    Tüm agentları paralel çalıştır → ağırlıklı skor → LONG/SHORT/NO_TRADE.

    Döner:
      karar       : LONG | SHORT | NO_TRADE
      konfidans   : 0-100
      conviction  : HIGH | MEDIUM | LOW
      ham_skor    : -100 ile +100 arası ağırlıklı skor
      agent_skorlar: her ajanın detayı
      zaman_riski : time_context verileri
      tarih       : UTC timestamp
    """
    from time_context import time_risk_skoru

    sembol_kisa = sembol.replace("USDT", "")

    # Tüm agentları paralel çalıştır
    oar, footprint, orderflow, volume, options, macro = await asyncio.gather(
        _oar_skoru(sembol),
        _footprint_skoru(sembol),
        _orderflow_skoru(sembol),
        _volume_skoru(sembol),
        _options_skoru(sembol_kisa),
        _macro_skoru(),
        return_exceptions=True
    )
    time_risk = await time_risk_skoru()
    backtest = _backtest_skoru()

    agent_skorlar = {}
    for ad, sonuc in zip(
        ["oar", "footprint", "orderflow", "volume", "options", "macro"],
        [oar, footprint, orderflow, volume, options, macro]
    ):
        if isinstance(sonuc, Exception):
            agent_skorlar[ad] = {"skor": 0, "yon": "NEUTRAL", "aciklama": str(sonuc), "guvenis": 0}
        else:
            agent_skorlar[ad] = sonuc
    agent_skorlar["backtest"] = backtest

    # Ağırlıklı ortalama (güvenirlik katsayısıyla)
    agirlikli_toplam = 0.0
    toplam_etkin_agirlik = 0.0
    for ad, agirlik in AGIRLIKLAR.items():
        a = agent_skorlar.get(ad, {})
        guven = a.get("guvenis", 50) / 100
        etkin = agirlik * guven
        agirlikli_toplam += a.get("skor", 0) * etkin
        toplam_etkin_agirlik += etkin

    ham_skor = agirlikli_toplam / toplam_etkin_agirlik if toplam_etkin_agirlik > 0 else 0

    # Oy dağılımı
    yonler = [a.get("yon", "NEUTRAL") for a in agent_skorlar.values()]
    long_say = yonler.count("LONG")
    short_say = yonler.count("SHORT")

    # Konfidans hesabı
    temel_konfidans = abs(ham_skor)

    # Çakışma cezası: Karşıt yönlü güçlü oylar
    if long_say > 0 and short_say > 0:
        catisma = min(long_say, short_say) / len(yonler) * 35
        temel_konfidans -= catisma

    # Zaman riski cezası (FED haftası, Triple Witching vb.)
    zaman_cezasi = time_risk.get("risk_skoru", 0) * 0.25
    temel_konfidans -= zaman_cezasi

    konfidans = round(max(0.0, min(100.0, temel_konfidans)), 1)

    # Karar
    if konfidans < KONFIDANS_ESIKLERI["trade"]:
        karar = "NO_TRADE"
        karar_nedeni = (f"Konfidans yetersiz ({konfidans}/100). "
                        f"Çakışma: {long_say}L vs {short_say}S. "
                        f"Zaman riski: {time_risk['seviye']}")
    elif ham_skor >= 0:
        karar = "LONG"
        karar_nedeni = f"Ağırlıklı skor +{ham_skor:.1f} — {long_say}/{len(yonler)} ajan LONG"
    else:
        karar = "SHORT"
        karar_nedeni = f"Ağırlıklı skor {ham_skor:.1f} — {short_say}/{len(yonler)} ajan SHORT"

    conviction = ("HIGH"   if konfidans >= KONFIDANS_ESIKLERI["high_conviction"] else
                  "MEDIUM" if konfidans >= KONFIDANS_ESIKLERI["trade"] else "LOW")

    return {
        "karar": karar,
        "konfidans": konfidans,
        "conviction": conviction,
        "ham_skor": round(ham_skor, 1),
        "karar_nedeni": karar_nedeni,
        "oy_dagilimi": {
            "LONG": long_say,
            "SHORT": short_say,
            "NEUTRAL": yonler.count("NEUTRAL"),
            "toplam": len(yonler)
        },
        "zaman_riski": time_risk,
        "agent_skorlar": {
            ad: {
                "skor": a.get("skor", 0),
                "yon": a.get("yon", "NEUTRAL"),
                "aciklama": a.get("aciklama", "")[:200],
                "agirlik_pct": round(AGIRLIKLAR.get(ad, 0.05) * 100),
                "guvenis": a.get("guvenis", 0)
            }
            for ad, a in agent_skorlar.items()
        },
        "sembol": sembol,
        "tarih": datetime.now(timezone.utc).isoformat()
    }


def karar_ozet_metni(k: dict) -> str:
    """Confidence Engine çıktısını insan okunur özet metne çevir."""
    karar = k.get("karar", "?")
    konfidans = k.get("konfidans", 0)
    conviction = k.get("conviction", "LOW")
    emoji = {"LONG": "📈", "SHORT": "📉", "NO_TRADE": "⏸"}.get(karar, "❓")

    satırlar = [
        f"{emoji} KARAR: {karar}  |  Konfidans: {konfidans}/100  |  Conviction: {conviction}",
        f"Neden: {k.get('karar_nedeni', '')}",
        "",
        "── Agent Skorları ──────────────────────────"
    ]
    for ad, a in k.get("agent_skorlar", {}).items():
        bar_len = abs(int(a["skor"] / 10))
        bar = ("▲" if a["skor"] >= 0 else "▼") * min(bar_len, 10)
        satırlar.append(
            f"  {ad.upper():12s} {a['skor']:+4.0f}  {bar:<10s}  {a['yon']:7s}  "
            f"[%{a['agirlik_pct']} ağırlık]  {a['aciklama'][:80]}"
        )

    zr = k.get("zaman_riski", {})
    if zr.get("aktif_etkinlikler"):
        satırlar.append("")
        satırlar.append(f"⚠ Zaman Riski: {zr['seviye']} ({zr['risk_skoru']}/100)")
        for e in zr["aktif_etkinlikler"][:3]:
            satırlar.append(f"    • {e.get('aciklama', '')}")

    return "\n".join(satırlar)
