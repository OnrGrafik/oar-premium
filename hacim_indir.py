"""
hacim_indir.py — HACİM KONSEYİ VERİSİNİ PC'YE OTOMATİK KAYDET (Faz 3b, kullanıcı isteği)
════════════════════════════════════════════════════════════════════════════════════
Kullanıcı isteği: "veriler haftada bir bilgisayarıma insin." Sunucu (Railway) veriyi
otomatik GitHub'a push EDEMEZ → çözüm: sunucudaki indirme ucundan (/api/hacim-veriseti)
bu script PC'de veriyi ÇEKER ve bilgisayara yazar (git GEREKMEZ).

NE İNER: haftalık özet arşivi (son 12 hafta) + o anki ham snapshot'lar (site hafızasından
SİLİNMEDEN önce PC'ye insin diye). Her GÜN için bir dosya → hiçbir ham veri kaybolmaz.

KAYIT: ./hacim_arsiv/hacim_veriseti_<YYYY-MM-DD>.json (aynı gün tekrar çalışırsa günceller).

KULLANIM:
  Tek sefer:   python hacim_indir.py [https://oar-premium.up.railway.app]
  Sürekli:     python hacim_indir.py --loop            (24 saatte bir çeker)
  Sunucu URL:  argüman > env OAR_SITE_URL > varsayılan (oar-premium.up.railway.app)

NOT: dış bağımlılık YOK (stdlib urllib). Vardiya (oar_vardiya.py) her turda tek-sefer
çağırır → kullanıcı ayrı komut öğrenmez (CLAUDE.md 5h "tek komut" ilkesi).
"""
import os
import sys
import json
import time
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

VARSAYILAN_URL = "https://oar-premium.up.railway.app"
ARSIV_DIR = Path(__file__).resolve().parent / "hacim_arsiv"


def _base_url() -> str:
    for a in sys.argv[1:]:
        if a.startswith("http"):
            return a.rstrip("/")
    return (os.environ.get("OAR_SITE_URL") or VARSAYILAN_URL).rstrip("/")


def indir_bir_kez(base: str) -> bool:
    url = f"{base}/api/hacim-veriseti"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "oar-hacim-indir"})
        with urllib.request.urlopen(req, timeout=30) as r:
            veri = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[hacim_indir] ❌ indirme hatası ({url}): {str(e)[:120]}", flush=True)
        return False
    if veri.get("durum") == "hata":
        print(f"[hacim_indir] ❌ sunucu hatası: {veri.get('aciklama')}", flush=True)
        return False

    ARSIV_DIR.mkdir(parents=True, exist_ok=True)
    gun = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dosya = ARSIV_DIR / f"hacim_veriseti_{gun}.json"
    try:
        dosya.write_text(json.dumps(veri, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"[hacim_indir] ❌ yazma hatası: {str(e)[:120]}", flush=True)
        return False

    snap = veri.get("snapshot_sayi", 0)
    hafta = veri.get("hafta_sayi", 0)
    print(f"[hacim_indir] ✅ kaydedildi → {dosya}  "
          f"({snap} ham snapshot · {hafta} haftalık özet · {veri.get('bu_hafta','?')})", flush=True)
    return True


def main():
    base = _base_url()
    loop = "--loop" in sys.argv
    print(f"[hacim_indir] sunucu: {base} · mod: {'sürekli (24s)' if loop else 'tek sefer'}", flush=True)
    if not loop:
        ok = indir_bir_kez(base)
        sys.exit(0 if ok else 1)
    while True:
        try:
            indir_bir_kez(base)
        except KeyboardInterrupt:
            print("\n[hacim_indir] durduruldu.", flush=True)
            break
        except Exception as e:
            print(f"[hacim_indir] ⚠ döngü hatası: {str(e)[:120]}", flush=True)
        time.sleep(24 * 3600)


if __name__ == "__main__":
    main()
