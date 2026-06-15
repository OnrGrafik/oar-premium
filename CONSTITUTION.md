# OAR PREMIUM — ANAYASA (CONSTITUTION)

Bu belge projenin değişmez kurallarını ve hedefini tanımlar. Her agent ve her
sohbet bu kurallara uyar. Claude'un context'i sıfırlansa bile bu kurallar geçerlidir.

---

## MİSYON
OAR Premium, kurumsal seviyede bir **Quant Research Platformu**dur. Sinyal botu
değildir. 2020'den itibaren oluşan piyasa davranışlarını inceler, kendi teorilerini
üretir, test eder, performansını ölçer, eksiklerini bulur ve araştırma laboratuvarını
büyütür.

## ÜÇ ALTIN KURAL
1. **Hiçbir teori test edilmeden doğru kabul edilemez.**
2. **Hiçbir kod test edilmeden dağıtıma çıkamaz.**
3. **Hiçbir veri doğrulanmadan araştırmaya dahil edilemez.**

## VERİ FELSEFESİ
Render 512MB RAM / 1GB disk sınırlıdır. Bu yüzden:
- Ham piyasa verileri Render'da **tutulmaz**. Anlık çekilir, işlenir, sonuç saklanır, ham veri atılır.
- Render'da yalnızca saklanır: Theory Database, Learning Memory, Agent Memory,
  Research Results, Success Metrics — SQLite / JSON olarak `/var/data` (kalıcı disk).
- Akış: Ham veri → işle → özet çıkar → sonucu sakla → ham veriyi sil.

## ARAŞTIRMA YAKLAŞIMI
Amaç tahmin değil, **istatistiksel olarak anlamlı ilişkileri keşfetmek.**
Her araştırma Backtest → Forward Test → Validation süreçlerinden geçer.

## THEORY ENGINE YAŞAM DÖNGÜSÜ
Draft → Testing → Forward Testing → Confirmed / Rejected.
**Başarısız teoriler silinmez, arşivlenir** (aynı hataya tekrar düşmemek için).

## KOD DEĞİŞİKLİĞİ SÜRECİ (zorunlu sıra)
1. Analiz → 2. Risk analizi → 3. Etkilenen dosyalar → 4. Plan → 5. Kod →
6. Offline test → 7. Rapor → 8. Deploy talimatı.

## TEST ZORUNLULUĞU
Dağıtıma çıkacak her dosya için: Python Syntax, Import Test, Mock Test,
Empty-Data Test, API-Failure Test, JSON-Parse Test. **Başarısız test varsa dosya teslim edilmez.**

## YASAKLAR
- Test edilmeden dosya verme.
- Tüm dosyaları yeniden yazma (sadece değişeni ver).
- Varsayımla kod yazma.
- Çalışan sistemi gereksiz değiştirme.
- `.py` dosyalarını `api/` klasörüne koyma (köke koy).

## DAVRANIŞ
- Matematiksel, bilimsel, doğrulanabilir.
- Açıklamalar kısa ve öz.
- Eski dosyalara hâkim, üzerine inşa et.

## NİHAİ HEDEF
Sinyal botu değil; kendi teorilerini üreten, test eden, performansını ölçen,
eksiklerini bulan, araştırma laboratuvarını büyüten kurumsal Quant Research Platformu.
