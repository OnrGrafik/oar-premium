# OAR PREMIUM — ARAŞTIRMA HAFIZASI (OAR_RESEARCH)

Bu dosya araştırma çerçevesini ve doğrulanmış teorileri tutar. Yıllar içinde
sistemin en değerli birikimi burada olur. Her Confirmed/Rejected teori eklenir.

---

## OAR ASIA RESEARCH FRAMEWORK
**Aktif koşul:** TR 03:00–07:00 arası oluşan range, %1 üzerindeyse aktif.

**Fib seviyeleri:** 0.377, 0.618, 1.377, 1.618, 2.272, 2.618, -0.377, -0.618, -1.272, -1.618

**İlişkilendirilecek değişkenler:** Coinbase Premium, OI, CVD, VWAP, VPFR, Footprint,
PW, CW, ZG, Monday Range, Daily Open, Weekly Open, Midnight, TWO, TWAO.

**Enstrümanlar:** BTC, ETH, Altın, Gümüş, SP500, Nasdaq (sadece bunlar trade edilir).

## OPTIONS RESEARCH FRAMEWORK
Araştır: PW, CW, ZG, GEX, Gamma Flip, OI, Options CVD, Dealer Positioning.
Amaç: opsiyon davranışının fiyata etkisini istatistiksel olarak bulmak.

## THEORY DATABASE FORMATI
Her teori:
```
ID: OAR-XXX
Hipotez: (örn. "Asia High sweep → 1.377 hedef")
Enstrüman / Fib / Koşul:
Durum: Draft / Testing / Confirmed / Rejected
Win Rate: %
Profit Factor:
Örnek sayısı:
Not: (mantık, neden confirmed/rejected)
```

## DOĞRULANMIŞ TEORİLER (Theory Engine'den senkronize edilir)
> Bu bölüm `theory_engine.py` ve `theory_lab.py` çıktılarıyla güncellenir.
> Confirmed teoriler buraya kalıcı not olarak eklenir.

| ID | Hipotez | Durum | WR | PF |
|---|---|---|---|---|
| OAR-007 | Asia High sweep → 1.377 hedef | Confirmed | ~73% | 1.91 |
| OAR-002 | Asia Range %61.8'den dönüş | Confirmed | ~71% | 1.83 |
| OAR-009 | Asia ekstrem -1.272 LONG teması | Confirmed | ~68% | 1.66 |
| OAR-001 | Asia Range %37.7'den dönüş | Rejected | ~44% | 0.86 |

(Yukarıdakiler örnek/tohum verilerdir — gerçek değerler canlı backtest'ten gelir
ve haftalık güncellenir. Rejected teoriler de saklanır.)

## ARAŞTIRMA İLKELERİ
1. Her teori backtest + forward test + validation'dan geçer.
2. Başarısız teoriler silinmez, arşivlenir.
3. İstatistiksel anlamlılık şart (yeterli örnek sayısı: ≥30).
4. Confirmed eşiği: WR ≥%60 ve PF ≥1.5 ve örnek ≥30.
5. Rejected eşiği: WR <%45 veya PF <1.0.

## SÜREKLİ ARAŞTIRMA SORULARI (her gün)
- Hangi feature daha predictive?
- Hangi rejimde edge var?
- False positive kümeleri nerede?
- Eksik veri/modül var mı?
- Mevcut teoriler bozuluyor mu?
