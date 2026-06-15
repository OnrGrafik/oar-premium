# OAR PREMIUM — PROJE DURUMU (PROJECT_STATE)

**Son güncelleme:** 2026-06-15

## DURUM: v3 yeniden yapılanma tamamlandı, hata düzeltme aşamasında

## TAMAMLANAN
- 7 ekranlı arayüz (Komuta, Opsiyon, Makro, Canlı, Agentlar, Theory, Bilgi)
- Başarı skoru sistemi (win rate yerine: max % + 10$/100x $, haftalık sıfırlanır)
- Kitap kütüphanesi: SQLite + FTS5, ~462 kitap, 27.000+ chunk (`kitaplar.db`)
- Kitap erişimi: FTS5 + LIKE fallback, Lider chat + ana sohbet + agentlar kullanıyor
- Ticker: 7 sabit sembol (BTC/ETH/SP500/Nasdaq/Altın/Gümüş/VIX)
- Komuta: Piyasa Durumu 3 başlık (Teknik/Temel/Psikoloji) + kitap kaynakları + grafik altı canlı yorum
- Opsiyon motoru: gerçek Hull 11e matematiği (Greekler Γ/𝒱/𝒞, 25Δ RR gerçek delta, log-moneyness ATM IV)
- Makro: 9 gösterge + BTC etkisi + carry trade monitörü (Yahoo/BLS/FRED/Treasury)
- Theory Lab: otomatik hipotez (coin/fib seçimi yok) + tüm sayfalardan teori
- Canlı Panel: sohbet başa dönme fix (updateFeedOnly/updateChatOnly)
- Dayanıklılık: startup her loop try/except, kalıcılık kontrolü, _klines/JSON crash-proof, spark/CVD NaN-safe

## SON DÜZELTİLEN HATALAR
- `<polyline> NaN` → spark + CVD grafikleri NaN filtreli
- `/api/indicators 500` → _klines JSON-parse crash-proof + spot fallback
- Opsiyon Greekleri yanlıştı → gex.js (Hull) birebir matematik
- 25Δ Risk Reversal "saçmalık"tı → gerçek delta hesabı (N(d1)=0.25/0.75)

## BİLİNEN AÇIK KONULAR / SIRADAKİ
- Opsiyon sayfası ince ayar: Buy/Sell volume per-strike grafiği (görsel 4), CVD görünüm cilası
- CSP/eval hatası: kullanıcının tarayıcı eklentisinden geliyor (bizim kod değil)
- Deploy sonrası canlı doğrulama gerekiyor: opsiyon verisi + grafik (Deribit/Binance Render'da erişilebilir)
- SP500/Nasdaq/Gümüş Theory Lab'de Yahoo proxy ile eklenebilir (şu an BTC/ETH/PAXG)

## DEPLOY KURALI
- Sadece değişen dosyaları yükle, tek commit.
- Tüm .py kökte (api/ değil).
- Render env: GEMINI_API_KEY, GROQ_API_KEY, FRED_API_KEY, COINGECKO_API_KEY ekli.

## ÇALIŞMA TARZI
Kullanıcı: kısa/öz açıklama, bilimsel/matematiksel, eski dosyalara hâkim ol.
"Birini yaparken diğerini bozma." Sayfa sayfa ilerle, her parçada deploy+test.
