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

## 6. Merkezi ajan kanalı
- `ajan_merkez.py` → Telegram thread **4129**, chat **-1002142274543**. `bildir(ajan,tur,ozet,detay)`.
- Tüm research/backtest/pattern/hipotez ajanları bulgularını buraya raporlar (OAR'ı geliştirmeye yönelik).

## 7. Kullanıcı hakkında
- Manuel trade yapmıyor (sinyal-bot'tan Bybit trading stack kaldırıldı).
- Açıklama Türkçe, kısa/öz, sayfa sayfa (ANAYASA #7).
