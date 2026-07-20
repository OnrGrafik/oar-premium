"""
oar_trend_paper.py — SİSTEM 2: TREND (Kırılım-Devam) Canlı Paper-Trade · $1000 · 5x
═══════════════════════════════════════════════════════════════════════════════════════
Backtest'le doğrulanan 2. şampiyonun (kirilim_devam_trend) canlı forward-testi.
Sistem 1 (fade paper) ile AYNI çerçeve: $1000 başlangıç, 5x, compound, fee dahil.

CANLI KURALLAR (backtest trend adayı üreticisiyle hizalı — tp_sl_breakout birebir):
  • Market kapısı: PER-SEMBOL — her coin KENDİ Asia ≥ %1 (Sistem 1 ile aynı per-sembol kapı).
  • Tetik: post-Asia fiyat Asia-HIGH üstünde → LONG devam · Asia-LOW altında → SHORT devam.
  • TP/SL = tp_sl_breakout birebir: LONG TP=1.377 fib, SL=0.5 (range ortası, geçersizlik);
    SHORT TP=-0.377 fib, SL=0.5.
  • Onay: gun_bias_uyum (şampiyon bloklarından canlıda hesaplanabilir olanı) —
    dünkü günlük yön işlem yönüyle uyumlu olmalı.
  NOT: şampiyonun trapped/poc_taraf blokları tick-veri ister → canlıda birebir değil
  (yaklaşım). Forward-test tam da bu yüzden: canlı hali kendini kanıtlamalı.

Durum dosyası: oar_trend_paper.json (DATA_DIR). Telegram: altcoin ile aynı thread.
"""
import json
import asyncio
from datetime import datetime, timezone

from oar_paper_box import (_net_fiyat_pct, _equity_carpani, _kapanis_kontrol,
                           trade_penceresi_uygun, _sure_saat, _son_hl, _fiyat,
                           _fib_seviyeleri, KALDIRAC, MAX_SAAT, BASLANGIC_BAKIYE)

SEMBOLLER = ["BTCUSDT", "ETHUSDT"]


def _dosya():
    from data_ingest import hist_dir
    return hist_dir() / "oar_trend_paper.json"


def _bos():
    return {"baslangic_bakiye": BASLANGIC_BAKIYE, "bakiye": BASLANGIC_BAKIYE,
            "kaldirac": KALDIRAC, "basladi": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "acik": {}, "islemler": []}


def _yukle():
    yol = _dosya()
    if yol.exists():
        try:
            with open(yol, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return _bos()


def _kaydet(d):
    yol = _dosya()
    yol.parent.mkdir(parents=True, exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


async def _tg(metin):
    try:
        from oar_altcoin_sistem import TG_CHAT, TG_THREAD
        from main import _telegram_gonder
        await _telegram_gonder(metin, thread_id=TG_THREAD, chat_id=TG_CHAT)
    except Exception as e:
        print(f"[OAR-Trend] telegram hatası: {str(e)[:60]}")


async def _gun_bias(sembol):
    """Dünkü günlük yön (LONG/SHORT/None) — gun_bias_uyum bloğunun canlı karşılığı."""
    try:
        from exchange_client import klines as _ec_klines
        k = await _ec_klines(sembol, "1d", 4)
        if len(k) >= 3:
            c1, c2 = float(k[-2][3]), float(k[-3][3])   # dünkü ve önceki kapanış
            return "LONG" if c1 > c2 else "SHORT"
    except Exception:
        pass
    return None


def _ac_karar_trend(analiz, bias):
    """Kırılım-devam kararı: TP/SL = tp_sl_breakout birebir; bias uyumu şart."""
    asia = analiz.get("asia") or {}
    hi, lo = asia.get("high"), asia.get("low")
    fiyat = analiz.get("fiyat")
    if not (hi and lo and fiyat) or hi <= lo:
        return None
    # TP_3R EXIT (kullanıcı onayı, ANAYASA #8): SL=mid (0.5, şampiyonla aynı),
    # TP = giriş ± 3R (R=|giriş−mid|). Analiz: base(TP=1.377) toplam 162 → TP_3R toplam
    # 1467 (~9x), 5x-likidasyon %0, her dönem +, DSR 1.0. Time-stop seans sonu kapatır.
    fibs = _fib_seviyeleri(lo, hi)
    mid = fibs[0.5]
    # KANIT BAĞI (5e): bu işlem serap-geçer TREND şampiyonuna ait — DSR/kanıt iliştir
    # (fail-open: serap dosyası yoksa şampiyonu durdurmaz).
    try:
        from oar_kanit_kapisi import kanit_iliştir
        kanit = kanit_iliştir("kirilim_devam_trend")
    except Exception:
        kanit = {}
    if fiyat > hi:                       # üst kırılım → LONG devam
        if bias != "LONG":
            return None
        sl = mid
        R = fiyat - sl
        tp = fiyat + 3.0 * R
        if not (sl < fiyat < tp):
            return None
        return {"yon": "LONG", "giris": round(fiyat, 2), "tp": round(tp, 2), "sl": round(sl, 2), **kanit}
    if fiyat < lo:                       # alt kırılım → SHORT devam
        if bias != "SHORT":
            return None
        sl = mid
        R = sl - fiyat
        tp = fiyat - 3.0 * R
        if not (tp < fiyat < sl):
            return None
        return {"yon": "SHORT", "giris": round(fiyat, 2), "tp": round(tp, 2), "sl": round(sl, 2), **kanit}
    return None


async def _kapat(d, sembol, cikis, sonuc):
    poz = d["acik"].pop(sembol)
    net = _net_fiyat_pct(poz["yon"], poz["giris"], cikis)
    carpan = _equity_carpani(net)
    onceki = d["bakiye"]
    d["bakiye"] = round(max(0.0, onceki * carpan), 2)
    d["islemler"].append({
        "sembol": sembol, "yon": poz["yon"], "giris": poz["giris"], "cikis": round(cikis, 2),
        "tp": poz["tp"], "sl": poz["sl"], "sonuc": sonuc, "net_fiyat_pct": net,
        "bakiye_sonra": d["bakiye"], "acilis": poz["acilis"],
        "kapanis": datetime.now(timezone.utc).isoformat(),
    })
    d["islemler"] = d["islemler"][-500:]
    emoji = "✅" if carpan > 1 else "❌"
    await _tg(f"⚡ SİSTEM-2 TREND KAPANIŞ — {sembol} {poz['yon']}\n"
              f"{sonuc} @ {round(cikis,2)} · {emoji} equity %{(carpan-1)*100:+.2f} (5x) "
              f"→ bakiye ${d['bakiye']}")
    print(f"[OAR-Trend] KAPANIŞ {sembol} {sonuc} → ${d['bakiye']}")


async def tik(d=None):
    """5 dk'lık adım: açıkları yönet; kapı+pencere uygunsa kırılım-devam aç."""
    from oar_session_agent import oar_analiz, _sembol_fade_gunu
    d = d if d is not None else _yukle()

    # PER-SEMBOL kapı (kullanıcı onaylı, ANAYASA #8): her coin kendi Asia≥%1'inde
    # açar (döngü içinde). Eski iki-kapı ETH getirisini ~yarıya indiriyordu.
    for sembol in SEMBOLLER:
        if sembol in d["acik"]:
            poz = d["acik"][sembol]
            high, low = await _son_hl(sembol)
            kap = _kapanis_kontrol(poz, high, low)
            if kap:
                await _kapat(d, sembol, kap[1], kap[0])
            elif _sure_saat(poz["acilis"]) >= MAX_SAAT:
                await _kapat(d, sembol, (high + low) / 2, "TIME_STOP")
            continue
        sfade, _spct = await _sembol_fade_gunu(sembol)   # per-sembol kapı
        if d["bakiye"] <= 0 or sfade is not True or not trade_penceresi_uygun():
            continue
        try:
            analiz = await oar_analiz(sembol)
        except Exception:
            continue
        bias = await _gun_bias(sembol)
        karar = _ac_karar_trend(analiz, bias)
        if karar:
            karar["acilis"] = datetime.now(timezone.utc).isoformat()
            d["acik"][sembol] = karar
            await _tg(f"⚡ SİSTEM-2 TREND GİRİŞ — {sembol} {karar['yon']}\n"
                      f"Giriş: {karar['giris']} · TP: {karar['tp']} · SL: {karar['sl']}\n"
                      f"Asia kırılım-devam + gün-bias uyumu (backtest-doğrulanmış stil)")
            print(f"[OAR-Trend] GİRİŞ {sembol} {karar['yon']} @ {karar['giris']}")

    _kaydet(d)
    return d


async def dongu(interval: int = 300):
    await asyncio.sleep(45)
    while True:
        try:
            await tik()
        except Exception as e:
            print(f"[OAR-Trend] döngü hatası: {str(e)[:80]}")
        await asyncio.sleep(interval)


def durum_ozet() -> dict:
    d = _yukle()
    return {"bakiye": d.get("bakiye"), "baslangic": d.get("baslangic_bakiye"),
            "kaldirac": d.get("kaldirac"), "basladi": d.get("basladi"),
            "acik": d.get("acik", {}), "islem_sayisi": len(d.get("islemler", [])),
            "son_islemler": list(reversed(d.get("islemler", [])))[:30]}
