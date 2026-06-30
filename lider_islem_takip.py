"""
Lider İşlem Takip — OAR Premium
══════════════════════════════════════════════════════════════════════════════
İstek (eksik #4):
  "Lider agent'in BTC-ETH için Telegram'a attığı işlem fikirleri tersine
   dönmüşse ya kâr alıp trailing stop devam etsin ya da işlemi tamamen
   kapatsın."

Bu modül lider'in Telegram'a attığı yönlü KARAR'ları (LONG/SHORT) kaydeder ve
her döngüde güncel fiyatla karşılaştırarak tersine dönüş tespitinde öneri üretir:

  • Kârdayken zirveden geri çekilme (trailing tetik)  → KAR_AL_TRAILING
       "Kârın bir kısmını al, kalan için trailing stop ile devam et."
  • Yön geçersizleşti (stop kırıldı / fikir ters döndü) → TAMAMEN_KAPAT
       "İşlemi tamamen kapat."

xATR trailing mantığının fiyat-yüzdesi karşılığıdır (lider tarafında
ATR serisi tutmadan, giriş + zirve PnL üzerinden trailing).
"""

from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
TAKIP_FILE = DATA_DIR / "lider_islem_takip.json"

# Trailing parametreleri (yüzde)
TRAILING_TETIK_PCT = 0.8    # zirvede en az +%0.8 kâr görülmeli ki trailing devreye girsin
TRAILING_GERI_PCT  = 0.5    # zirveden %0.5 geri çekilme → kâr al + trailing
GECERSIZ_PCT       = 0.6    # giriş aleyhine %0.6 → fikir geçersiz, tamamen kapat


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _yukle() -> dict:
    try:
        if TAKIP_FILE.exists():
            return json.loads(TAKIP_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _kaydet(d: dict):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        TAKIP_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except Exception:
        pass


def islem_kaydet(kok: str, yon: str, giris: float,
                 stop: float | None = None, hedef: float | None = None):
    """
    Lider yeni bir yönlü KARAR attığında çağrılır. Aynı kök için açık
    fikir varsa ve yön değiştiyse üzerine yazar (yeni fikir).
    """
    yon = (yon or "").upper()
    if yon not in ("LONG", "SHORT") or giris <= 0:
        return
    d = _yukle()
    mevcut = d.get(kok)
    # Aynı yön + yakın giriş → güncelleme, fikir devam ediyor say
    if mevcut and mevcut.get("yon") == yon and mevcut.get("durum") == "ACIK":
        return
    d[kok] = {
        "yon": yon, "giris": giris, "stop": stop, "hedef": hedef,
        "acilis": _now(), "zirve_pnl_pct": 0.0,
        "trailing_aktif": False, "durum": "ACIK",
    }
    _kaydet(d)


def islem_kapat(kok: str, neden: str = ""):
    """Fikir kapandı olarak işaretle (öneri uygulandıktan sonra)."""
    d = _yukle()
    if kok in d and d[kok].get("durum") == "ACIK":
        d[kok]["durum"] = "KAPALI"
        d[kok]["kapanis"] = _now()
        d[kok]["kapanis_neden"] = neden
        _kaydet(d)


def acik_islem(kok: str) -> dict | None:
    d = _yukle()
    o = d.get(kok)
    return o if o and o.get("durum") == "ACIK" else None


def _pnl_pct(yon: str, giris: float, guncel: float) -> float:
    if giris <= 0:
        return 0.0
    ham = (guncel - giris) / giris * 100
    return ham if yon == "LONG" else -ham


def reversal_kontrol(kok: str, guncel_fiyat: float,
                     yeni_karar: str | None = None) -> dict | None:
    """
    Açık lider fikrini güncel fiyatla değerlendirir.

    Döner (öneri varsa):
      {"aksiyon": "KAR_AL_TRAILING"|"TAMAMEN_KAPAT", "mesaj": str,
       "pnl_pct": float, "zirve_pnl_pct": float}
    Yoksa None.

    yeni_karar: Supervisor o an ters yönde KARAR verdiyse (LONG↔SHORT)
                fikir doğrudan geçersizdir.
    """
    o = acik_islem(kok)
    if not o or guncel_fiyat <= 0:
        return None

    yon = o["yon"]
    pnl = _pnl_pct(yon, o["giris"], guncel_fiyat)

    # Zirve PnL güncelle
    zirve = max(o.get("zirve_pnl_pct", 0.0), pnl)
    if zirve != o.get("zirve_pnl_pct"):
        d = _yukle()
        if kok in d:
            d[kok]["zirve_pnl_pct"] = zirve
            d[kok]["trailing_aktif"] = zirve >= TRAILING_TETIK_PCT
            _kaydet(d)

    # ── 1) Yön tamamen ters döndü → tamamen kapat ─────────────────────────────
    ters = {"LONG": "SHORT", "SHORT": "LONG"}.get(yon)
    if yeni_karar and yeni_karar.upper() == ters:
        islem_kapat(kok, "supervisor ters karar")
        return {
            "aksiyon": "TAMAMEN_KAPAT", "pnl_pct": round(pnl, 2),
            "zirve_pnl_pct": round(zirve, 2),
            "mesaj": (f"🔄 <b>{kok} fikri TERSİNE döndü</b> — Supervisor artık {ters} diyor.\n"
                      f"Öneri: <b>{yon} işlemini TAMAMEN KAPAT</b> "
                      f"(anlık {pnl:+.2f}%, zirve {zirve:+.2f}%)."),
        }

    # ── 2) Stop kırıldı / giriş aleyhine geçersizleşme → tamamen kapat ─────────
    stop_kirildi = False
    if o.get("stop"):
        stop_kirildi = (guncel_fiyat <= o["stop"]) if yon == "LONG" \
                       else (guncel_fiyat >= o["stop"])
    if stop_kirildi or pnl <= -GECERSIZ_PCT:
        islem_kapat(kok, "stop/geçersiz")
        sebep = "stop kırıldı" if stop_kirildi else f"giriş aleyhine %{GECERSIZ_PCT}"
        return {
            "aksiyon": "TAMAMEN_KAPAT", "pnl_pct": round(pnl, 2),
            "zirve_pnl_pct": round(zirve, 2),
            "mesaj": (f"⛔ <b>{kok} {yon} fikri GEÇERSİZ</b> — {sebep}.\n"
                      f"Öneri: <b>İşlemi TAMAMEN KAPAT</b> (anlık {pnl:+.2f}%)."),
        }

    # ── 3) Kârdayken zirveden geri çekilme → kâr al + trailing devam ──────────
    if zirve >= TRAILING_TETIK_PCT and (zirve - pnl) >= TRAILING_GERI_PCT and pnl > 0:
        islem_kapat(kok, "trailing — kâr al")
        return {
            "aksiyon": "KAR_AL_TRAILING", "pnl_pct": round(pnl, 2),
            "zirve_pnl_pct": round(zirve, 2),
            "mesaj": (f"🎯 <b>{kok} {yon} — kâr koruması</b>\n"
                      f"Zirve {zirve:+.2f}%'ten {pnl:+.2f}%'e geri çekildi "
                      f"(≥%{TRAILING_GERI_PCT}).\n"
                      f"Öneri: <b>Kârın bir kısmını AL, kalanı TRAILING STOP ile sürdür</b>."),
        }

    return None


if __name__ == "__main__":
    islem_kaydet("BTC", "LONG", 100000, stop=98000, hedef=104000)
    print(reversal_kontrol("BTC", 101000))   # zirve oluşur
    print(reversal_kontrol("BTC", 100400))   # geri çekilme → kar al trailing
