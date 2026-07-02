"""
OAR Paper-Trade Kutusu — OAR Premium
═══════════════════════════════════════════════════════════════════════════════
Backtest-kanıtlı OAR sistemini (OAR-CORE: poc_taraf + absorpsiyon + reclaim)
CANLI forward-test eder. Sadece BTCUSDT + ETHUSDT. $1000 başlangıç, 5x kaldıraç,
compound. Her işlem hafızaya kaydedilir; kutu o AYIN işlemlerini + güncel bakiyeyi
gösterir. "Bugün başlıyoruz" — durum diske (Railway volume) yazılır, kalıcıdır.

Disiplin:
  • Giriş YALNIZ OAR-CORE confluence sinyalinde (oar_session_agent).
  • TP = Asia POC (range ortası), SL = süpürülen ekstrem (Asia high/low) + tampon.
  • Maliyet: round-trip %0.13 (fee+slippage) fiyat hareketinden düşülür.
  • Time-stop: MAX_SAAT sonra zorla kapanır (OAR gün-içi geçerlilik).
  • Likidasyon: 5x'te ters %20 → bakiye sıfır (SL bunu önler ama korunur).
"""
import asyncio
import json
from datetime import datetime, timezone

from exchange_client import klines as _ec_klines, ticker_price as _ticker

SEMBOLLER = ["BTCUSDT", "ETHUSDT"]
BASLANGIC_BAKIYE = 1000.0
KALDIRAC = 5
FEE_PCT = 0.13          # round-trip fee+slippage (fiyat %)
MAX_SAAT = 18           # time-stop (OAR gün-içi geçerlilik ~NY close)


def _dosya():
    from data_ingest import hist_dir
    return hist_dir() / "oar_paper_box.json"


def _bos_durum():
    return {
        "baslangic_bakiye": BASLANGIC_BAKIYE,
        "bakiye": BASLANGIC_BAKIYE,
        "kaldirac": KALDIRAC,
        "basladi": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "ay": datetime.now(timezone.utc).strftime("%Y-%m"),
        "acik": {},          # sembol -> pozisyon
        "islemler": [],      # kapanan işlemler (tüm geçmiş; kutu ay'a göre süzer)
    }


def _yukle():
    yol = _dosya()
    if yol.exists():
        try:
            with open(yol, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return _bos_durum()


def _kaydet(durum):
    yol = _dosya()
    yol.parent.mkdir(parents=True, exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(durum, f, ensure_ascii=False, indent=2)


# ─── Saf hesap fonksiyonları (test edilebilir) ───────────────────────────────
def _net_fiyat_pct(yon: str, giris: float, cikis: float) -> float:
    """Fiyat hareketi net % (round-trip fee düşülmüş). Kaldıraç DAHİL DEĞİL."""
    gross = (giris - cikis) / giris * 100 if yon == "SHORT" else (cikis - giris) / giris * 100
    return round(gross - FEE_PCT, 4)


def _equity_carpani(net_fiyat_pct: float, kaldirac: int = KALDIRAC) -> float:
    """Equity getirisi = kaldıraç × fiyat hareketi. ≤ -100% → likidasyon (0)."""
    carpan = 1.0 + kaldirac * net_fiyat_pct / 100.0
    return max(0.0, carpan)


def _ac_karar(analiz: dict) -> dict | None:
    """OAR-CORE confluence varsa pozisyon parametreleri (yon/giris/tp/sl), yoksa None."""
    setups = analiz.get("setup_listesi") or []
    core = [s for s in setups if "OAR-CORE" in s]
    if not core:
        return None
    yon = analiz.get("yon")
    if yon not in ("LONG", "SHORT"):
        yon = "LONG" if "LONG" in core[0] else "SHORT" if "SHORT" in core[0] else None
    if yon not in ("LONG", "SHORT"):
        return None
    asia = analiz.get("asia") or {}
    poc, hi, lo = asia.get("poc"), asia.get("high"), asia.get("low")
    fiyat = analiz.get("fiyat")
    if not (poc and hi and lo and fiyat):
        return None
    if yon == "SHORT":
        tp, sl = poc, hi * 1.002
        if not (tp < fiyat < sl):
            return None
    else:
        tp, sl = poc, lo * 0.998
        if not (sl < fiyat < tp):
            return None
    return {"yon": yon, "giris": round(fiyat, 2), "tp": round(tp, 2), "sl": round(sl, 2)}


def _kapanis_kontrol(poz: dict, high: float, low: float):
    """Pozisyon TP/SL vurdu mu (5m H/L ile). Döner: (sonuc, cikis) ya da None."""
    yon, tp, sl = poz["yon"], poz["tp"], poz["sl"]
    if yon == "SHORT":
        if high >= sl:
            return ("SL", sl)
        if low <= tp:
            return ("TP", tp)
    else:
        if low <= sl:
            return ("SL", sl)
        if high >= tp:
            return ("TP", tp)
    return None


def trade_penceresi_uygun(dt=None) -> bool:
    """
    CBDR başlangıcı (TR 23:00 = UTC 20:00) ile Asia oluşumu (TR 07:00 = UTC 04:00)
    ARASI YENİ işlem AÇILMAZ — range henüz oluşuyor. Döner: True=açılabilir.
    Yasak pencere (UTC): [20:00, 24:00) ∪ [00:00, 04:00). Kapatma bu kuraldan muaf.
    """
    dt = dt or datetime.now(timezone.utc)
    h = dt.hour + dt.minute / 60.0
    return not (h >= 20.0 or h < 4.0)


def _sure_saat(acilis_iso: str) -> float:
    try:
        a = datetime.fromisoformat(acilis_iso)
        if a.tzinfo is None:
            a = a.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - a).total_seconds() / 3600
    except Exception:
        return 0.0


# ─── Canlı veri ──────────────────────────────────────────────────────────────
async def _fiyat(sembol):
    return await _ticker(sembol, futures=False)


async def _son_hl(sembol):
    try:
        rows = await _ec_klines(sembol, "5m", 1, futures=False)
        if rows:
            return rows[-1][2], rows[-1][3]
    except Exception:
        pass
    f = await _fiyat(sembol)
    return f, f


def _ay_kontrol(durum):
    """Ay değiştiyse 'basladi' korunur; islemler ay etiketiyle saklanır (süzme görselde)."""
    su_an = datetime.now(timezone.utc).strftime("%Y-%m")
    durum["ay"] = su_an
    return durum


async def _kapat(durum, sembol, cikis, sonuc):
    poz = durum["acik"].pop(sembol)
    net_pct = _net_fiyat_pct(poz["yon"], poz["giris"], cikis)
    carpan = _equity_carpani(net_pct, durum.get("kaldirac", KALDIRAC))
    onceki = durum["bakiye"]
    durum["bakiye"] = round(onceki * carpan, 2)
    pnl_usd = round(durum["bakiye"] - onceki, 2)
    durum["islemler"].append({
        "sembol": sembol, "yon": poz["yon"], "giris": poz["giris"], "cikis": round(cikis, 2),
        "tp": poz["tp"], "sl": poz["sl"], "sonuc": sonuc,
        "net_fiyat_pct": net_pct, "kaldirac": durum.get("kaldirac", KALDIRAC),
        "equity_pct": round((carpan - 1) * 100, 3), "pnl_usd": pnl_usd,
        "acilis": poz["acilis"], "kapanis": datetime.now(timezone.utc).isoformat(),
        "bakiye_sonra": durum["bakiye"], "ay": poz["acilis"][:7],
    })
    print(f"[OAR-Paper] {sembol} {poz['yon']} kapandı: {sonuc} @ {cikis:.2f} "
          f"(equity %{(carpan-1)*100:.2f}, ${pnl_usd}) → bakiye ${durum['bakiye']}")


async def tik(durum=None):
    """Bir döngü adımı: açıkları kontrol/kapat, yeni OAR-CORE sinyali varsa aç."""
    from oar_session_agent import oar_analiz
    durum = durum if durum is not None else _yukle()
    _ay_kontrol(durum)

    for sembol in SEMBOLLER:
        # 1) Açık pozisyon → TP/SL/time-stop kontrolü
        if sembol in durum["acik"]:
            poz = durum["acik"][sembol]
            high, low = await _son_hl(sembol)
            kap = _kapanis_kontrol(poz, high, low)
            if kap:
                await _kapat(durum, sembol, kap[1], kap[0])
            elif _sure_saat(poz["acilis"]) >= MAX_SAAT:
                await _kapat(durum, sembol, (high + low) / 2, "TIME_STOP")
            continue   # aynı turda yeni pozisyon açma

        # 2) Açık yok → OAR-CORE sinyali var mı?
        if durum["bakiye"] <= 0:
            continue
        try:
            analiz = await oar_analiz(sembol)
        except Exception:
            continue
        # CBDR–Asia penceresinde (TR 23:00–07:00) yeni işlem açılmaz
        if not trade_penceresi_uygun():
            continue
        karar = _ac_karar(analiz)
        if karar:
            karar["acilis"] = datetime.now(timezone.utc).isoformat()
            durum["acik"][sembol] = karar
            print(f"[OAR-Paper] {sembol} {karar['yon']} açıldı @ {karar['giris']} "
                  f"(TP {karar['tp']} / SL {karar['sl']})")

    # Günlük/haftalık/aylık rapor + aylık hafıza temizliği
    try:
        from oar_rapor import kontrol_ve_gonder
        await kontrol_ve_gonder(durum, "OAR BTC/ETH", _tg)
    except Exception as e:
        print(f"[OAR-Paper] rapor hatası: {str(e)[:60]}")

    _kaydet(durum)
    return durum


async def _tg(metin):
    try:
        from oar_altcoin_sistem import TG_CHAT, TG_THREAD
        from main import _telegram_gonder
        await _telegram_gonder(metin, thread_id=TG_THREAD, chat_id=TG_CHAT)
    except Exception as e:
        print(f"[OAR-Paper] telegram hatası: {str(e)[:60]}")


async def dongu(interval: int = 300):
    """Arka plan döngüsü (main.py startup). 5 dakikada bir tik."""
    await asyncio.sleep(30)
    while True:
        try:
            await tik()
        except Exception as e:
            print(f"[OAR-Paper] döngü hatası: {str(e)[:80]}")
        await asyncio.sleep(interval)


def durum_ozet() -> dict:
    """UI kutusu için: güncel bakiye, açık pozisyonlar, BU HAFTANIN işlemleri + istatistik.
    (Kutu haftalık gösterir; hafıza ay sonuna dek tutulur, aylık rapordan sonra silinir.)"""
    from oar_rapor import bu_hafta_islemler, _iso_hafta
    d = _yukle()
    bu_hafta = bu_hafta_islemler(d.get("islemler", []))
    kazanan = [t for t in bu_hafta if t.get("pnl_usd", 0) > 0]
    return {
        "basladi": d.get("basladi"),
        "hafta": _iso_hafta(datetime.now(timezone.utc)),
        "baslangic_bakiye": d.get("baslangic_bakiye", BASLANGIC_BAKIYE),
        "bakiye": d.get("bakiye", BASLANGIC_BAKIYE),
        "kaldirac": d.get("kaldirac", KALDIRAC),
        "getiri_pct": round((d.get("bakiye", BASLANGIC_BAKIYE) /
                             d.get("baslangic_bakiye", BASLANGIC_BAKIYE) - 1) * 100, 2),
        "acik_pozisyonlar": d.get("acik", {}),
        "bu_ay_islem": len(bu_hafta),
        "bu_ay_kazanan": len(kazanan),
        "bu_ay_wr": round(100 * len(kazanan) / len(bu_hafta), 1) if bu_hafta else 0,
        "bu_ay_pnl_usd": round(sum(t.get("pnl_usd", 0) for t in bu_hafta), 2),
        "islemler": list(reversed(bu_hafta))[:50],
    }


if __name__ == "__main__":
    print(json.dumps(durum_ozet(), ensure_ascii=False, indent=2))
