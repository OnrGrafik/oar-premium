# OAR Premium — Tam Sistem Dokümantasyonu

**Tarih:** Haziran 2026  
**Platform:** Render.com (512MB, Python 3.12, FastAPI)  
**Model:** Google Gemini 2.5 Flash  
**Exchange:** Binance → Bybit (fallback) · Deribit (opsiyonlar) · Coinbase (premium)

---

## İÇİNDEKİLER

1. [Sistem Mimarisi](#1-sistem-mimarisi)
2. [Veri Kaynakları ve API'lar](#2-veri-kaynakları-ve-apiler)
3. [Hacim İndikatörleri](#3-hacim-i̇ndikatörleri)
4. [Teknik Göstergeler](#4-teknik-göstergeler)
5. [Opsiyonlar Motoru](#5-opsiyonlar-motoru)
6. [Tüm Agentlar — Detaylı Açıklama](#6-tüm-agentlar--detaylı-açıklama)
7. [Trading Supervisor — Karar Motoru](#7-trading-supervisor--karar-motoru)
8. [Zaman ve Makro Bağlam](#8-zaman-ve-makro-bağlam)
9. [Seans Analizi](#9-seans-analizi)
10. [Telegram Entegrasyonu](#10-telegram-entegrasyonu)
11. [Anlık AI Yorum Sistemi](#11-anlık-ai-yorum-sistemi)
12. [Veri Akışı — Uçtan Uca](#12-veri-akışı--uçtan-uca)

---

## 1. Sistem Mimarisi

```
exchange_client.py          ← Tüm exchange bağlantıları (Binance/Bybit/Deribit/Coinbase)
        │
        ├── regime_engine.py         ← Günlük rejim (trend/range/panik)
        ├── market_structure_agent.py ← Swing pivot, BOS, CHoCH, ADX (15m/1h/4h)
        ├── order_flow_agent.py       ← CVD, OI, Funding, Coinbase Premium, Taker
        ├── liquidity_agent.py        ← EQH/EQL, SFP, Stop Cluster, Sweep
        ├── session_agent.py          ← Asia/London/NY seans analizi
        ├── options_engine.py         ← Deribit GEX, IV, Greeks, Max Pain
        ├── time_context.py           ← FOMC, Triple Witching, Opsiyon Expirysi
        ├── market_context.py         ← OAR Skor (0-100) birleşik
        │
        └── trading_supervisor.py    ← 3 zorunlu soru → TRADE_LONG/SHORT/NO_TRADE
                │
                ├── leader_agent.py       ← AI kombolu sinyal analizi, raporlar
                ├── lider_anlik_yorum.py  ← Tetikleyici tabanlı anlık AI yorumu
                └── paper_trade_agent.py  ← Gerçek para kullanmadan forward test

main.py                  ← FastAPI sunucu, Telegram loop, tüm task yönetimi
brain.py                 ← Piyasa tarayıcı (10 sembol, RSI/MACD/Bollinger stratejileri)
```

**Retry / Fallback:** Tüm exchange çağrıları 3 deneme yapar (1s, 2s, 4s backoff). Binance başarısız olursa Bybit'e geçer.

---

## 2. Veri Kaynakları ve API'lar

### 2.1 Binance (Spot + Futures)

| Endpoint | Fonksiyon | Kullanım |
|---|---|---|
| `GET /fapi/v1/klines` | Futures OHLCV | Tüm teknik analiz |
| `GET /api/v3/klines` | Spot OHLCV | Regime engine (1d) |
| `GET /fapi/v1/openInterest` | Anlık OI | Order flow agent |
| `GET /futures/data/openInterestHist` | OI geçmişi | OI trend analizi |
| `GET /fapi/v1/fundingRate` | Funding rate | Order flow, market context |
| `GET /futures/data/takerlongshortRatio` | Taker oranı | Order flow agent |
| `GET /api/v3/ticker/price` | Spot fiyat | Coinbase premium hesabı |

**Çekilen OHLCV format:** `[timestamp_ms, open, high, low, close, volume, ...]`

### 2.2 Bybit (Fallback)

Binance başarısız olduğunda devreye girer:
- `GET /v5/market/kline` → OHLCV
- `GET /v5/market/open-interest` → OI
- `GET /v5/market/funding/history` → Funding

### 2.3 Deribit (Opsiyonlar)

| Endpoint | Fonksiyon |
|---|---|
| `get_book_summary_by_currency` | Tüm BTC/ETH opsiyonları — tek API çağrısı |
| `get_index_price` | BTC/ETH spot indeks fiyatı |

Deribit'ten çekilen her opsiyon satırı:
- `instrument_name` — `BTC-27JUN25-100000-C` formatı
- `open_interest` — kontrat adedi
- `mark_iv` — implied volatility (%)
- `mark_price` — opsiyon mark fiyatı
- Vade tarihi ve strike: isimden parse edilir

### 2.4 Coinbase Exchange

```
GET https://api.exchange.coinbase.com/products/{SEMBOL}-USD/ticker
```
Coinbase spot fiyatını Binance ile karşılaştırarak "ABD kurumsal alım baskısı" ölçülür.

### 2.5 Google Gemini 2.5 Flash

- **Kullanılan model:** `gemini-2.5-flash-preview-05-20`
- **Amaç:** Sinyal açıklamaları, saatlik raporlar, anlık yorumlar
- **Giriş:** Piyasa verileri + soru formatı Türkçe prompt
- **Çıkış:** 3-5 cümle AI analizi

---

## 3. Hacim İndikatörleri

### 3.1 CVD — Cumulative Volume Delta

**Dosya:** `order_flow_agent.py`

**Formül (Kaygın Formülü):**
```
buy_vol  = (close - low) / (high - low) * volume
sell_vol = volume - buy_vol
delta    = buy_vol - sell_vol
CVD      = Σ delta  (kümülatif toplam)
```

**Yorumlama:**
- `delta > 0` → O mumda net alım baskısı
- `delta < 0` → O mumda net satım baskısı
- CVD yükseliyorsa → alımlar satımları geçiyor (gizli birikim)
- CVD düşüyorsa → satımlar ağırlıklı (gizli dağıtım)

**CVD Trend Tespiti (son 10 mum):**
```python
normalize = (CVD[son] - CVD[başlangıç]) / ortalama_hacim
yon = BULLISH  if normalize > +0.05
    = BEARISH  if normalize < -0.05
    = NOTR     otherwise
```

**Kullanıldığı yerler:**
- `order_flow_agent.py` → ±30 puan (birincil sinyaller)
- `market_context.py` → OAR skor katkısı
- `lider_anlik_yorum.py` → CVD yön değişimi tetikleyicisi

### 3.2 Taker Long/Short Oranı

**Kaynak:** Binance `takerlongshortRatio`

```
long_ratio = 0.0 → 1.0  (1.0 = tüm işlemler alım tarafından)
```

**Puanlama:**
- `long_ratio > 0.55` → +15 puan (agresif alımcılar)
- `long_ratio < 0.45` → -15 puan (agresif satıcılar)

### 3.3 Open Interest (OI) Değişimi

OI tek başına yön vermez; CVD ile birleştirilir:

| CVD | OI | Yorum |
|---|---|---|
| BULLISH | Artıyor | Long pozisyon açılıyor → +20 puan |
| BEARISH | Artıyor | Short pozisyon açılıyor → -20 puan |
| Herhangi | Azalıyor | Pozisyon kapatma → -10 puan |

**Eşik:** OI değişimi ≥+1.0% → artıyor, ≤-1.0% → azalıyor

### 3.4 Opsiyon CVD

**Dosya:** `options_engine.py`  
**Kaynak:** Deribit son 3 gün işlem akışı

```
call_delta = call alımları - call satımları (delta ağırlıklı)
put_delta  = put satımları - put alımları   (delta ağırlıklı, sign ters)
opsiyon_cvd = call_delta + put_delta
```

Pozitif → kurumsal alım ağırlıklı  
Negatif → korunma satışı ağırlıklı

---

## 4. Teknik Göstergeler

### 4.1 ATR — Average True Range

**Dosya:** `regime_engine.py`, `market_structure_agent.py`

```
TR = max(high-low, |high-prev_close|, |low-prev_close|)
ATR(14) = Σ TR[-14:] / 14
ATR% = ATR / fiyat * 100
```

**Kullanım:**
- Stop mesafesi hesabı
- Volatilite ölçümü (expansion/compression tespiti)
- PANIC rejimi eşiği: ATR% > 4.5

### 4.2 RSI — Relative Strength Index

**Dosya:** `regime_engine.py`  
**Periyot:** 14 gün (1d mum)

```
RSI = 100 - (100 / (1 + RS))
RS  = avg_gain(14) / avg_loss(14)
```

**Kullanım:**
- Rejim tespiti:
  - RSI < 28 → PANIC olası
  - RSI > 52 + fiyat > SMA20 → TREND_UP
  - RSI < 48 + fiyat < SMA20 → TREND_DOWN

### 4.3 ADX — Average Directional Index

**Dosya:** `market_structure_agent.py`  
**Periyot:** 14 (intraday mumlar)

```
+DM = max(high - prev_high, 0) eğer > -DM
-DM = max(prev_low - low, 0)   eğer > +DM
DX  = 100 * |+DI - -DI| / (+DI + -DI)
ADX = SMA(DX, 14)   [Wilder smoothing]
```

**Kullanım:**
- ADX > 25 → Trend var (HH_HL veya LH_LL ile birleşir)
- ADX < 20 → Range / sıkışma

### 4.4 SMA — Simple Moving Average

```
SMA20 = Σ close[-20:] / 20
SMA50 = Σ close[-50:] / 50
```

**Kullanım:**
- `regime_engine.py`: Fiyat SMA20'nin kaç % üstünde/altında?
- `market_structure_agent.py`: Trend onayı

### 4.5 Gerçekleşen Volatilite (Realized Volatility)

**Dosya:** `regime_engine.py`

```
log_return(i) = ln(close[i] / close[i-1])
variance      = Σ (r - mean)² / N
RV            = sqrt(variance * 365) * 100  (yıllık, %)
```

**Eşikler:**
- RV > 80% → PANIC
- RV > 70% → HIGH_VOL

### 4.6 Band / ATR Oranı (Range Genişliği)

**Dosya:** `market_structure_agent.py`

```
band       = max(high[-20:]) - min(low[-20:])
range_oran = band / ATR(14)
```

- `range_oran > 4.0` ve `ATR% > 3.0` → EXPANSION
- `range_oran < 1.5` ve `ATR% < 1.0` → COMPRESSION

### 4.7 Bollinger Bantları

**Dosya:** `brain.py`  
**Periyot:** 20, 2σ

```
BB_middle = SMA(20)
BB_upper  = SMA(20) + 2 * σ
BB_lower  = SMA(20) - 2 * σ
```

### 4.8 MACD

**Dosya:** `brain.py`

```
EMA_fast(12), EMA_slow(26)
MACD_line   = EMA_fast - EMA_slow
Signal_line = EMA(MACD_line, 9)
Histogram   = MACD_line - Signal_line
```

---

## 5. Opsiyonlar Motoru

**Dosya:** `options_engine.py`  
**Kaynak:** Deribit (tek API çağrısı → tüm BTC/ETH opsiyonları)

### 5.1 Black-Scholes Greeks (Hull 11. Baskı)

Her opsiyon için hesaplanan:

| Greek | Formül | Anlam |
|---|---|---|
| **Delta** | N(d1) · e^(-qT) | Fiyat duyarlılığı |
| **Gamma** | N'(d1) · e^(-qT) / (S·σ·√T) | Delta değişim hızı |
| **Vega** | S · e^(-qT) · N'(d1) · √T | IV duyarlılığı |
| **Theta** | −S·e^(-qT)·N'(d1)·σ/(2√T) − rK·e^(-rT)·N(d2) | Zaman erozyonu |
| **Rho** | K·T·e^(-rT)·N(d2) | Faiz duyarlılığı |
| **Vanna** | −e^(-qT)·N'(d1)·d2/σ | Delta'nın IV'e göre türevi |
| **Charm** | Delta'nın zamana göre türevi | Delta'nın günlük bozunumu |

```
d1 = [ln(S/K) + (r-q+0.5σ²)T] / (σ√T)
d2 = d1 - σ√T
```

### 5.2 GEX — Gamma Exposure

```
GEX(strike) = gamma * OI * spot² * 0.01 * sign
sign = +1 (call), -1 (put)
```

**Toplam GEX:** Tüm strike'ların GEX toplamı

| GEX | Rejim | Piyasa Davranışı |
|---|---|---|
| > 0 | POZİTİF GAMMA | Market maker stabilizan — büyük hareket zor |
| < 0 | NEGATİF GAMMA | Market maker destabilize — trend güçlenir |

### 5.3 Seviyeler

| Seviye | Hesap |
|---|---|
| **Call Wall** | En yüksek call gamma OI'nin olduğu strike |
| **Put Wall** | En yüksek put gamma OI'nin olduğu strike |
| **Max Pain** | Opsiyon yazarlarının en az kayıp ettiği fiyat: Σ(payoff) minimum |
| **Zero Gamma** | GEX = 0 geçiş noktası (Brent metodu ile kök bulma) |

### 5.4 Implied Volatility

- **DVOL:** Deribit Volatility Index (mark_iv ortalaması)
- **ATM IV:** At-the-money opsiyon IV'si
- **Expected Move:** `spot * ATM_IV/100 * sqrt(days/365)`

### 5.5 Vade Dilimleri

| Dilim | Gün | Anlam |
|---|---|---|
| kisa | 0-7g | Bu haftanın opsiyonları (en güçlü pin riski) |
| orta | 8-45g | Aylık opsiyonlar |
| uzun | 45g+ | Kurumsal pozisyonlar |
| genel | Tümü | Birleşik GEX |

---

## 6. Tüm Agentlar — Detaylı Açıklama

### 6.1 Regime Engine (`regime_engine.py`)

**Amaç:** Günlük piyasa rejimini tespit eder.  
**Veri:** 1d OHLCV — Binance spot — son 35 bar  
**Çalışma:** `market_context.py` tarafından her 15 dakikada çağrılır

**Hesaplamalar:**
- ATR(14) → ATR%
- RSI(14)
- Realized Volatility (20 gün, yıllık)
- SMA(20)
- Son 5 günde kaç gün yukarı / aşağı kapandı

**Çıktı:**

| Rejim | Koşul |
|---|---|
| `PANIC` | RSI<28 + ≥3/4 gün aşağı + RV>80% |
| `HIGH_VOL` | RV>70% veya ATR%>4.5 |
| `TREND_UP` | Fiyat>SMA20×1.01 + RSI>52 + ≥3/4 gün yukarı |
| `TREND_DOWN` | Fiyat<SMA20×0.99 + RSI<48 + ≥3/4 gün aşağı |
| `RANGE` | Diğer tüm durumlar |

**OAR Uyarısı:** Her rejim için önerilen trade yaklaşımı metni döner.

---

### 6.2 Market Structure Agent (`market_structure_agent.py`)

**Amaç:** İntraday piyasa yapısını tespit eder; trend/range/expansion/compression.  
**Veri:** Futures OHLCV — 15m, 1h, 4h — son 100 bar  
**Çalışma:** `trading_supervisor.py` içinde paralel gather

**Hesaplamalar:**

**Swing High / Low (pencere=3):**
```
i noktası swing high ise: candles[i][high] >= tüm candles[i-3..i+3][high]
i noktası swing low ise:  candles[i][low]  <= tüm candles[i-3..i+3][low]
Son 6 pivot saklanır.
```

**HH/HL Zinciri:**
```
HH_HL: son_yh > önceki_yh VE son_dl > önceki_dl  → Yukarı trend yapısı
LH_LL: son_yh < önceki_yh VE son_dl < önceki_dl  → Aşağı trend yapısı
```

**BOS (Break of Structure):**
```
Son kapanış > son swing high → YUKARI_BOS
Son kapanış < son swing low  → ASAGI_BOS
```

**CHoCH (Change of Character):**
```
LH_LL zinciri + YUKARI_BOS → BULLISH_CHOCH  (trend dönüşü sinyali)
HH_HL zinciri + ASAGI_BOS  → BEARISH_CHOCH (trend dönüşü sinyali)
```

**Multi-Timeframe Hizalama (`coklu_timeframe_analiz`):**
- 15m + 1h + 4h aynı anda paralel çekilir
- ≥2 timeframe TREND_UP → `BULLISH_HIZALI`
- ≥2 timeframe TREND_DOWN → `BEARISH_HIZALI`
- Diğer → `KARISIK`

**Çıktı Durumları:**
`TREND_UP / TREND_DOWN / RANGE / EXPANSION / COMPRESSION`

---

### 6.3 Order Flow Agent (`order_flow_agent.py`)

**Amaç:** Gerçek alım-satım baskısını ölçer, kurumsal yönü tespit eder.  
**Veri:** 5m futures OHLCV (50 bar) + OI + Funding + Coinbase spot  
**Çalışma:** `trading_supervisor.py` içinde paralel gather

**Puanlama Tablosu (Toplam: -100 / +100):**

| Bileşen | Bullish | Bearish | Nötr |
|---|---|---|---|
| CVD | +30 | -30 | 0 |
| OI + CVD uyumu | +20 | -20 | +5 (OI artıyor ama yön belirsiz) |
| OI azalıyor | – | -10 | – |
| Funding: ASIRI_SHORT | +15 | – | – |
| Funding: HAFIF_LONG | +5 | – | – |
| Funding: ASIRI_LONG | – | -10 | – |
| Coinbase Premium POZİTİF | +20 | – | – |
| Coinbase Premium NEGATİF | – | -20 | – |
| Taker >%55 | +15 | – | – |
| Taker <%45 | – | -15 | – |

**Funding Rate Yorumu:**
```
rate > +0.05% → ASIRI_LONG   (long kalabalık, contrarian düşüş baskısı)
rate < -0.01% → ASIRI_SHORT  (short squeeze potansiyeli)
rate > +0.01% → HAFIF_LONG   (bullish nötr)
```

**Karar Eşiği:**
- puan ≥ +25 → `BULLISH_FLOW`
- puan ≤ -25 → `BEARISH_FLOW`
- Diğer → `NEUTRAL_FLOW`

---

### 6.4 Liquidity Agent (`liquidity_agent.py`)

**Amaç:** Likidite havuzlarını, stop avcılığını ve fakeout'ları tespit eder.  
**Veri:** 15m futures OHLCV — son 100 bar  
**Çalışma:** `trading_supervisor.py` içinde paralel gather

**Equal High / Equal Low (EQH / EQL):**
```
Tolerans: ±%0.05 fiyat farkı
Son 30 mum içinde birbirine bu kadar yakın swing high → stop avı havuzu
En fazla 3 EQH + 3 EQL saklanır (en yakın fark önce)
```

**SFP — Swing Failure Pattern:**
```
Mum high > önceki swing high AMA kapanış < swing high → BEARISH_SFP
Mum low  < önceki swing low  AMA kapanış > swing low  → BULLISH_SFP
```
Stop veya yanlış kırılım → ters hareket beklenebilir.

**Stop Cluster:**
```
En yüksek 5 high → STOP_ABOVE bölgesi (± ATR × 0.1)
En düşük  5 low  → STOP_BELOW bölgesi (± ATR × 0.1)
```
Bu bölgeler yoğun stop emri birikimi içerir, fiyat hızla geçer.

**Liquidity Sweep (Gerçekleşmiş):**
```
Son mum herhangi bir EQH/EQL seviyesini kırdı VE kapandı → SWEEP tespit
BULLISH_SWEEP: EQL kırıldı → muhtemelen alım fırsatı
BEARISH_SWEEP: EQH kırıldı → muhtemelen satış fırsatı
```

**Trading Supervisor'a Katkısı:**
- SFP stop seviyesi → R:R hesabı için kullanılır
- Sweep → trade yönünü güçlendirir

---

### 6.5 Session Agent (`session_agent.py`)

**Amaç:** Asia / London / New York seans analizini yaparak trade yönlendirmesi üretir.  
**Veri:** 15m futures OHLCV — son 96 bar (24 saat)  
**Çalışma:** `trading_supervisor.py` içinde paralel gather

**Seans Saatleri (UTC):**

| Seans | Başlangıç | Bitiş | Özellik |
|---|---|---|---|
| ASIA | 00:00 | 08:00 | Düşük volatilite, range oyunu |
| LONDON | 07:00 | 16:00 | En yüksek fakeout riski |
| NY | 13:00 | 22:00 | Trend kırılımları, yüksek hacim |

**OAR Seans Kuralları:**

1. **Asia BUY** → London'da continuation veya fakeout izle
2. **London range yapıldı** → NY'de bu range'in kırılması beklenir
3. **NY London'a zıt** → `REVERSAL_RISKI` (dikkat, trend bitmemiş olabilir)
4. **NY London'u onaylıyor** → `LONG_AKTIF` veya `SHORT_AKTIF`

**Trade Yönlendirme Çıktıları:**

| Çıktı | Anlam |
|---|---|
| `LONG_AKTIF` | NY London bullish devam ediyor |
| `SHORT_AKTIF` | NY London bearish devam ediyor |
| `LONG_ONCEKI` | Asia + London bullish hizalı, NY başlamadı |
| `SHORT_ONCEKI` | Asia + London bearish hizalı, NY başlamadı |
| `REVERSAL_RISKI` | NY London'a zıt → dikkat |
| `BEKLE` | Net sinyal yok |

**Trading Supervisor Filtresi:**  
Asia seansında aktifse trade açılmaz (`seans_filtresi_aktif=True` varsayılan).

---

### 6.6 Market Context Agent (`market_context.py`)

**Amaç:** Tüm verileri birleştirip 0-100 arası OAR Skor üretir.  
**Çalışma:** Her 15 dakikada `baglam_loop()` ile

**OAR Skor Bileşenleri:**

| Bileşen | Max Puan | Veri Kaynağı |
|---|---|---|
| CVD yönü | 15 | 5m futures CVD (son 12 bar) |
| OI değişimi | 10 | `open_interest` (5m) |
| Funding + Coinbase Premium | 15 | funding_rate + coinbase spot |
| Rejim (günlük) | 10 | `regime_engine.rejim_tespit()` |
| Move Source | 20 | Spot CVD vs Futures CVD karşılaştırması |
| OAR Yapı | 14-20 | BOS, CHoCH, seans kuralları |
| Opsiyon GEX | 0-10 | `options_engine.gex_ozet()` |

**Market Day Type (Gün Tipi):**
- `EXPANSION` — Genişleme günü
- `INSIDE` — Inside bar / sıkışma
- `TREND` — Tek yönlü akış
- `REVERSAL` — Dönüş günü
- `RANGE` — Range günü

---

### 6.7 Trading Supervisor (`trading_supervisor.py`)

**Amaç:** Beş agent + makro filtre → Final trade kararı.  
**Veri:** Tüm agentlardan paralel gather  
**Desteklenen modlar:** `scalper` (1:3 RR) ve `swing` (1:4 RR)

**3 Zorunlu Soru (hepsi geçmeli):**

**Soru 1 — Edge var mı?**
```
MTF hizalama = BULLISH_HIZALI  VE  order_flow = BULLISH_FLOW → LONG edge
MTF hizalama = BEARISH_HIZALI  VE  order_flow = BEARISH_FLOW → SHORT edge
```

**Soru 2 — R:R yeterli mi?**
```
scalper: minimum 1:3
swing:   minimum 1:4

Stop = SFP seviyesi (liquidity agent) veya son swing high/low
Hedef = yakın EQH/EQL veya yapı zirvesi/dibi
```

**Soru 3 — Invalidasyon seviyesi var mı?**
```
Stop seviyesi tanımlı mı? → EVET ise geçer
```

**Ekstra Filtreler:**

| Filtre | Koşul | Sonuç |
|---|---|---|
| Seans filtresi | Asia seansı aktif | `NO_TRADE` |
| Makro filtresi | FOMC/Triple Witching günü | `NO_TRADE` |
| Likidite filtresi | SFP veya Sweep zorunlu (opsiyonel) | `NO_TRADE` |

**Çıktı:**
```json
{
  "karar":    "TRADE_LONG | TRADE_SHORT | NO_TRADE",
  "guven":    0-100,
  "stop":     float,
  "hedef":    float,
  "rr":       float,
  "neden":    [...],
  "sembol":   "BTCUSDT",
  "mod":      "scalper"
}
```

---

### 6.8 Leader Agent (`leader_agent.py`)

**Amaç:** AI destekli sinyal analizi, kombolu backtest, saatlik raporlar.  
**Model:** Gemini 2.5 Flash  
**Çalışma:** 3 farklı arka plan loop

**Loop'lar:**
1. `sinyal_toplayici_loop` — Her 4 dakikada tüm stratejilerden sinyal toplar (başlangıç gecikmesi: 4 dakika)
2. `saatlik_backtest_loop` — Her saatte backtest sinyallerini kombolar + AI analizi
3. `saatlik_lider_raporu_loop` — Her saatte lider sinyal + AI özet (başlangıç gecikmesi: 15 dakika)

**Backtest — Sadece BTC/ETH Kombolar:**
- 500 mumlu 4h veri
- RSI+MACD+Bollinger kombinasyonu
- Kâr/zarar istatistikleri
- Kombo sinyal yoksa rapor atlanır (gereksiz mesaj gönderilmez)

---

### 6.9 Brain / Scanner (`brain.py`)

**Amaç:** 10 sembolü otomatik tarar, basit strateji sinyalleri üretir.  
**Veri:** 1h OHLCV — 200 bar  
**Çalışma:** Her 15 dakikada (başlangıç gecikmesi: 6 dakika)

**İzleme Listesi:**
```
BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT
ADAUSDT, DOGEUSDT, AVAXUSDT, LINKUSDT, DOTUSDT
```

**Stratejiler:**
- `rsi_macd` — RSI aşırı alım/satım + MACD histogram
- `bollinger` — BB alt/üst banttan dönüş

**Rate limit:** Semboller arası 0.2 saniye bekleme.

---

### 6.10 Paper Trade Agent (`paper_trade_agent.py`)

**Amaç:** Trading Supervisor kararlarını gerçek para kullanmadan ileri test eder.  
**Çalışma:** Arka planda sürekli

**Takip edilenler:**
- Açık pozisyon sayısı
- Kâr/zarar
- Win rate
- Gerçekleşen R:R

---

### 6.11 Lider Anlık Yorum (`lider_anlik_yorum.py`)

**Amaç:** BTC ve ETH'de anlamlı değişim olduğunda Telegram'a AI yorumu gönderir.  
**Çalışma:** 5 dakikalık döngü  
**Hedef semboller:** Yalnızca BTCUSDT ve ETHUSDT

**Tetikleyiciler (herhangi biri yeterli):**

| Tetikleyici | Eşik | Açıklama |
|---|---|---|
| Fiyat hareketi | ≥%1.0 (15 dakika) | Hızlı fiyat değişimi |
| OI değişimi | ≥%2.0 | Büyük pozisyon açılması/kapanması |
| Funding mutlak değeri | ±0.03% geçişi | Aşırı yanlılık başlangıcı |
| OAR skor atlaması | ±10 puan | Bağlam kayması |
| GEX rejim değişimi | Pozitif↔Negatif | Gamma flip |
| CVD yön değişimi | Bullish↔Bearish | Alım/satım baskısı dönüşü |
| CB Premium işaret değişimi | +→- veya -→+ | Kurumsal alım/satış değişimi |

**Hız Sınırlama:** Aynı sembol için minimum 20 dakika ara.  
**Durum Dosyası:** `lider_anlik_durum.json`

**Toplanan Veri (`_veri_topla`):**
- Anlık fiyat (ticker)
- OAR Skor (market_context)
- Order Flow (CVD, OI, Funding, CB Premium, Taker)
- Market Structure (MTF yapı, BOS, CHoCH, ADX)
- GEX + Call/Put Wall + Zero Gamma (options_engine)
- Seans durumu (session_agent)
- Makro risk (time_context)

---

## 7. Trading Supervisor — Karar Motoru

Tam pipeline (paralel):

```
asyncio.gather(
    coklu_timeframe_analiz(sembol),    # 15m + 1h + 4h yapı
    order_flow_analiz(sembol),         # CVD + OI + Funding + CBP + Taker
    liquidity_analiz(sembol),          # EQH/EQL + SFP + Stop Cluster
    session_analiz(sembol),            # Asia/London/NY
    time_risk_skoru(),                 # Makro risk
)
```

Karar ağacı:
```
1. Seans filtresi → Asia? → NO_TRADE
2. Makro filtresi → KRİTİK? → NO_TRADE
3. Soru 1: Edge var mı? → Yok → NO_TRADE
4. Soru 2: R:R yeterli? → Hayır → NO_TRADE
5. Soru 3: Stop seviyesi? → Yok → NO_TRADE
6. Hepsi geçti → TRADE_LONG veya TRADE_SHORT
```

---

## 8. Zaman ve Makro Bağlam

**Dosya:** `time_context.py`

**Takip edilen olaylar:**

| Olay | Base Puan | Neden Kritik |
|---|---|---|
| FOMC Toplantısı | 22 | Fed faiz kararı → volatilite |
| Triple Witching | 28 | Hisse + endeks + opsiyon vadesi aynı anda |
| BTC/ETH Opsiyon Vadesi | 18 | Her ayın son Cuması — Deribit |
| US Tatili | 12 | Düşük likidite |
| Cuma günü | +12 | Hafta sonu riski |
| Hafta sonu | +18 | Gap riski |

**Gün Mesafesi Çarpanları:**
```
Aynı gün:    × 1.0
+1 gün önce: × 0.7
-1 gün sonra:× 0.5
±2-3 gün:    × 0.15
```

**Risk Seviyeleri:**
```
> 65 → KRİTİK   → Trading Supervisor NO_TRADE döner
> 40 → YÜKSEK   → Stop genişlet uyarısı
> 20 → ORTA
≤ 20 → DÜŞÜK
```

**FOMC Tarihleri (2025-2026):** 2025 için 8, 2026 için 8 toplantı tarihi hardcoded.

---

## 9. Seans Analizi

**Dosya:** `session_agent.py`

Her seans için hesaplanan:
- `high` — Seans içi en yüksek fiyat
- `low` — Seans içi en düşük fiyat
- `range` — high - low (dolar)
- `acilis` — İlk mumun open'ı
- `kapanis` — Son mumun close'u
- `yon` — BULLISH / BEARISH / NOTR
- `mum_sayisi` — Kaç 15m mum düştü

**OAR Ticaretinde Seans Önem Sırası:**
1. **London açılışı (07:00-09:00 UTC)** — En tehlikeli; fakeout sık görülür
2. **NY açılışı (13:30-15:00 UTC)** — En yüksek hacim ve trend kırılımları
3. **Asia (00:00-08:00 UTC)** — Range oyunu, yeni trade açılmaz

---

## 10. Telegram Entegrasyonu

**Dosya:** `main.py`

**Mesaj Filtresi (çok katı):**
- Sadece `tip == "karar"` raporlar gönderilir
- Sadece BTC ve ETH (BTCUSDT, ETHUSDT)
- Minimum güven: %70
- Karar: `LONG` veya `SHORT` (NO_TRADE gönderilmez)

**Gönderilmeyenler:**
- Backtest sonuçları (birleşik sinyal yoksa zaten oluşmuyor)
- Sistem sağlığı mesajları (sadece `/bot` komutunda)
- Nötr veya düşük güven sinyalleri
- BTC/ETH dışı semboller
- Sinyal üretilmedi bildirimleri

**Hash Sistemi (tekrar önleme):**
```python
anahtar = MD5(sembol + karar + konfidans + tarih)
```
Aynı sinyal + aynı tarih → ikinci kez gönderilmez.

**`/bot` Komutu:**
- Exchange sağlığı: Binance Spot, Binance Futures, Bybit durumu
- Bot kaynakları: Her birinin OK/KAPALI durumu + son sinyal zamanı
- Son 3 BTC/ETH LONG/SHORT kararı

---

## 11. Anlık AI Yorum Sistemi

**Dosya:** `lider_anlik_yorum.py`

**Gemini Prompt Yapısı:**
```
Görev: BTC/ETH piyasasını analiz et.

Fiyat: {fiyat}  OAR Skor: {skor}
CVD: {yon} ({ivme})
OI: {yon} ({pct}%)
Funding: {taraflilik} ({rate}%)
GEX: {rejim} | Call Wall: {cw} | Put Wall: {pw}
Seans: {aktif} | Yönlendirme: {yonlendirme}
Makro Risk: {seviye}

Tetikleyiciler: {liste}

3-5 cümle Türkçe yorum. Spekülasyon değil, veri odaklı.
```

**Telegram Mesaj Formatı:**
```
🔍 BTC — Anlık Analiz

Fiyat: $95,420 | OAR: 68
CVD: BULLISH (+0.0234)
OI: ARTIYOR (+2.3%)
Funding: HAFIF_LONG (0.01%)
GEX: NEGATİF GAMMA | CW: 96k | PW: 90k

Seans: NY | Yönlendirme: LONG_AKTIF
Makro: DÜŞÜK

Tetik: Fiyat +1.2%, CVD BULLISH'e döndü

💬 [AI yorumu 3-5 cümle]
```

---

## 12. Veri Akışı — Uçtan Uca

```
EXCHANGE LAYER
  Binance Futures API ──────────────────────────────┐
  Binance Spot API ─────────────────────────────────┤
  Bybit API (fallback) ─────────────────────────────┤──→ exchange_client.py
  Deribit API ──────────────────────────────────────┤    (retry + fallback)
  Coinbase Exchange API ────────────────────────────┘

INDICATOR LAYER (her agent kendi hesaplar)
  OHLCV → ATR, RSI, SMA, MACD, Bollinger
  OHLCV → CVD (Kaygın formülü)
  OI    → OI% değişimi
  Fund  → Funding yorumu
  CB    → Coinbase Premium
  Deribit → Black-Scholes Greeks → GEX, IV, Max Pain, Call/Put Wall

AGENT LAYER (paralel)
  regime_engine      → Günlük rejim (1d)
  market_structure   → MTF yapı 15m/1h/4h (swing, BOS, CHoCH, ADX)
  order_flow         → CVD+OI+Funding+CB+Taker → BULLISH/BEARISH/NEUTRAL
  liquidity          → EQH/EQL, SFP, Stop Cluster, Sweep
  session            → Asia/London/NY saatleri + OAR kuralları
  time_context       → FOMC/Witching/Expiry risk skoru
  options_engine     → GEX rejimi, seviyeleri

DECISION LAYER
  trading_supervisor → 3 soru + 3 filtre → TRADE_LONG/SHORT/NO_TRADE

OUTPUT LAYER
  Telegram           → Sadece BTC/ETH LONG/SHORT ≥%70 güven
  lider_anlik_yorum  → 7 tetikleyiciden biri varsa AI yorum
  paper_trade        → Gerçeksiz forward test
  rapor_gecmisi      → SQLite/JSON persistence
```

---

## Özet Tablo

| Kategori | Kullanılan |
|---|---|
| **Exchange'ler** | Binance, Bybit, Deribit, Coinbase |
| **AI Model** | Google Gemini 2.5 Flash |
| **Hacim Göstergeleri** | CVD, Taker Oranı, OI değişimi, Opsiyon CVD |
| **Trend Göstergeleri** | ADX, RSI, SMA20/50, Realized Volatility |
| **Volatilite** | ATR, ATR%, Realized Vol, Implied Vol (IV), DVOL |
| **Fiyat Yapısı** | Swing High/Low, BOS, CHoCH, HH/HL zinciri |
| **Opsiyonlar** | GEX, Call Wall, Put Wall, Max Pain, Zero Gamma, Greeks |
| **Likidite** | EQH/EQL, SFP, Stop Cluster, Sweep |
| **Seans** | Asia/London/NY seans analizi, OAR kuralları |
| **Makro** | FOMC, Triple Witching, Opsiyon vadesi, US tatilleri |
| **Timeframe'ler** | 5m (CVD), 15m (session, OAR), 1h (yapı, scan), 4h (backtest), 1d (rejim) |
| **Semboller** | 10 watchlist (AI yorum: BTC+ETH, Telegram: BTC+ETH) |
