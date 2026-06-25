#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════
  OAR Premium — Kitap İndeksi Yükleyici (BİLGİSAYARINDA ÇALIŞIR)
═══════════════════════════════════════════════════════════════════
kitap_hazirla.py'nin ürettiği kitap_indeksi.json dosyasını
Railway'deki sisteme PARÇA PARÇA yükler (timeout olmaz).

ÖNCE:  python kitap_hazirla.py <kitap_klasoru>   → kitap_indeksi.json üretir
SONRA: python kitap_yukle.py kitap_indeksi.json  → sisteme yükler

KULLANIM:
  pip install requests
  python kitap_yukle.py kitap_indeksi.json

  (URL değiştirmek için:)
  python kitap_yukle.py kitap_indeksi.json https://oar-premium.up.railway.app

  (Sistemde OAR_API_KEY ayarlıysa anahtarı ver:)
  python kitap_yukle.py kitap_indeksi.json https://oar-premium.up.railway.app MYKEY
  veya ortam değişkeni:  set OAR_API_KEY=MYKEY
"""
import sys, os, json, time

try:
    import requests
except ImportError:
    print("❌ requests gerekli: pip install requests")
    sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print("KULLANIM: python kitap_yukle.py kitap_indeksi.json [URL] [API_KEY]")
        sys.exit(1)

    dosya = sys.argv[1]
    base = sys.argv[2] if len(sys.argv) > 2 else "https://oar-premium.up.railway.app"
    base = base.rstrip("/")
    api_key = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("OAR_API_KEY", "")
    headers = {"X-API-Key": api_key} if api_key else {}

    if not os.path.exists(dosya):
        print(f"❌ Dosya bulunamadı: {dosya}")
        print(f"   Bulunduğun klasör: {os.getcwd()}")
        print(f"\n   Önce kitap indeksini oluşturman gerekiyor:")
        print(f"      python kitap_hazirla.py <kitap_klasoru>")
        print(f"   Bu komut '{dosya}' dosyasını üretir, sonra bu scripti çalıştır.")
        sys.exit(1)

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
                              json={"documents": batch}, headers=headers, timeout=180)
            if r.status_code == 200:
                res = r.json()
                gonderilen += res.get("eklenen_chunk", 0)
                print(f"✓ (toplam kitap: {res.get('toplam_kitap','?')})")
            elif r.status_code == 401:
                print("❌ HTTP 401 — API Key gerekli/geçersiz.")
                print("   Anahtarı 3. argüman olarak ver veya OAR_API_KEY ayarla.")
                sys.exit(1)
            else:
                print(f"❌ HTTP {r.status_code}")
        except Exception as e:
            print(f"❌ {str(e)[:50]} — 10sn sonra tekrar...")
            time.sleep(10)
            try:
                r = requests.post(f"{base}/api/knowledge/import",
                                  json={"documents": batch}, headers=headers, timeout=180)
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
