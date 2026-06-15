# OAR PREMIUM — AGENT KAYITLARI (AGENTS)

## ALT AGENTLAR

### Lider Agent
Görev: Orkestrasyon, sohbet, görev dağıtımı, tüm sayfalara + kütüphaneye erişim.
Saatlik rapor üretir, çoklu bot teyidini değerlendirir, kullanıcı sorularını
canlı veri + kitap + backtest bağlamıyla yanıtlar. Kitaplara erişimi vardır
("erişemiyorum" demez — FTS5 + LIKE fallback ile arama yapar).

### Research Agent
Görev: Matematiksel/bilimsel pattern analizi. Hipotez üretir, görev kuyruğuna yazar.
Theory Lab'de tüm sayfalardan (indikatör, opsiyon, makro, rejim, kitap) teori sentezler.

### Backtest Agent
Görev: Sinyal sonrası coin ne kadar gitti ölçer (başarı skoru). Kombo sinyal üretir.
Win/Loss değil — sinyal yönünde hareketi ve 10$/100x dolar kazancını hesaplar.

### Shared Memory
`agent_memory.json` — tüm agentların okuyup yazdığı ortak banka (`/var/data`).

---

## SİNYAL BOTLARI (başarı skoru ölçülür)
Başarı = sinyal üretmek DEĞİL, sinyal yönünde coin'in ne kadar gittiği.

- **OAR Kombo** — CVD+OI+funding kombinasyonu
- **OAR Pattern** — öğrenilmiş WIN profillerinden pattern
- **UTBot** — ATR Trailing Stop + STC + RSI
- **MA Scanner** — MA temas + whale filtresi
- **CVD Scanner** — CVD + OI artış + hacim patlaması
- **Asia Ekstrem** — Asia Range fib ekstrem teması
- **Volume Bot** — hacim + OI + VWAP/MA üstü (SİNYAL botu)

## BİLGİ BOTLARI (bağlam sağlar, skor ölçülmez)
- **Balina Bot** — büyük aggTrade taker alış/satış
- **Korelasyon** — Pearson korelasyon + beta, risk-on/off rejim
- **Whale Tracker** — BlackRock/MicroStrategy cüzdan + ETF akışı
- **Makro Alarm** — CW/PW/ZG seviyeleri (Deribit)

---

## BOT REPOSİTORY NOTU
Sinyal botları ayrı bir Render servisinde (`Oar-Sinyal-Bot` reposu, tek `bot.py`)
çalışabilir; sinyalleri `/signals` ile ana sisteme besler. Ana OAR sistemi bu
sinyalleri toplar, değerlendirir, başarı skorunu hesaplar.

## KURAL
Botlar BİRBİRİYLE YARIŞTIRILMAZ. Her botun görevi farklı. Win rate ölçülmez.
Sadece başarı skoru (sinyal sonrası hareket) tutulur, haftalık sıfırlanır.
