# OAR PREMIUM — MİMARİ (ARCHITECTURE)

Gerçek sistem haritası. Son güncelleme: 2026-06-15.

## YIĞIN (STACK)
- **Backend:** Python + FastAPI (`main.py`, ~78 endpoint)
- **Frontend:** Tek dosya `static/live.html` (7 ekranlı quant OS, vanilla JS + Lightweight Charts)
- **AI:** Gemini 2.5 Flash → Groq llama-3.3-70b fallback
- **Kalıcı veri:** `/var/data` (Render disk) — JSON + SQLite
- **Deploy:** GitHub `OnrGrafik/oar-premium` → Render otomatik (Starter, 512MB RAM, 1GB disk)
- **Env:** GEMINI_API_KEY, GROQ_API_KEY, FRED_API_KEY, COINGECKO_API_KEY (opsiyonel: RENDER_API_KEY, GITHUB_TOKEN)

## DOSYALAR (hepsi repo KÖKÜNDE)
| Dosya | Görev |
|---|---|
| `main.py` | Tüm endpoint'ler + startup (dayanıklı mod, her loop try/except) |
| `leader_agent.py` | Lider+Research+Backtest agent, BOT_KATALOG, shared memory, saatlik raporlar, sinyal döngüleri |
| `indicator_engine.py` | ~30 ücretsiz Binance indikatörü + ağırlıklı -100/+100 skor. `analiz(symbol,interval)` |
| `options_engine.py` | Deribit Black-Scholes (Hull 11e): GEX, CW/PW/ZG, MaxPain, Greekler (Γ/𝒱/𝒞), IV skew, 25Δ RR, 3×CVD, strike topografya |
| `macro_engine.py` | BLS/FRED/Treasury/Yahoo — 9 makro gösterge + BTC etkisi + carry trade (USD/JPY, JGB, Nikkei, VIX, BoJ). 5dk cache |
| `theory_engine.py` | Otomatik hipotez üreteci (tüm fib × indikatör × dönem), `tum_enstruman_tara`, `hipotez_loop` |
| `market_context.py` | Market Regime, OAR Score, Move Source. 15dk günceller, shared memory'ye yazar |
| `feature_engine.py` | Sinyal zenginleştirme + WIN/LOSS profil + OAR Pattern üretici |
| `basari_skoru.py` | Win rate YERİNE: sinyal sonrası max % + 10$/100x max $. Haftalık sıfırlanır |
| `kitap_db.py` | SQLite+FTS5 kitap DB (`kitaplar.db`). `ara()` FTS5 + LIKE fallback |
| `theory_lab.py` | OAR-001..010 teorileri (Draft→Testing→Confirmed/Rejected) |
| `devops_monitor.py` | Render API + GitHub API (token opsiyonel) |
| `historical_backtest.py`, `bots.py`, `brain.py` (get_ohlcv), `knowledge.py`, `memory.py`, `start.py` | Yardımcılar |
| `render.yaml` | startCommand `$PORT` kullanır, disk `/var/data` 1GB |
| `requirements.txt` | fastapi/uvicorn/httpx/python-multipart/pypdf/pdfminer.six/pdfplumber |
| `static/live.html` | 7 ekranlı arayüz |
| `static/index.html` | Eski sohbet (/chat) |

## 7 EKRAN (sol menü sırası)
1. **Komuta Merkezi** — Piyasa Durumu (3 başlık: Teknik/Temel/Psikoloji + kitap kaynakları), ASIA RANGE BTC/ETH 5M grafik (OAR fib + CW/PW/ZG), grafik altı canlı yorum, 5M indikatör skoru, opsiyon genel durum, çoklu bot teyidi
2. **Opsiyon & GEX** — Pozisyon·Analiz, Strike Topografyası (hover), Kuantum Duvarlar, 3×CVD, işlem dağılımı, Toplu Greekler, IV skew
3. **Makro Ekonomi** — 9 gösterge + BTC etkisi + AI özet + carry trade monitörü
4. **Canlı Panel** — agent feed + Lider sohbet (sohbet başa dönmüyor: updateFeedOnly/updateChatOnly)
5. **Agentlar & Botlar** — iletişim ağı üstte + başarı skoru (win rate yok) + haftalık MAX%/MAX$ tabloları
6. **Theory Lab** — otomatik hipotez (coin/fib seçimi yok) + tüm sayfalardan teori + Research/Lider yorum
7. **Bilgi/Hafıza** — kütüphane + FTS5 arama + Shared Memory akışı + DevOps gömülü (ayrı sayfa yok)

## ÖNEMLİ ENDPOINT'LER
`/api/ticker` (7 sabit sembol), `/api/piyasa-durumu` (3 başlık+kitap), `/api/grafik-yorum`,
`/api/opsiyon-yorum`, `/api/indicators`, `/api/options/{topografya|greekler|skew|cvd-uclu|islem-dagilimi|levels|gex}`,
`/api/market-context`, `/api/makro` + `/api/makro/{ozet|carry}`, `/api/basari-skoru`,
`/api/theory/{hipotezler|tara|yorum}`, `/api/oar-fib`, `/api/ohlcv`, `/api/leader/chat`,
`/api/knowledge/{import|kitaplar|kitap-temizle|kitap-ara}`, `/api/devops`

## STARTUP LOOP'LARI (dayanıklı mod, her biri try/except)
background_scanner, sinyal_toplayici, sinyal_degerlendirici, saatlik_lider_raporu,
saatlik_backtest, saatlik_research, zenginlestirici, pattern_sinyal, baglam_loop,
skor_loop (30dk), hipotez_loop (24s)

## VERİ KAYNAKLARI (hepsi ücretsiz)
- Binance (spot+futures): fiyat, OHLCV, indikatör, OI, funding
- Deribit: opsiyon zinciri, GEX, Greekler, opsiyon CVD
- Coinbase: premium
- Yahoo Finance: SP500/Nasdaq/Altın/Gümüş/VIX + carry trade
- BLS/FRED/US Treasury: makro göstergeler
- alternative.me: korku endeksi
