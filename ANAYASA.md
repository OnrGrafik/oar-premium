# OAR ANAYASA — tek kaynak

1. **Test et, sonra ver.** `node --check` / `py_compile` = yalnız sözdizimi; davranış değil. Sandbox canlı API'ye erişemiyorsa açıkça "fonksiyonel test edilmedi" de.
2. **Sadece değişen dosya(lar).** Çalışan kodu gereksiz değiştirme; yeniden yazmak yerine cerrahi düzenle.
3. **Varsayım YOK.** JSON alan adı, formül, endpoint → koddan doğrula. Doğrulayamıyorsan dosyayı iste; uydurma.
4. **Tüm `.py` repo kökünde.**
5. **Çoklu dosya = tek commit.**
6. **Over-reach yok.** "Ekle" denmişse ekle. "Komple değiştir/aktar" denmedikçe mevcut yapı/düzen korunur.
7. **Açıklama kısa/öz; sayfa sayfa ilerle.**

## Token kuralı
Bu dosya tek referanstır. Kurallar her turda tekrar listelenmez; gerekince sadece "anayasa #N" diye atıf yapılır.
