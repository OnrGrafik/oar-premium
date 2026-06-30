# Kitapları Bilgisayar Açılışında Otomatik Yükleme

Railway'deki **canlı site senin `C:\` diskini göremez** (ayrı makineler). Bu yüzden
"bulut sunucu diskime baksın" mümkün değil. Çözüm: **senin bilgisayarında** açılışta
çalışıp yeni kitapları otomatik siteye yükleyen küçük bir görev. **Bir kez kurarsın,
sonra hiçbir şey yapmazsın** — her açılışta yeni kitaplar otomatik gider.

## Tek seferlik kurulum (5 dakika)

### 1) `kitap_sync_baslat.bat` dosyasını düzenle
`oar-premium` klasöründeki `kitap_sync_baslat.bat` dosyasını Not Defteri ile aç,
şu 3 satırı kendi bilgilerinle doldur:
```
set OAR_SITE_URL=https://SENIN-SITEN.up.railway.app
set OAR_API_KEY=SENIN_API_KEY
set KITAP_KAYNAK_DIR=C:\Users\ONURKLNC\Desktop\Data\kitaplar
```
- **OAR_SITE_URL**: Railway'deki canlı site adresin.
- **OAR_API_KEY**: Sitenin `OAR_API_KEY` env'i ile AYNI değer (yazma izni için).
- **KITAP_KAYNAK_DIR**: Kitap klasörün (alt klasörler otomatik kategori olur).

### 2) Windows Görev Zamanlayıcı'ya ekle ("oturum açıldığında")
1. Başlat → **Görev Zamanlayıcı** (Task Scheduler) aç.
2. Sağda **"Temel Görev Oluştur"**.
3. Ad: `OAR Kitap Sync` → İleri.
4. Tetikleyici: **"Oturum açtığımda"** (When I log on) → İleri.
5. Eylem: **"Program başlat"** → Gözat ile `kitap_sync_baslat.bat` dosyasını seç → İleri → Son.

Bitti. Artık bilgisayarın her açıldığında script sessizce çalışır, **yeni** kitapları
siteye yükler (eskileri atlar — `kitap_sync_durum.json` ile takip eder).

## Manuel test (kurulumdan sonra bir kez)
`kitap_sync_baslat.bat` dosyasına çift tıkla. Şunu görmelisin:
```
📂 C:\...\kitaplar — N dosya · Hedef: https://.../api/knowledge/import
   ✓ [Trading] Trading In The Zone (142 parça)
   ✓ [Psikoloji] ...
✅ N yeni kitap yüklendi
```
Sonra sitede **KÜTÜPHANE KATEGORİLERİ** kısmında kitapların kategorileriyle görünür.

## Notlar
- Yeni kitap eklersen: sadece klasöre koy; bir sonraki açılışta otomatik yüklenir.
- Aynı kitap iki kez yüklenmez (başlığa göre atlanır).
- `pip install requests pypdf` gerekebilir (PDF okumak için).
