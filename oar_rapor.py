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


def teyit_dokumu(trades: list) -> str:
    """
    Hangi teyit hangi WR getirmiş — canlı forward-test'in bilimsel çıktısı.
    Her işlem birden çok teyit taşıyabilir; her teyit için ayrı WR/net hesaplanır.
    (Altcoin geçmiş verisi olmadığı için validation yolumuz bu aylık döküm.)
    """
    sayac = {}
    for t in trades:
        eq = float(t.get("equity_pct", 0) or 0)
        for ty in (t.get("teyitler") or []):
            # etiketten filtre adını sadeleştir (ör. "HTF-VPFR Confluence (VAH) → SHORT" → "HTF-VPFR")
            for ad in ("HTF-VPFR", "Whale-Retail", "Opsiyon", "OAR Asia Range", "Trend-Devam"):
                if ad in ty:
                    d = sayac.setdefault(ad, {"n": 0, "kaz": 0, "net": 0.0})
                    d["n"] += 1; d["net"] += eq
                    if eq > 0:
                        d["kaz"] += 1
                    break
    if not sayac:
        return ""
    satirlar = []
    for ad, d in sorted(sayac.items(), key=lambda kv: kv[1]["n"], reverse=True):
        wr = round(100 * d["kaz"] / d["n"], 1) if d["n"] else 0
        satirlar.append(f"  • {ad}: n{d['n']} · WR %{wr} · net %{round(d['net'],1)}")
    return "Teyit dökümü (hangi filtre → WR):\n" + "\n".join(satirlar)


def rapor_metni(baslik: str, tip: str, etiket: str, oz: dict, trades: list = None) -> str | None:
    if not oz:
        return None
    usd = f" · ${oz['usd']:+.2f}" if oz.get("usd") else ""
    metin = (f"📊 {baslik} — {tip} Rapor ({etiket})\n"
             f"İşlem: {oz['n']} · Kazanan: {oz['kazanan']} · WR %{oz['wr']}\n"
             f"Net (5x toplam): %{oz['net']}{usd}")
    # Aylık raporda teyit-bazında döküm (bilimsel forward-test)
    if tip == "Aylık" and trades:
        dokum = teyit_dokumu(trades)
        if dokum:
            metin += "\n" + dokum
    return metin


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
        ay_trades = [t for t in trades if _ay_of(t) == r["son_ay"]]
        oz = ozet(ay_trades)
        m = rapor_metni(baslik, "Aylık", r["son_ay"], oz, ay_trades)
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
