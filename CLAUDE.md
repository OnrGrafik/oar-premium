# OAR Premium — Kalıcı Hafıza (her oturumda oku)

> Bu dosya her yeni oturumda otomatik yüklenir. Bağlam sıfırlansa da burada yazanlar kaybolmaz.
> Kullanıcının her seferinde hatırlatmak zorunda kalmaması için buradadır. Yeni kalıcı kural/olgu öğrenince BURAYA ekle.

## 0. ÖNCE ANAYASA
`docs/ANAYASA.md` bağlayıcı kurallar bütünüdür (9 kural + token kuralı). Her işten önce ona uy. Özellikle:
- **#3 Varsayım YOK** — JSON alanı/formül/endpoint/dönüş tipini KODDAN doğrula (ör. `_vpfr_deger_alani` **tuple** `(poc,vah,val)` döner, dict değil).
- **#8** — en yüksek WR / şampiyon sistemin koduna dokunmadan ÖNCE BÜYÜK HARFLERLE uyar + açık onay al.
- **#9** — TÜM enerji yalnızca OAR Asia Range için.

## 1. Depolar & dallar
- `onrgrafik/oar-premium` (ana platform, Railway) — branch: `claude/new-session-qy6kax`
- `onrgrafik/Oar-Sinyal-Bot` (Telegram sinyal botları) — branch: `claude/new-session-qy6kax`
- Tüm `.py` repo kökünde (ANAYASA #4). Çoklu dosya = tek commit (#5).

## 2. Git — DİKKAT (tekrarlayan sorun)
- **FORCE-PUSH YAPMA.** `--force`/`--force-with-lease` kullanıcının yerel git'ini bozdu (branch divergence → `git pull` patladı → dosyalar gelmedi). Normal `git push -u origin <branch>` kullan.
- Kullanıcı her şeyi **kendi bilgisayarına** çekiyor (`git pull`) ve orada çalıştırıyor. Yerel git tutarlılığı kritik — divergence yaratma.
- Kullanıcı istemedikçe **PR açma**.
- Commit → push sonrası kullanıcıya `git pull` + çalıştırma komutunu ver.

## 3. Şampiyon sistem (ANAYASA #8 kapsamında — dokunmadan önce CAPS uyarı)
- **Şampiyon: `fade + htf_vpfr`** (Asia range ekstremlerinde mean-reversion). BTC+ETH holdout SAĞLAM: PF ~2.3, WR %35-37, +197-272%.
- **Market kapısı (rejim anahtarı):** BTC **VE** ETH Asia range ≥ %1 → fade-işlem günü; değilse işlem yok.
- Asia < %1 → trend-devam modu. Trend modu holdout'ta 3 kez battı; şampiyon fade'dir.
- Yaşadığı dosya: `oar_local_backtest.py` (şampiyon burada — CAPS uyarısı olmadan değiştirme).

## 4. Seanslar (UTC) & veri
- Asia 00:00–04:00 · London 07:00–11:00 · NY 13:00–17:00 (UTC).
- Veri: BTC/ETH parquet **klines(1m)** + **aggTrades** (yerel diskte). Klines kolonları: `[open_time, open, high, low, close, volume]`.
- aggTrades işleme YAVAŞ (78+ ay). Uzun işlerde **ay ay ilerleme yazdır** (`flush=True`) — sessiz bekletme, kullanıcı Ctrl+C yapıyor.

## 5. seans_karakter.py (research modülü)
- Seans karakteristikleri → ileri sonuç backtest'i (scalp + swing confirm). No-lookahead, LIFT, Wilson CI, OOS holdout.
- aggTrades sonucu `.seans_cache/` içine cache'lenir → tekrar çalıştırınca kaldığı yerden devam (yeniden işlemez).
- Çalıştırma: `python seans_karakter.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06`
- **BULGU (2019-01..2025-06, BTC+ETH, 13.148 gün×seans):** Test edilen seans-koşullarının HİÇBİRİ pozitif LIFT vermedi (hepsi taban WR'nin altında). "Cumartesi Asia alıcı → hafta bull" hipotezi ÇÜRÜDÜ (WR %47.8 vs taban %55.4, LIFT −7.6). Taban %55.4 zaten bull-drift'ten yüksek. SONUÇ: bu seans karakteristikleri scalp/swing confirm olarak KULLANILAMAZ; şampiyon fade+htf_vpfr'ye ekleme yapmıyor. Aynı yola tekrar girme. ("OOS ✅" sadece yeterli örneklem demek, tahmin gücü değil.)

## 5b. oar_impulse_breakout.py (yerel hipotez-test + Telegram)
- Kullanıcı hipotezi: Asia HIGH kırılır + HACİM desteklerse → fiyat fib 1.618 (TP1) ve 2.618 (ekstrem/full TP) hedeflerine "geçen günlerde fazla" ulaşıyor.
- Yerel parquet klines tarar (aggTrades gerekmez → hızlı). P(TP1|kırılım+hacim) + hacim DESTEKSİZ'e kıyasla LIFT. No-lookahead hacim tabanı (önceki 20 gün).
- Çalıştırma: `python oar_impulse_breakout.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram`
- NOT: autonomous_researcher.py YALNIZ parametre gridi deniyor (kullanıcının GERÇEK fikirlerini test etmiyor, Telegram'a atmıyor). Kullanıcı "araştırmacı hiç denemiyor" diye haklı sitem etti. Yeni hipotezler bu tarz ayrı yerel modüllerle test edilip Telegram'a raporlanmalı.

## 5c. oar_hipotez_motoru.py (OTONOM hipotez üretici + araştırıcı + Telegram) ⭐
- Kullanıcının ASIL istediği: hipotez ajanı SÜREKLİ yeni OAR hipotezleri ÜRETSİN → yerel derin veride ARAŞTIRSIN → LIFT/OOS ile elesin → Telegram'a atsın → denenen/reddedileni HATIRLASIN (tekrar denemesin). "Benim dediğim tek hipotezle sınırlı olmasın."
- Kombinatoryal hipotez uzayı (tetik×filtre×hedef): asia_high/low kırılım, üst/alt ekstrem fade, hacim-destek var/yok, gün (haftaiçi/sonu/tek gün), genlik kovası → hedef: fib 1.618/2.618 vur, 0.5 fade dönüş, hafta bull. Gün×feature tablosunu bir kez hesaplar, maske ile 25+ hipotezi hızlı test eder.
- NO-LOOKAHEAD hacim tabanı (önceki 20 gün), LIFT=koşullu−taban, OOS holdout, MIN_N=40, LIFT_ESIK=5. `.hipotez_hafiza.json` → yeni kazananları ayırır.
- Çalıştırma: `python oar_hipotez_motoru.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram`  ·  sürekli: `--loop --aralik-saat 6 --telegram`
- Yeni yapı taşı/hedef eklemek = `_feature_tablo` + `_hipotezler` genişlet. Hipotez uzayını büyüterek ajan daha çok "dener".

## 5d. oar_kirilim_trade.py (kırılım trend-devam GERÇEK işlem doğrulaması)
- Hipotez motorunun EN GÜÇLÜ adayı: genlik%1-2 + hacim destekli + Asia-HIGH kırılım → fib 1.618 devam. Vuruş WR %70 / OOS %73 / LIFT +9.7 (2192 gün, BTC+ETH). AMA vuruş ≠ kâr (temiz-TP1 sadece %15.5 → whipsaw riski).
- Bu modül gerçek işleme çevirir: giriş=Asia-high, TP=1.618, 4 SL adayı (asia_high_alti/range_orta_0.5/range_dibi_0.0/yarim_R), bar-bar high/low, AYNI barda SL önce (kötümser), fee+slippage. Çıktı: WR/PF/beklenti/maxDD/OOS her SL için. PF≥1.3+beklenti>0 = aday.
- Çalıştırma: `python oar_kirilim_trade.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram`
- SONUÇ (GERÇEK veri, n737 hacim-destekli): en iyi SL=range_orta(0.5) → PF 1.21, beklenti +0.075%/işlem, maxDD %11.5, OOS beklenti +0.151 (in-sample'dan YÜKSEK = sağlam). Dar SL (asia_high_alti) whipsaw'dan batıyor (PF 0.78). Hacim filtresiz TÜM kırılım = negatif. → Marjinal ama pozitif, düşük-DD, OOS-sağlam TREND-DEVAM confirm adayı. Hacim ŞART. PF<1.3 (ev-kaçıran değil). Şampiyona eklenebilir, şampiyona dokunmadan.
- BACKTEST METODOLOJİSİ (kullanıcı sordu): backtest 1-DAKİKALIK klines mumlarıyla GÜN GÜN yapılır (5m değil). "Ay ay" olan şey sadece aggTrades yükleme/ilerleme göstergesidir; analiz gün×seans bazında 1m bar ile.

## 5e. AÇIK MİMARİ EKSİĞİ / İLKE (kullanıcı sordu — kalıcı)
- SORU: "öğrenilenleri lider agent kullanıyor mu? Agentlere nasıl güvenaliriz?" CEVAP: ŞU AN kullanmıyor — hipotez motorunun kazananları (.hipotez_hafiza.json) canlı OAR karar akışına BESLENMİYOR. Bu gerçek bir eksik. Hedef: kazanan+trade-doğrulanmış hipotezleri (ör. genlik%1-2 kırılım devam) canlı sisteme confirm olarak bağlamak.
- GÜVEN İLKESİ: LLM ajan LAFINA güvenme (research/öneri metni = düşük değer). SAYIYA güven: LIFT + OOS + PF + maxDD. Sistem kanıt-temelli olmalı, LLM-görüş-temelli değil. Telegram'a yalnız sayısal kanıtı olan bulgu gider.
- KULLANICI YÖNÜ: "BTC'de CHoCH/HH-LL incelemiyoruz; fiblerde ne tür HACİM hareketleri olduğunu inceliyoruz." → hipotez uzayı fib×hacim davranışına odaklanmalı (SMC yapı kırılımına değil).

## 6. Merkezi ajan kanalı
- `ajan_merkez.py` → Telegram thread **4129**, chat **-1002142274543**. `bildir(ajan,tur,ozet,detay)`.
- Tüm research/backtest/pattern/hipotez ajanları bulgularını buraya raporlar (OAR'ı geliştirmeye yönelik).

## 6b. Telegram kanalı GÜRÜLTÜ YASAĞI (kullanıcı sitemi — kalıcı)
- Kanal (thread 4129) YALNIZCA gerçek OAR değeri taşımalı: test edilmiş hipotez (LIFT/OOS), backtest sonucu, sistem eksiği/hatası/aksaması, kullanıcıya somut istek.
- ATILMAYACAK (spam — kaldırıldı, geri EKLEME): ① "Pattern Öğrenici" kazanan profil tekrarı (feature_engine) ② "Research Agent Trend: altcoinler · Korku: N" (leader — fear index + BTC/ETH dışı coin = gürültü, kullanıcı KULLANMIYOR) ③ saatlik "6/6 servis aktif, sağlık mükemmel" (leader görüş) ④ "LİDER Ajan Aktivite Özeti" yankı digest (bekleyen_ozet) ⑤ jenerik "Öneri (research)" LLM lafı (oneri_motoru).
- Lider ajan artık SADECE arıza olunca konuşur (tur="eksik", servis down listesi). "Her şey yolunda" = SESSİZLİK.
- Fear/greed index ve BTC/ETH dışı coinler kullanıcı için ALAKASIZ. Sadece BTC+ETH.
- ⑥ lider_anlik_yorum "CVD yön değişti" TETİĞİ kaldırıldı — her poll'da BEARISH↔BULLISH flip-flop = kararsız gürültü ("BTC 5 sn'de yön değiştiremez"). Anlık yorum yalnız ANLAMLI değişimle (KARAR/GEX/funding/OI/fiyat%/CB/likidasyon/indikatör) tetiklenir. ⑦ oneri_motoru: yalnız PARAMETRE (uygulanabilir) öneri Telegram'a gider; BILGI tipi jenerik LLM lafı ("Extreme Fear…") gönderilmez.
- CMD KURALI: tüm backtest/hipotez/trade araçları uzun işlerde ay ay "· YYYY-MM analiz ediliyor…" yazmalı (flush=True). Donuk ekran kullanıcıyı Ctrl+C'ye itiyor.

## 7. Kullanıcı hakkında
- Manuel trade yapmıyor (sinyal-bot'tan Bybit trading stack kaldırıldı).
- Açıklama Türkçe, kısa/öz, sayfa sayfa (ANAYASA #7).
