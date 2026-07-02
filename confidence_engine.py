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

_AGIRLIKLAR_VARSAYILAN = {
    "oar":       0.30,
    "footprint": 0.20,
    "orderflow": 0.15,
    "volume":    0.10,
    "options":   0.10,
    "macro":     0.10,
    "backtest":  0.05,
}

def _etkin_agirliklar() -> dict:
    """Learning engine varsa öğrenilmiş ağırlıkları kullan, yoksa varsayılan."""
    try:
        from learning_engine import agirliklar_al
        return agirliklar_al()
    except Exception:
        return dict(_AGIRLIKLAR_VARSAYILAN)

# Modül seviyesinde kısa isim — geriye dönük uyum için
AGIRLIKLAR = _AGIRLIKLAR_VARSAYILAN

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
        funding = None; oi_now = 0; oi_vals = []; taker_data = []
        async with httpx.AsyncClient(timeout=10) as cl:
            # Güncel funding — premiumIndex.lastFundingRate (en doğru anlık değer)
            try:
                pr = await cl.get("https://fapi.binance.com/fapi/v1/premiumIndex",
                                  params={"symbol": sembol})
                if pr.status_code == 200:
                    funding = float(pr.json().get("lastFundingRate"))
            except Exception:
                pass

            try:
                oi_r = await cl.get("https://fapi.binance.com/fapi/v1/openInterest",
                                    params={"symbol": sembol})
                if oi_r.status_code == 200:
                    oi_now = float(oi_r.json().get("openInterest", 0))
            except Exception:
                pass

            try:
                oi_hist = await cl.get("https://fapi.binance.com/futures/data/openInterestHist",
                                       params={"symbol": sembol, "period": "1d", "limit": 7})
                if oi_hist.status_code == 200:
                    oi_vals = [float(x["sumOpenInterest"]) for x in oi_hist.json()]
            except Exception:
                pass

            try:
                taker_r = await cl.get("https://fapi.binance.com/futures/data/takerlongshortRatio",
                                       params={"symbol": sembol, "period": "1h", "limit": 4})
                if taker_r.status_code == 200:
                    taker_data = taker_r.json()
            except Exception:
                pass

        skor = 0
        nedenler = []

        # Funding — % olarak gösterilir (her zaman okunan değer yazılır)
        if funding is not None:
            fpct = funding * 100  # oran → yüzde
            if funding < -0.005:
                skor += 30; nedenler.append(f"Aşırı negatif funding (%{fpct:+.4f}) — short squeeze yakın → LONG")
            elif funding < -0.001:
                skor += 12; nedenler.append(f"Negatif funding (%{fpct:+.4f}) — short ağır")
            elif funding > 0.005:
                skor -= 30; nedenler.append(f"Aşırı pozitif funding (%{fpct:+.4f}) — long squeeze yakın → SHORT")
            elif funding > 0.001:
                skor -= 12; nedenler.append(f"Pozitif funding (%{fpct:+.4f}) — long ağır")
            else:
                nedenler.append(f"Funding nötr (%{fpct:+.4f})")

        # OI değişimi — geçmiş seri varsa değişim, yoksa anlık değeri raporla
        if len(oi_vals) >= 2 and oi_vals[0] > 0:
            oi_pct = (oi_vals[-1] - oi_vals[0]) / oi_vals[0] * 100
            if oi_pct > 8:
                skor += 18; nedenler.append(f"OI %{oi_pct:+.1f} (7g) — güçlü pozisyon birikimi")
            elif oi_pct > 3:
                skor += 8; nedenler.append(f"OI %{oi_pct:+.1f} (7g) — pozisyon artışı")
            elif oi_pct < -8:
                skor -= 18; nedenler.append(f"OI %{oi_pct:+.1f} (7g) — toplu kapatma")
            elif oi_pct < -3:
                skor -= 8; nedenler.append(f"OI %{oi_pct:+.1f} (7g) — pozisyon azalışı")
            else:
                nedenler.append(f"OI yatay (%{oi_pct:+.1f} 7g)")
        elif oi_now:
            nedenler.append(f"OI: {oi_now:,.0f} (geçmiş seri yok)")

        # Taker oranı — buySellRatio = buyVol/sellVol (1.0 merkezli oran).
        # Alıcı payına çevir: buy_pct = ratio/(1+ratio)*100
        if taker_data:
            avg_ratio = sum(float(x.get("buySellRatio", 1.0)) for x in taker_data) / len(taker_data)
            buy_pct = avg_ratio / (1 + avg_ratio) * 100 if avg_ratio > 0 else 50.0
            if buy_pct > 56:
                skor += 20; nedenler.append(f"Taker alıcı dominant (alış %{buy_pct:.1f})")
            elif buy_pct > 52:
                skor += 8; nedenler.append(f"Hafif taker alıcı (alış %{buy_pct:.1f})")
            elif buy_pct < 44:
                skor -= 20; nedenler.append(f"Taker satıcı dominant (alış %{buy_pct:.1f})")
            elif buy_pct < 48:
                skor -= 8; nedenler.append(f"Hafif taker satıcı (alış %{buy_pct:.1f})")
            else:
                nedenler.append(f"Taker dengeli (alış %{buy_pct:.1f})")

        if not nedenler:
            return {"skor": 0, "yon": "NEUTRAL", "aciklama": "Orderflow verisi alınamadı (Binance fapi)", "guvenis": 0}

        skor = max(-100, min(100, int(skor)))
        return {
            "skor": skor,
            "yon": "LONG" if skor > 15 else "SHORT" if skor < -15 else "NEUTRAL",
            "aciklama": " | ".join(nedenler),
            "detay": {"funding": round(funding, 6) if funding is not None else None,
                      "oi_now": round(oi_now, 2)},
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

        # indicator_engine göstergeleri "indikatorler" altında, kompozit "skor.skor"
        inds = ind.get("indikatorler", {})
        skor = 0
        nedenler = []

        rsi = inds.get("RSI", {}).get("deger")
        if rsi is not None:
            if rsi > 72:
                skor -= 22; nedenler.append(f"RSI aşırı alım ({rsi:.1f})")
            elif rsi < 28:
                skor += 22; nedenler.append(f"RSI aşırı satım ({rsi:.1f})")
            elif rsi > 55:
                skor += 12; nedenler.append(f"RSI yükseliş bölgesi ({rsi:.1f})")
            elif rsi < 45:
                skor -= 12; nedenler.append(f"RSI düşüş bölgesi ({rsi:.1f})")
            else:
                nedenler.append(f"RSI nötr ({rsi:.1f})")

        macd_h = inds.get("MACD", {}).get("hist")
        if macd_h is not None:
            if macd_h > 0:
                skor += 15; nedenler.append(f"MACD pozitif histogram ({macd_h:+.5f})")
            elif macd_h < 0:
                skor -= 15; nedenler.append(f"MACD negatif histogram ({macd_h:+.5f})")

        cmf = inds.get("CMF", {}).get("deger")
        if cmf is not None:
            if cmf > 0.05:
                skor += 20; nedenler.append(f"CMF güçlü para girişi ({cmf:+.3f})")
            elif cmf < -0.05:
                skor -= 20; nedenler.append(f"CMF güçlü para çıkışı ({cmf:+.3f})")

        # indicator_engine'in kendi kompozit 5m skoru (en güvenilir özet)
        komp = ind.get("skor", {}).get("skor")
        if komp is not None and abs(komp) > 20:
            skor += komp * 0.25
            nedenler.append(f"Kompozit 5m skor: {komp:+.0f} ({ind.get('skor',{}).get('yon','')})")

        if not nedenler:
            return {"skor": 0, "yon": "NEUTRAL", "aciklama": "İndikatör verisi boş", "guvenis": 0}

        skor = max(-100, min(100, int(skor)))
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

                    mesafe_zg = abs(fiyat - zg) / fiyat * 100
                    if mesafe_zg < 0.7:
                        # ZG sınırı: pozitif↔negatif gamma geçiş bölgesi — rejim
                        # DEĞİŞİKLİĞİ/kırılım riski (Hull §19: zero gamma = flip).
                        # Yön baskısı verme, kırılım uyarısı ver.
                        nedenler.append(
                            f"⚠ ZG SINIRI %{mesafe_zg:.1f} (${zg:,.0f}) — gamma rejim "
                            f"DEĞİŞİMİ/kırılım bölgesi: yön belirsiz, volatilite genişleyebilir")
                    elif fiyat > zg:
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

        # ATM IV from near-term options for volatility engine
        iv_pct = None
        try:
            from options_engine import _zincir
            import httpx as _hx
            async with _hx.AsyncClient(timeout=20) as _cl:
                _spot, _opts = await _zincir(_cl, sembol_kisa)
            if _opts and _spot:
                import math as _math
                _now = int(__import__("time").time() * 1000)
                _near_ts = min(o["expiryTs"] for o in _opts)
                _near = [o for o in _opts if o["expiryTs"] == _near_ts]
                _atm = min(_near, key=lambda o: abs(o["strike"] - _spot))
                _iv = _atm.get("iv", 0)
                if _iv > 0:
                    iv_pct = round(_iv * 100, 1)
        except Exception:
            pass

        skor = max(-100, min(100, skor))
        return {
            "skor": skor,
            "yon": "LONG" if skor > 15 else "SHORT" if skor < -15 else "NEUTRAL",
            "aciklama": " | ".join(nedenler),
            "guvenis": 75,
            "iv_pct": iv_pct,
        }
    except Exception as e:
        return {"skor": 0, "yon": "NEUTRAL", "aciklama": f"Options hatası: {e}", "guvenis": 0}


async def _macro_skoru() -> dict:
    """Macro Engine — DXY, VIX, risk-on/off."""
    try:
        from macro_engine import carry_trade, makro_veri
        carry, makro = await asyncio.gather(
            carry_trade(), makro_veri(), return_exceptions=True)

        skor = 0
        nedenler = []

        # VIX + carry unwind (Yahoo Finance — carry_trade)
        if isinstance(carry, dict) and carry.get("gostergeler"):
            g = carry["gostergeler"]
            vix = (g.get("vix") or {}).get("fiyat")
            if vix is not None:
                v = float(vix)
                if v > 35:
                    skor -= 28; nedenler.append(f"VIX kritik ({v:.1f}) — risk-off panik")
                elif v > 25:
                    skor -= 15; nedenler.append(f"VIX yüksek ({v:.1f}) — risk-off")
                elif v < 14:
                    skor += 15; nedenler.append(f"VIX düşük ({v:.1f}) — risk-on")
                else:
                    nedenler.append(f"VIX normal ({v:.1f})")
            if carry.get("risk") == "YÜKSEK":
                skor -= 18
                nedenler.append(f"Carry unwind riski YÜKSEK ({carry.get('unwind_sinyalleri','?')}/5)")
            usdjpy_chg = (g.get("usdjpy") or {}).get("chg")
            if usdjpy_chg is not None and float(usdjpy_chg) < -0.5:
                skor -= 8
                nedenler.append(f"USD/JPY düşüyor (%{usdjpy_chg}) — JPY güçleniyor, carry baskı")

        # Makro econ tablo eğilimi (CPI/NFP/PPI/faiz — makro_veri)
        if isinstance(makro, dict) and makro.get("egilim"):
            eg = makro["egilim"]
            if "POZİTİF" in eg:
                skor += 15; nedenler.append(f"Makro tablo POZİTİF ({makro.get('olumlu','?')} destek)")
            elif "NEGATİF" in eg:
                skor -= 15; nedenler.append(f"Makro tablo NEGATİF ({makro.get('olumsuz','?')} baskı)")
            else:
                nedenler.append(f"Makro nötr ({makro.get('olumlu','?')}+/{makro.get('olumsuz','?')}-)")

        if not nedenler:
            return {"skor": 0, "yon": "NEUTRAL", "aciklama": "Macro verisi alınamadı", "guvenis": 0}

        skor = max(-100, min(100, int(skor)))
        return {
            "skor": skor,
            "yon": "LONG" if skor > 15 else "SHORT" if skor < -15 else "NEUTRAL",
            "aciklama": " | ".join(nedenler),
            "guvenis": 60
        }
    except Exception as e:
        return {"skor": 0, "yon": "NEUTRAL", "aciklama": f"Macro hatası: {str(e)[:80]}", "guvenis": 0}


def _backtest_skoru() -> dict:
    # Önce forward test (paper trade) varsa onu kullan — gerçek veri
    try:
        from learning_engine import backtest_guven_skoru
        sonuc = backtest_guven_skoru()
        if sonuc.get("guvenis", 0) > 0:
            return sonuc
    except Exception:
        pass
    # Yoksa eski sinyal log bazlı backtest
    try:
        from leader_agent import backtest_sinyal_analizi
        bt = backtest_sinyal_analizi()
        wr = bt.get("genel_win_rate", 50)
        skor = max(-100, min(100, (wr - 50) * 2))
        return {
            "skor": skor,
            "yon": "LONG" if skor > 10 else "SHORT" if skor < -10 else "NEUTRAL",
            "aciklama": f"Genel WR %{wr:.1f} | {bt.get('toplam_sinyal', 0)} sinyal",
            "guvenis": 40   # log bazlı backtest daha az güvenilir
        }
    except Exception as e:
        return {"skor": 0, "yon": "NEUTRAL", "aciklama": f"Backtest hatası: {e}", "guvenis": 0}


# ─────────────────────────────────────────────────────────────────
# ANA CONFIDENCE ENGINE
# ─────────────────────────────────────────────────────────────────

_KARAR_CACHE = {}          # sembol -> (ts, karar) — kısa TTL, tek kaynak
_KARAR_TTL = 90            # sn: chat/panel/memory/çakışan aynı kararı görsün


async def confidence_karar(sembol: str = "BTCUSDT", refresh: bool = False) -> dict:
    """
    TTL-cache'li tek kaynak: 90 sn içinde tüm çağıranlar (chat, panel, memory,
    çakışan detay) AYNI kararı alır → konfidans çakışması olmaz.
    """
    import time as _t
    c = _KARAR_CACHE.get(sembol)
    if not refresh and c and (_t.time() - c[0]) < _KARAR_TTL:
        return c[1]
    karar = await _confidence_karar_ic(sembol)
    _KARAR_CACHE[sembol] = (_t.time(), karar)
    return karar


async def _confidence_karar_ic(sembol: str = "BTCUSDT") -> dict:
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
    from regime_engine import rejim_tespit

    sembol_kisa = sembol.replace("USDT", "")

    # Tüm agentları + rejimi paralel çalıştır
    oar, footprint, orderflow, volume, options, macro, rejim = await asyncio.gather(
        _oar_skoru(sembol),
        _footprint_skoru(sembol),
        _orderflow_skoru(sembol),
        _volume_skoru(sembol),
        _options_skoru(sembol_kisa),
        _macro_skoru(),
        rejim_tespit(sembol),
        return_exceptions=True
    )
    if isinstance(rejim, Exception):
        rejim = {"rejim": "UNKNOWN", "guvenis": 0, "aciklama": str(rejim), "oar_uyari": ""}
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

    # Ağırlıklı ortalama — güven=0 olan (veri yok) agentler hariç tutulur
    # Böylece 3 SHORT + 4 nötr/veri-yok → nötrler seyreltmez, SHORT kazanır
    _agirliklar = _etkin_agirliklar()   # öğrenilmiş veya varsayılan
    agirlikli_toplam = 0.0
    toplam_etkin_agirlik = 0.0
    for ad, agirlik in _agirliklar.items():
        a = agent_skorlar.get(ad, {})
        guven = a.get("guvenis", 0)
        if guven == 0:
            continue  # veri yok → ağırlıktan çıkar
        etkin = agirlik * (guven / 100)
        agirlikli_toplam += a.get("skor", 0) * etkin
        toplam_etkin_agirlik += etkin

    ham_skor = agirlikli_toplam / toplam_etkin_agirlik if toplam_etkin_agirlik > 0 else 0

    # Oy dağılımı
    yonler = [a.get("yon", "NEUTRAL") for a in agent_skorlar.values()]
    long_say = yonler.count("LONG")
    short_say = yonler.count("SHORT")

    # ─── Çakışma analizi (Conflict Detection) ────────────────────
    catismalar = []
    agent_liste = list(agent_skorlar.items())
    for i in range(len(agent_liste)):
        for j in range(i+1, len(agent_liste)):
            ad1, a1 = agent_liste[i]
            ad2, a2 = agent_liste[j]
            s1, s2 = a1.get("skor", 0), a2.get("skor", 0)
            g1, g2 = a1.get("guvenis", 0), a2.get("guvenis", 0)
            # İkisi de verisi olan ama zıt yönlü güçlü agentlar
            if g1 > 30 and g2 > 30 and abs(s1) > 15 and abs(s2) > 15:
                if (s1 > 0) != (s2 > 0):
                    siddet = "YÜKSEK" if abs(s1-s2) > 60 else "ORTA"
                    catismalar.append({
                        "agent1": ad1, "skor1": round(s1),
                        "agent2": ad2, "skor2": round(s2),
                        "siddet": siddet,
                        "aciklama": f"{ad1.upper()} {'LONG' if s1>0 else 'SHORT'} ({s1:+.0f}) ↔ "
                                    f"{ad2.upper()} {'LONG' if s2>0 else 'SHORT'} ({s2:+.0f})"
                    })

    # Çakışma cezası: Güçlü zıt oylar konfidansı düşürür
    catisma_cezasi = 0.0
    if long_say > 0 and short_say > 0:
        catisma_cezasi = min(long_say, short_say) / len(yonler) * 30
        catisma_cezasi += len(catismalar) * 3  # her güçlü çakışma +3 ceza

    # ─── Volatility Engine ───────────────────────────────────────
    # Options agentından IV verisi varsa expected move hesapla
    vol_analiz = {}
    try:
        iv_pct = options.get("iv_pct") if isinstance(options, dict) else None
        atr_pct = rejim.get("atr_pct", 0) if isinstance(rejim, dict) else 0

        if iv_pct and iv_pct > 0:
            # Beklenen günlük hareket: IV_yıllık / sqrt(365)
            expected_gunluk = iv_pct / (365 ** 0.5)
            rv = rejim.get("realized_vol_pct", 0) if isinstance(rejim, dict) else 0
            rv_gunluk = rv / (365 ** 0.5) if rv > 0 else expected_gunluk

            vol_analiz = {
                "iv_pct":           round(iv_pct, 1),
                "expected_move_pct": round(expected_gunluk, 2),
                "realized_vol_pct": round(rv, 1),
                "vol_oran":         round(iv_pct / rv, 2) if rv > 0 else None,
                "durum": (
                    "IV çok yüksek — premium sat fırsatı"   if iv_pct > rv * 1.5 else
                    "IV düşük — trend hareketi yakın olabilir" if iv_pct < rv * 0.7 else
                    "IV/RV dengeli"
                )
            }
    except Exception:
        pass

    # ─── Rejim bazlı konfidans ayarlaması ────────────────────────
    rejim_adi = rejim.get("rejim", "UNKNOWN") if isinstance(rejim, dict) else "UNKNOWN"
    rejim_cezasi = 0.0
    if rejim_adi == "RANGE":
        # Range'de breakout ihtimali düşer
        rejim_cezasi = 8.0
    elif rejim_adi == "HIGH_VOL":
        rejim_cezasi = 5.0
    elif rejim_adi == "PANIC":
        # Panik'te sinyaller güvenilmez, ceza büyük
        rejim_cezasi = 15.0

    # Konfidans hesabı
    temel_konfidans = abs(ham_skor)
    temel_konfidans -= catisma_cezasi
    temel_konfidans -= rejim_cezasi

    # Zaman riski cezası (FED haftası, Triple Witching vb.)
    zaman_cezasi = time_risk.get("risk_skoru", 0) * 0.25
    temel_konfidans -= zaman_cezasi

    konfidans = round(max(0.0, min(100.0, temel_konfidans)), 1)

    # Karar
    if konfidans < KONFIDANS_ESIKLERI["trade"]:
        nedensler = [f"Konfidans yetersiz ({konfidans}/100)"]
        if catisma_cezasi > 0:
            nedensler.append(f"Çakışma cezası -{catisma_cezasi:.0f}")
        if rejim_cezasi > 0:
            nedensler.append(f"Rejim ({rejim_adi}) cezası -{rejim_cezasi:.0f}")
        karar = "NO_TRADE"
        karar_nedeni = ". ".join(nedensler)
    elif ham_skor >= 0:
        karar = "LONG"
        karar_nedeni = (f"Ağırlıklı skor +{ham_skor:.1f} — {long_say}/{len(yonler)} ajan LONG. "
                        f"Rejim: {rejim_adi}")
    else:
        karar = "SHORT"
        karar_nedeni = (f"Ağırlıklı skor {ham_skor:.1f} — {short_say}/{len(yonler)} ajan SHORT. "
                        f"Rejim: {rejim_adi}")

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
        "catismalar": catismalar,
        "rejim": rejim if isinstance(rejim, dict) else {},
        "volatilite": vol_analiz,
        "zaman_riski": time_risk,
        "agent_skorlar": {
            ad: {
                "skor": a.get("skor", 0),
                "yon": a.get("yon", "NEUTRAL"),
                "aciklama": a.get("aciklama", "")[:200],
                "agirlik_pct": round(_agirliklar.get(ad, 0.05) * 100),
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

    # Rejim
    rj = k.get("rejim", {})
    if rj.get("rejim") and rj["rejim"] != "UNKNOWN":
        emojiler = {"TREND_UP": "📈", "TREND_DOWN": "📉", "RANGE": "↔️", "HIGH_VOL": "⚡", "PANIC": "🔥"}
        re = emojiler.get(rj["rejim"], "")
        satırlar.append("")
        satırlar.append(f"{re} Rejim: {rj['rejim']}  ATR%{rj.get('atr_pct',0)}  RSI {rj.get('rsi',0)}")
        if rj.get("oar_uyari"):
            satırlar.append(f"   ⚑ {rj['oar_uyari']}")

    # Volatilite
    vl = k.get("volatilite", {})
    if vl.get("iv_pct"):
        satırlar.append(f"⚡ Vol: IV %{vl['iv_pct']} | Beklenen günlük ±%{vl.get('expected_move_pct',0)} | {vl.get('durum','')}")

    # Çakışmalar
    catismalar = k.get("catismalar", [])
    if catismalar:
        satırlar.append("")
        satırlar.append(f"⚔ Çakışan Sinyaller ({len(catismalar)}):")
        for c in catismalar[:3]:
            satırlar.append(f"   [{c['siddet']}] {c['aciklama']}")

    # Zaman riski
    zr = k.get("zaman_riski", {})
    if zr.get("aktif_etkinlikler"):
        satırlar.append("")
        satırlar.append(f"⚠ Zaman Riski: {zr['seviye']} ({zr['risk_skoru']}/100)")
        for e in zr["aktif_etkinlikler"][:3]:
            satırlar.append(f"    • {e.get('aciklama', '')}")

    return "\n".join(satırlar)
