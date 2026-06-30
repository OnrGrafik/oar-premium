"""
Kitap Sync — Bilgisayar açılışında kitapları CANLI siteye otomatik yükler
═══════════════════════════════════════════════════════════════════════════════
Railway'deki canlı site senin C:\\ diskini göremez (ayrı makine). Bu script
SENİN bilgisayarında çalışır: kitap klasörünü (alt klasörler = kategori) tarar,
YENİ kitapları canlı sitenin /api/knowledge/import ucuna gönderir. Daha önce
gönderilenleri atlar (kitap_sync_durum.json).

Bir kez Windows "Görev Zamanlayıcı"ya "oturum açıldığında" eklenir → sonra her
açılışta SEN HİÇBİR ŞEY YAPMADAN yeni kitaplar otomatik yüklenir.

Ayarlar (ortam değişkeni ya da varsayılan):
  KITAP_KAYNAK_DIR  : kitap klasörü (vars: C:\\Users\\ONURKLNC\\Desktop\\Data\\kitaplar)
  OAR_SITE_URL      : canlı site (vars: env OAR_SITE_URL)
  OAR_API_KEY       : yazma anahtarı (site OAR_API_KEY ile aynı olmalı)
"""
import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone

VARSAYILAN_KAYNAK = r"C:\Users\ONURKLNC\Desktop\Data\kitaplar"
DURUM_DOSYA = Path(__file__).with_name("kitap_sync_durum.json")
BATCH = 150


def _ayar():
    kaynak = os.environ.get("KITAP_KAYNAK_DIR", VARSAYILAN_KAYNAK)
    site = os.environ.get("OAR_SITE_URL", "").rstrip("/")
    key = os.environ.get("OAR_API_KEY", "")
    if len(sys.argv) >= 2:
        site = sys.argv[1].rstrip("/")
    if len(sys.argv) >= 3:
        key = sys.argv[2]
    if len(sys.argv) >= 4:
        kaynak = sys.argv[3]
    return kaynak, site, key


def _gonderilenler() -> set:
    if DURUM_DOSYA.exists():
        try:
            return set(json.loads(DURUM_DOSYA.read_text(encoding="utf-8")).get("basliklar", []))
        except Exception:
            return set()
    return set()


def _durum_yaz(basliklar: set):
    DURUM_DOSYA.write_text(json.dumps({"basliklar": sorted(basliklar),
                                       "guncelleme": datetime.now(timezone.utc).isoformat()},
                                      ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    kaynak, site, key = _ayar()
    if not site:
        print("❌ OAR_SITE_URL verilmedi. Örnek:\n"
              "   python kitap_sync.py https://senin-siten.up.railway.app SENIN_API_KEY")
        sys.exit(1)
    klasor = Path(kaynak)
    if not klasor.is_dir():
        print(f"❌ Kitap klasörü yok: {klasor}")
        sys.exit(1)

    try:
        import requests
        from kitap_hazirla import _pdf_metin, _txt_metin, _temizle, _parcala
    except Exception as e:
        print(f"❌ Gerekli modül yüklenemedi ({e}). 'pip install requests pypdf' gerekebilir.")
        sys.exit(1)

    gonderilen = _gonderilenler()
    headers = {"X-API-Key": key} if key else {}
    yeni_basliklar = set()
    toplam_parca = 0

    dosyalar = [p for p in sorted(klasor.rglob("*"))
                if p.is_file() and p.suffix.lower() in (".pdf", ".txt", ".md")]
    print(f"📂 {klasor} — {len(dosyalar)} dosya · Hedef: {site}/api/knowledge/import\n")

    for p in dosyalar:
        baslik = p.stem
        if baslik in gonderilen:
            continue
        kategori = p.parent.name if p.parent != klasor else "genel"
        try:
            ham = _pdf_metin(p) if p.suffix.lower() == ".pdf" else _txt_metin(p)
            metin = _temizle(ham)
            if len(metin) < 100:
                print(f"   ⏭ {baslik}: metin yok/taranmış PDF, atlandı")
                continue
            parcalar = _parcala(metin)
            docs = [{"title": baslik, "category": kategori, "chunk_idx": i,
                     "content": parca, "added_at": datetime.now(timezone.utc).isoformat()}
                    for i, parca in enumerate(parcalar)]
        except Exception as e:
            print(f"   ❌ {baslik}: okunamadı ({str(e)[:50]})")
            continue

        # Batch'ler halinde gönder
        ok = True
        for i in range(0, len(docs), BATCH):
            batch = docs[i:i + BATCH]
            try:
                r = requests.post(f"{site}/api/knowledge/import",
                                  json={"documents": batch}, headers=headers, timeout=180)
                if r.status_code == 401:
                    print("❌ HTTP 401 — API Key gerekli/geçersiz (OAR_API_KEY).")
                    sys.exit(1)
                if r.status_code != 200:
                    print(f"   ❌ {baslik}: HTTP {r.status_code}")
                    ok = False
                    break
            except Exception as e:
                print(f"   ❌ {baslik}: ağ hatası ({str(e)[:40]}) — 10sn sonra tekrar")
                time.sleep(10)
                ok = False
                break
        if ok:
            yeni_basliklar.add(baslik)
            toplam_parca += len(docs)
            print(f"   ✓ [{kategori}] {baslik} ({len(docs)} parça)")

    if yeni_basliklar:
        _durum_yaz(gonderilen | yeni_basliklar)
        print(f"\n✅ {len(yeni_basliklar)} yeni kitap yüklendi ({toplam_parca} parça). "
              f"Toplam kayıtlı: {len(gonderilen | yeni_basliklar)}")
    else:
        print("\n✅ Yeni kitap yok — hepsi zaten yüklü.")


if __name__ == "__main__":
    main()
