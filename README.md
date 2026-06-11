# 🤖 Crypto AI Agent — Google Gemini

**Tamamen ücretsiz** kripto yapay zeka asistanı.
Gemini 2.0 Flash + Binance + CoinGecko

---

## ⚡ Başlatma (3 adım)

### 1. Python kur (yoksa)
→ https://python.org → Python 3.11+

### 2. Bu klasörü aç, terminalde çalıştır:
```
python start.py
```

### 3. API Key al (ücretsiz, kart yok):
→ https://aistudio.google.com/apikey

Script açıldığında zaten sorar, direkt yapıştır.

---

## 💰 Ücret Durumu

| Servis | Limit | Ücret |
|--------|-------|-------|
| Gemini 2.0 Flash | 1500 istek/gün, 1M token/dk | **ÜCRETSİZ** |
| Binance API | Sınırsız | **ÜCRETSİZ** |
| CoinGecko API | 30 istek/dk | **ÜCRETSİZ** |
| Alternative.me F&G | Sınırsız | **ÜCRETSİZ** |

Günde 1500 mesaj yeterli değilse:
→ Google AI Studio'da fatura ekle → sınır kalkar, çok ucuz olur

---

## 🎯 Özellikler

- 📊 **Canlı Kripto Verisi** — Binance + CoinGecko
- 🖼️ **Grafik/Resim Analizi** — Chart yükle, AI yorumlasın
- 📄 **Dosya Analizi** — PDF, TXT, CSV okur
- 🌡️ **Korku/Açgözlülük** — Endeks göstergesi
- 💬 **Bağlamsal Sohbet** — Geçmişi hatırlar
- ⚡ **Hızlı Sorgular** — Sidebar butonu ile tek tık

---

## 📁 Dosyalar

```
crypto-agent-gemini/
├── main.py          ← FastAPI backend
├── start.py         ← Kurulum & başlatma
├── requirements.txt ← Bağımlılıklar
├── .env             ← API key (otomatik oluşur)
└── static/
    └── index.html   ← Arayüz
```
