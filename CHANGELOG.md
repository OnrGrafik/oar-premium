# OAR PREMIUM — DEĞİŞİKLİK GÜNLÜĞÜ (CHANGELOG)

Her deploy/değişiklik buraya tarih sırasıyla eklenir. En yeni en üstte.

---

## 2026-06-15 — Opsiyon matematiği + crash fix
- options_engine: gerçek Hull 11e Greekleri (Γ/𝒱/𝒞), GEX/VEX/CEX, log-moneyness ATM IV, gerçek 25Δ Risk Reversal
- indicator_engine: _klines JSON-parse crash-proof + spot fallback (500 fix)
- live.html: spark + CVD grafikleri NaN-safe (polyline NaN fix)

## 2026-06-15 — Makro carry + Theory tüm sayfalar
- macro_engine: carry trade monitörü (USD/JPY, JGB, ABD 10Y, Nikkei, VIX, BoJ)
- theory_engine + main: Theory Lab artık indikatör+opsiyon+makro+rejim+kitap harmanlıyor

## 2026-06-15 — Komuta + Opsiyon görsel
- Piyasa Durumu 3 başlık (Teknik/Temel/Psikoloji) + kitap kaynakları + renkli vurgu
- Grafik altı canlı yorum (Lider gözlemi)
- Opsiyon sayfası görsele göre: Strike Topografyası hover, Kuantum Duvarlar yatay GEX bar

## 2026-06-15 — Canlı Panel + API key
- Canlı Panel sohbet başa dönme fix (updateFeedOnly/updateChatOnly)
- CoinGecko key + ticker yedeği, FRED key bağlandı

## 2026-06-14 — v3 sayfalar
- 7 ekran yeniden tasarım, başarı skoru sistemi, ASIA RANGE grafik, makro motoru

## 2026-06-13 — Kitaplık
- SQLite + FTS5 kitap DB, 462 kitap / 27.000+ chunk import

---
> Yeni satır eklerken: tarih + kısa başlık + değişen dosyalar/etki.
