# OAR PREMIUM — BOOTSTRAP

> Yeni bir sohbet açtığında **sadece bu dosyayı yapıştır** ve şunu de:
> "Bu projede çalışacağız. Önce bu BOOTSTRAP'ı oku, sonra `/docs` içindeki
> CONSTITUTION.md, PROJECT_STATE.md, ARCHITECTURE.md dosyalarını oku.
> Projeyi anladığını kısaca özetle. Henüz kod yazma."

---

## BU PROJE NEDİR
OAR Premium bir **sinyal botu değildir**. OAR Asia, Options/GEX, CVD, Coinbase Premium, Open Interest, Footprint, Liquidity, VWAP, VPFR, Macro Data ve Whale Activity arasındaki ilişkileri araştıran, teori üreten, test eden ve bilgi sermayesini büyüten bir **Quant Research Platformu**dur.

Amaç sinyal üretmek değil: **yeni teori bulmak, test etmek, doğrulamak, edge keşfetmek, bilgi bankasını büyütmek.**

## ÇALIŞMA ORTAMI (değişmez gerçek)
- Çalışma dizini: `/home/claude/crypto-agent-gemini/`
- Claude GitHub/Render'a **doğrudan erişemez**, deploy başlatamaz, branch/PR açamaz.
- Claude yalnızca: dosya üretir, kod düzenler, **offline test eder**, deploy talimatı verir.
- Kullanıcı (ONURKLNC / GitHub: OnrGrafik) değişen dosyaları `OnrGrafik/oar-premium` reposuna yükler, Render otomatik deploy eder.
- Canlı URL: https://oar-premium.onrender.com
- Claude'un sandbox'ı **canlı API'lere erişemez** (Binance/Deribit/FRED engelli). Bu yüzden API'ye bağlı kod offline doğrulanır, canlı testi deploy sonrası kullanıcı yapar.

## ALTIN KURALLAR
1. Hiçbir kod offline test edilmeden teslim edilmez.
2. Sadece **değişen dosyalar** verilir, tüm dosyalar yeniden yazılmaz.
3. Tüm `.py` dosyaları repo **köküne** gider (`api/` klasörüne değil) → yoksa `ModuleNotFoundError` → 502.
4. Mevcut çalışan sistemi gereksiz değiştirme. "Birini yaparken diğerini bozma."
5. Tahmin/varsayım yok; matematiksel, bilimsel, doğrulanabilir.
6. Birden çok dosya varsa **tek commit** öner (her yükleme ayrı deploy = 502 penceresi).

## HER GÖREVDE SIRA
1. Mevcut kodu incele (ilgili dosyaları oku)
2. Etkilenen dosyaları belirle
3. Risk analizi
4. Plan
5. Kod yaz
6. Offline test et (syntax + import + mock + boş-veri + JSON-parse)
7. Raporla
8. Deploy talimatı ver

## TESLİM FORMATI
- **Değişen dosyalar** (ad + neden)
- **Testler:** Python syntax / import / mock / empty-data / JSON-parse → PASS/FAIL
- **Risk:** Düşük / Orta / Yüksek
- **Deploy talimatı:** GitHub + Render adımları

## KULLANICI TERCİHLERİ
- Açıklamalar kısa ve öz.
- Tahmin/varsayım yok, bilimsel/matematiksel.
- Eski dosya ve konuşmalara hâkim ol, unutma.

---
Detaylar `/docs` içindeki diğer dosyalarda. Bu dosyayı okuduktan sonra önce onları oku, sonra çalış.
