"""
OAR Rapor + Hafıza Yaşam Döngüsü — OAR Premium
═══════════════════════════════════════════════════════════════════════════════
BTC/ETH ve Altcoin OAR kutuları için ortak raporlama:
  • GÜNLÜK / HAFTALIK / AYLIK özet → Telegram (gün/hafta/ay dönümünde önceki dönem).
  • Kutu görseli SADECE içinde bulunulan HAFTAYI gösterir (durum_ozet filtreler).
  • Hafıza ay sonuna kadar tutulur; AYLIK rapordan SONRA o ay silinir.

Saf fonksiyonlar (test edilebilir) + kontrol_ve_gonder (durum dict üzerinde çalışır,
Telegram gönderimi çağrana bırakılır).
"""
from datetime import datetime, timezone


def _gun(dt): return dt.strftime("%Y-%m-%d")
def _iso_hafta(dt):
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"
def _ay(dt): return dt.strftime("%Y-%m")


def _dt_of(t):
    try:
        return datetime.fromisoformat(t.get("kapanis", ""))
    except Exception:
        return None


def _gun_of(t):
    d = _dt_of(t); return _gun(d) if d else ""
def _hafta_of(t):
    d = _dt_of(t); return _iso_hafta(d) if d else ""
def _ay_of(t):
    d = _dt_of(t); return _ay(d) if d else ""


def ozet(trades: list) -> dict | None:
    """İşlem listesinden özet (adet, kazanan, WR, net 5x toplam)."""
    n = len(trades)
    if not n:
        return None
    kaz = [t for t in trades if float(t.get("equity_pct", 0) or 0) > 0]
    net = round(sum(float(t.get("equity_pct", 0) or 0) for t in trades), 2)
    usd = round(sum(float(t.get("pnl_usd", 0) or 0) for t in trades), 2)
    return {"n": n, "kazanan": len(kaz), "wr": round(100 * len(kaz) / n, 1),
            "net": net, "usd": usd}


def rapor_metni(baslik: str, tip: str, etiket: str, oz: dict) -> str | None:
    if not oz:
        return None
    usd = f" · ${oz['usd']:+.2f}" if oz.get("usd") else ""
    return (f"📊 {baslik} — {tip} Rapor ({etiket})\n"
            f"İşlem: {oz['n']} · Kazanan: {oz['kazanan']} · WR %{oz['wr']}\n"
            f"Net (5x toplam): %{oz['net']}{usd}")


async def kontrol_ve_gonder(durum: dict, baslik: str, tg_gonder) -> list:
    """
    Gün/hafta/ay dönümünü izler; dönüm olunca ÖNCEKİ dönemin raporunu Telegram'a
    gönderir. Aylık rapordan sonra o aydan eski işlemleri siler (hafıza = current ay).
    tg_gonder: async(metin) çağrılabilir. Döner: gönderilen rapor tipleri.
    """
    r = durum.setdefault("rapor", {})
    now = datetime.now(timezone.utc)
    bugun, buhafta, buay = _gun(now), _iso_hafta(now), _ay(now)
    trades = durum.get("islemler", [])
    gonderildi = []

    # GÜNLÜK
    if r.get("son_gun") and r["son_gun"] != bugun:
        oz = ozet([t for t in trades if _gun_of(t) == r["son_gun"]])
        m = rapor_metni(baslik, "Günlük", r["son_gun"], oz)
        if m:
            await tg_gonder(m); gonderildi.append("gun")
    r["son_gun"] = bugun

    # HAFTALIK (kutu görseli zaten current-week filtreli → hafta başında otomatik boşalır)
    if r.get("son_hafta") and r["son_hafta"] != buhafta:
        oz = ozet([t for t in trades if _hafta_of(t) == r["son_hafta"]])
        m = rapor_metni(baslik, "Haftalık", r["son_hafta"], oz)
        if m:
            await tg_gonder(m); gonderildi.append("hafta")
    r["son_hafta"] = buhafta

    # AYLIK + hafıza temizliği (rapordan SONRA sil)
    if r.get("son_ay") and r["son_ay"] != buay:
        oz = ozet([t for t in trades if _ay_of(t) == r["son_ay"]])
        m = rapor_metni(baslik, "Aylık", r["son_ay"], oz)
        if m:
            await tg_gonder(m); gonderildi.append("ay")
        # aylık rapordan sonra: yalnız current ayın işlemlerini tut, gerisini sil
        durum["islemler"] = [t for t in trades if _ay_of(t) == buay]
    r["son_ay"] = buay

    return gonderildi


def bu_hafta_islemler(trades: list) -> list:
    """Kutu görseli için: yalnız içinde bulunulan ISO haftanın işlemleri."""
    buhafta = _iso_hafta(datetime.now(timezone.utc))
    return [t for t in trades if _hafta_of(t) == buhafta]
