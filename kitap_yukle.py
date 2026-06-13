#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════
  OAR Premium — Kitap İndeksi Yükleyici (BİLGİSAYARINDA ÇALIŞIR)
═══════════════════════════════════════════════════════════════════
kitap_hazirla.py'nin ürettiği kitap_indeksi.json dosyasını
Render'daki sisteme PARÇA PARÇA yükler (timeout olmaz).

KULLANIM:
  pip install requests
  python kitap_yukle.py kitap_indeksi.json

  (URL'i değiştirmek istersen:)
  python kitap_yukle.py kitap_indeksi.json https://oar-premium.onrender.com
"""
import sys, json, time

try:
    import requests
except ImportError:
    print("❌ requests gerekli: pip install requests")
    sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print("KULLANIM: python kitap_yukle.py kitap_indeksi.json [URL]")
        sys.exit(1)

    dosya = sys.argv[1]
    base = sys.argv[2] if len(sys.argv) > 2 else "https://oar-premium.onrender.com"
    base = base.rstrip("/")

    print(f"📂 Okunuyor: {dosya}")
    with open(dosya, "r", encoding="utf-8") as f:
        data = json.load(f)

    docs = data.get("documents", [])
    print(f"📚 {data.get('kitap_sayisi','?')} kitap · {len(docs)} parça")
    print(f"🌐 Hedef: {base}/api/knowledge/import\n")

    BATCH = 150   # SQLite ile küçük batch yeterli, RAM dostu
    toplam = len(docs)
    gonderilen = 0

    for i in range(0, toplam, BATCH):
        batch = docs[i:i+BATCH]
        n = i // BATCH + 1
        toplam_batch = (toplam + BATCH - 1) // BATCH
        print(f"[{n}/{toplam_batch}] {len(batch)} parça gönderiliyor...", end=" ", flush=True)
        try:
            r = requests.post(f"{base}/api/knowledge/import",
                              json={"documents": batch}, timeout=180)
            if r.status_code == 200:
                res = r.json()
                gonderilen += res.get("eklenen_chunk", 0)
                print(f"✓ (toplam kitap: {res.get('toplam_kitap','?')})")
            else:
                print(f"❌ HTTP {r.status_code}")
        except Exception as e:
            print(f"❌ {str(e)[:50]} — 10sn sonra tekrar...")
            time.sleep(10)
            try:
                r = requests.post(f"{base}/api/knowledge/import",
                                  json={"documents": batch}, timeout=180)
                if r.status_code == 200:
                    gonderilen += r.json().get("eklenen_chunk", 0)
                    print("   ✓ ikinci denemede başarılı")
            except Exception as e2:
                print(f"   ❌ tekrar başarısız: {str(e2)[:40]}")
        time.sleep(1)  # Render'ı yormamak için

    print(f"\n{'═'*55}")
    print(f"✅ TAMAMLANDI — {gonderilen} parça yüklendi")
    print(f"   Kontrol: {base}/api/knowledge/kitaplar")
    print(f"{'═'*55}")

if __name__ == "__main__":
    main()
