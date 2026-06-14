"""
Crypto AI Agent - Google Gemini Powered
Tamamen ücretsiz: Gemini 2.5 Flash + Binance + CoinGecko + Deribit Opsiyonları
"""

import os
import base64
import json
import httpx
import asyncio
from pathlib import Path

import os as _os_data
DATA_DIR = Path(_os_data.environ.get("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from brain import (
    scan_market, quick_backtest, get_accuracy_stats,
    add_alert, load_json, save_json,
    SIGNALS_FILE, BACKTEST_FILE, ALERTS_FILE, MEMORY_FILE
)
from leader_agent import (
    rapor_uret, son_rapor,
    backtest_sinyal_analizi, research_analizi,
    ai_yorum_uret, bot_katalog_al,
    sabah_raporu_loop
)
from bots import (
    list_sources, add_source, delete_source,
    poll_all_sources, evaluate_signals, get_bot_stats, get_recent_signals
)
from brain import get_ohlcv
from knowledge import (
    add_document, add_note, search_knowledge,
    get_context_for_prompt, list_knowledge,
    delete_document, delete_note, clear_category,
    log_exchange, search_conversations, get_full_context, get_memory_stats
)
from memory import (
    add_memory, search_memories, get_memory_stats, delete_memory,
    index_document, search_knowledge, get_knowledge_list, delete_document,
    add_note, get_notes, build_context_for_query, detect_learn_intent
)

app = FastAPI(title="Crypto AI Agent - Gemini")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
Path("static").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ─── Arka Plan Tarayıcı ──────────────────────────────────────────────────────
scanner_task = None

async def background_scanner():
    """Her 15 dakikada piyasayı otomatik tara"""
    await asyncio.sleep(10)  # Başlangıçta 10sn bekle
    while True:
        try:
            await scan_market("1h")
        except Exception as e:
            print(f"[Scanner] Hata: {e}")
        await asyncio.sleep(900)  # 15 dakika

@app.on_event("startup")
async def startup_event():
    global scanner_task
    scanner_task = asyncio.create_task(background_scanner())
    print("🧠 Otonom tarayıcı başlatıldı (her 15dk)")

@app.on_event("shutdown")
async def shutdown_event():
    if scanner_task:
        scanner_task.cancel()

# ─── Çoklu API Sistemi: Gemini → Gemini Lite → Groq ─────────────────────────
GEMINI_MODEL      = "gemini-2.5-flash"
GEMINI_MODEL_LITE = "gemini-2.5-flash-lite"
GEMINI_BASE       = "https://generativelanguage.googleapis.com/v1beta"
GROQ_MODEL        = "llama-3.3-70b-versatile"
GROQ_BASE         = "https://api.groq.com/openai/v1/chat/completions"

def _read_env_key(name: str) -> str:
    key = os.environ.get(name, "")
    if not key and Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if line.startswith(f"{name}="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                os.environ[name] = key
                break
    return key

def get_gemini_key():
    key = _read_env_key("GEMINI_API_KEY")
    if not key:
        raise HTTPException(status_code=500,
            detail="GEMINI_API_KEY bulunamadı. .env dosyasına ekleyin.")
    return key

def get_groq_key():
    return _read_env_key("GROQ_API_KEY")  # Opsiyonel — yoksa fallback atlanır

def _is_overloaded(detail: str) -> bool:
    d = detail.lower()
    return any(k in d for k in ["high demand", "overloaded", "503", "resource_exhausted",
                                 "try again", "rate limit", "quota", "unavailable", "429"])

async def _gemini_request(api_key: str, model: str, contents: list,
                          system_instruction: str = "") -> str:
    url = f"{GEMINI_BASE}/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": contents,
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192, "topP": 0.95, "thinkingConfig": {"thinkingBudget": 512}}
    }
    if system_instruction:
        payload["system_instruction"] = {"parts": [{"text": system_instruction}]}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=payload)
        if r.status_code != 200:
            try:
                err = r.json().get('error', {}).get('message', r.text)
            except Exception:
                err = r.text
            raise RuntimeError(err)
        data = r.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Boş yanıt")
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)

def _contents_to_openai(contents: list, system_instruction: str) -> list:
    """Gemini formatını OpenAI formatına çevir (sadece metin)"""
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    for c in contents:
        role = "user" if c.get("role") == "user" else "assistant"
        text = " ".join(p.get("text", "") for p in c.get("parts", []) if "text" in p)
        if text.strip():
            messages.append({"role": role, "content": text})
    return messages

async def _groq_request(api_key: str, contents: list, system_instruction: str = "") -> str:
    messages = _contents_to_openai(contents, system_instruction)
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(GROQ_BASE,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"model": GROQ_MODEL, "messages": messages,
                  "temperature": 0.4, "max_tokens": 4096})
        if r.status_code != 200:
            try:
                err = r.json().get('error', {}).get('message', r.text)
            except Exception:
                err = r.text
            raise RuntimeError(err)
        return r.json()["choices"][0]["message"]["content"]

async def _gemini_stream(api_key: str, model: str, contents: list,
                         system_instruction: str = ""):
    """SSE streaming — parça parça metin üretir"""
    url = f"{GEMINI_BASE}/models/{model}:streamGenerateContent?alt=sse&key={api_key}"
    payload = {
        "contents": contents,
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192, "topP": 0.95, "thinkingConfig": {"thinkingBudget": 512}}
    }
    if system_instruction:
        payload["system_instruction"] = {"parts": [{"text": system_instruction}]}
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", url, json=payload) as r:
            if r.status_code != 200:
                body = await r.aread()
                raise RuntimeError(body.decode("utf-8", errors="ignore")[:300])
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:].strip()
                if raw == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                    for cand in chunk.get("candidates", []):
                        for part in cand.get("content", {}).get("parts", []):
                            if part.get("text"):
                                yield part["text"]
                except Exception:
                    continue

async def stream_ai(api_key: str, contents: list, system_instruction: str = ""):
    """Streaming + fallback zinciri. Yield: text parçaları. İlk yield öncesi model seçilir."""
    has_files = any("inline_data" in p for c2 in contents for p in c2.get("parts", []))
    # 1) Gemini Flash stream
    try:
        got_any = False
        async for t in _gemini_stream(api_key, GEMINI_MODEL, contents, system_instruction):
            got_any = True
            globals()["_last_model_used"] = GEMINI_MODEL
            yield t
        if got_any:
            return
    except RuntimeError as e:
        if not _is_overloaded(str(e)):
            raise HTTPException(status_code=502, detail=f"Gemini hatası: {str(e)[:200]}")
    # 2) Gemini Lite stream
    try:
        got_any = False
        async for t in _gemini_stream(api_key, GEMINI_MODEL_LITE, contents, system_instruction):
            got_any = True
            globals()["_last_model_used"] = GEMINI_MODEL_LITE
            yield t
        if got_any:
            return
    except RuntimeError:
        pass
    # 3) Groq (tek parça)
    groq_key = get_groq_key()
    if groq_key and not has_files:
        try:
            result = await _groq_request(groq_key, contents, system_instruction)
            globals()["_last_model_used"] = f"groq/{GROQ_MODEL}"
            yield result
            return
        except RuntimeError:
            pass
    raise HTTPException(status_code=503, detail="Tüm API'ler yoğun, 30sn sonra tekrar deneyin.")

async def call_gemini(api_key: str, contents: list, system_instruction: str = "") -> str:
    """Çoklu sağlayıcı zinciri: Gemini → Gemini Lite → Groq.
       Dosya (resim/PDF) varsa Groq atlanır (görüntü desteklemiyor)."""
    has_files = any("inline_data" in p for c in contents for p in c.get("parts", []))
    last_err = ""

    # 1. Gemini 2.5 Flash
    try:
        result = await _gemini_request(api_key, GEMINI_MODEL, contents, system_instruction)
        globals()["_last_model_used"] = GEMINI_MODEL
        return result
    except RuntimeError as e:
        last_err = str(e)
        if not _is_overloaded(last_err):
            raise HTTPException(status_code=502, detail=f"Gemini API hatası: {last_err}")
        print(f"[Fallback] Gemini yoğun → Lite deneniyor")

    # 2. Gemini 2.5 Flash-Lite
    try:
        result = await _gemini_request(api_key, GEMINI_MODEL_LITE, contents, system_instruction)
        globals()["_last_model_used"] = GEMINI_MODEL_LITE
        return result
    except RuntimeError as e:
        last_err = str(e)
        print(f"[Fallback] Gemini Lite de yoğun → Groq deneniyor")

    # 3. Groq (sadece metin)
    groq_key = get_groq_key()
    if groq_key and not has_files:
        try:
            result = await _groq_request(groq_key, contents, system_instruction)
            globals()["_last_model_used"] = f"groq/{GROQ_MODEL}"
            return result
        except RuntimeError as e:
            last_err = str(e)

    raise HTTPException(status_code=503,
        detail=f"Tüm API'ler yoğun. Son hata: {last_err}. Lütfen 30sn sonra tekrar deneyin.")

_last_model_used = GEMINI_MODEL

# ─── Binance ────────────────────────────────────────────────────────────────
async def get_binance_ticker(symbol: str) -> dict:
    """Çoklu borsa: Binance → OKX → Bybit (ilk çalışan kazanır)"""
    sym = symbol.upper().replace("/", "")
    if not sym.endswith("USDT"):
        sym += "USDT"
    base = sym.replace("USDT", "")
    async with httpx.AsyncClient(timeout=8) as c:
        # 1) Binance
        try:
            r = await c.get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}")
            if r.status_code == 200:
                d = r.json()
                return {"source": "Binance", "symbol": d["symbol"],
                        "price": float(d["lastPrice"]),
                        "change_24h_pct": float(d["priceChangePercent"]),
                        "high_24h": float(d["highPrice"]), "low_24h": float(d["lowPrice"]),
                        "volume": float(d["volume"]),
                        "quote_volume_usdt": float(d["quoteVolume"])}
        except Exception:
            pass
        # 2) OKX
        try:
            r = await c.get(f"https://www.okx.com/api/v5/market/ticker?instId={base}-USDT")
            if r.status_code == 200:
                items = r.json().get("data", [])
                if items:
                    d = items[0]
                    last = float(d["last"]); open24 = float(d["open24h"]) or last
                    return {"source": "OKX", "symbol": sym, "price": last,
                            "change_24h_pct": round((last-open24)/open24*100, 2),
                            "high_24h": float(d["high24h"]), "low_24h": float(d["low24h"]),
                            "volume": float(d["vol24h"]),
                            "quote_volume_usdt": float(d.get("volCcy24h", 0))}
        except Exception:
            pass
        # 3) Bybit
        try:
            r = await c.get(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={sym}")
            if r.status_code == 200:
                items = r.json().get("result", {}).get("list", [])
                if items:
                    d = items[0]
                    return {"source": "Bybit", "symbol": sym,
                            "price": float(d["lastPrice"]),
                            "change_24h_pct": round(float(d["price24hPcnt"])*100, 2),
                            "high_24h": float(d["highPrice24h"]), "low_24h": float(d["lowPrice24h"]),
                            "volume": float(d["volume24h"]),
                            "quote_volume_usdt": float(d["turnover24h"])}
        except Exception:
            pass
    return {}

# ─── CoinGecko ──────────────────────────────────────────────────────────────
def _cg_headers():
    k = os.environ.get("COINGECKO_API_KEY", "")
    return {"x-cg-demo-api-key": k} if k else {}

async def get_coingecko_markets(coins: int = 15) -> list:
    async with httpx.AsyncClient(timeout=12) as c:
        try:
            r = await c.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={"vs_currency": "usd", "order": "market_cap_desc",
                        "per_page": coins, "page": 1,
                        "price_change_percentage": "1h,24h,7d"},
                headers=_cg_headers()
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
    return []

async def get_coingecko_coin(coin_id: str) -> dict:
    async with httpx.AsyncClient(timeout=12) as c:
        try:
            r = await c.get(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}",
                params={"localization": "false", "tickers": "false",
                        "community_data": "false", "developer_data": "false"},
                headers=_cg_headers()
            )
            if r.status_code == 200:
                d = r.json()
                md = d.get("market_data", {})
                return {
                    "name": d.get("name"),
                    "symbol": d.get("symbol", "").upper(),
                    "rank": d.get("market_cap_rank"),
                    "price_usd": md.get("current_price", {}).get("usd", 0),
                    "market_cap_usd": md.get("market_cap", {}).get("usd", 0),
                    "ath_usd": md.get("ath", {}).get("usd", 0),
                    "ath_change_pct": md.get("ath_change_percentage", {}).get("usd", 0),
                    "change_1h": md.get("price_change_percentage_1h_in_currency", {}).get("usd", 0),
                    "change_24h": md.get("price_change_percentage_24h", 0),
                    "change_7d": md.get("price_change_percentage_7d", 0),
                    "change_30d": md.get("price_change_percentage_30d", 0),
                    "total_supply": md.get("total_supply"),
                    "circulating_supply": md.get("circulating_supply"),
                }
        except Exception:
            pass
    return {}

async def get_fear_greed() -> dict:
    async with httpx.AsyncClient(timeout=8) as c:
        try:
            r = await c.get("https://api.alternative.me/fng/?limit=3")
            if r.status_code == 200:
                items = r.json()["data"]
                return {
                    "today": {"value": int(items[0]["value"]), "label": items[0]["value_classification"]},
                    "yesterday": {"value": int(items[1]["value"]), "label": items[1]["value_classification"]},
                }
        except Exception:
            pass
    return {}

async def get_global_stats() -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.get("https://api.coingecko.com/api/v3/global")
            if r.status_code == 200:
                d = r.json()["data"]
                return {
                    "total_market_cap_trillion": d.get("total_market_cap", {}).get("usd", 0) / 1e12,
                    "total_volume_24h_billion": d.get("total_volume", {}).get("usd", 0) / 1e9,
                    "btc_dominance_pct": round(d.get("market_cap_percentage", {}).get("btc", 0), 1),
                    "eth_dominance_pct": round(d.get("market_cap_percentage", {}).get("eth", 0), 1),
                    "active_cryptocurrencies": d.get("active_cryptocurrencies", 0),
                    "market_cap_change_24h_pct": round(d.get("market_cap_change_percentage_24h_usd", 0), 2),
                }
        except Exception:
            pass
    return {}

# ─── Deribit Opsiyon API (Ücretsiz, kayıt gerekmez) ─────────────────────────
async def get_deribit_instruments(currency: str = "BTC") -> list:
    """Aktif opsiyon enstrümanlarını listele"""
    async with httpx.AsyncClient(timeout=12) as c:
        try:
            r = await c.get(
                "https://www.deribit.com/api/v2/public/get_instruments",
                params={"currency": currency, "kind": "option", "expired": "false"}
            )
            if r.status_code == 200:
                return r.json().get("result", [])
        except Exception:
            pass
    return []

async def get_deribit_ticker(instrument: str) -> dict:
    """Tek enstrüman fiyatı"""
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.get(
                "https://www.deribit.com/api/v2/public/ticker",
                params={"instrument_name": instrument}
            )
            if r.status_code == 200:
                return r.json().get("result", {})
        except Exception:
            pass
    return {}

async def get_deribit_summary(currency: str = "BTC") -> dict:
    """Piyasa geneli opsiyon özeti — IV, PCR, açık pozisyon"""
    async with httpx.AsyncClient(timeout=12) as c:
        try:
            r = await c.get(
                "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                params={"currency": currency, "kind": "option"}
            )
            if r.status_code == 200:
                items = r.json().get("result", [])
                if not items:
                    return {}
                total_oi   = sum(x.get("open_interest", 0) for x in items)
                call_oi    = sum(x.get("open_interest", 0) for x in items if "-C" in x.get("instrument_name",""))
                put_oi     = sum(x.get("open_interest", 0) for x in items if "-P" in x.get("instrument_name",""))
                total_vol  = sum(x.get("volume", 0) for x in items)
                call_vol   = sum(x.get("volume", 0) for x in items if "-C" in x.get("instrument_name",""))
                put_vol    = sum(x.get("volume", 0) for x in items if "-P" in x.get("instrument_name",""))
                ivs        = [x.get("mark_iv", 0) for x in items if x.get("mark_iv", 0) > 0]
                avg_iv     = round(sum(ivs) / len(ivs), 2) if ivs else 0
                pcr_oi     = round(put_oi / call_oi, 3) if call_oi > 0 else 0
                pcr_vol    = round(put_vol / call_vol, 3) if call_vol > 0 else 0
                return {
                    "currency": currency,
                    "total_open_interest": round(total_oi, 2),
                    "call_open_interest": round(call_oi, 2),
                    "put_open_interest": round(put_oi, 2),
                    "pcr_oi": pcr_oi,
                    "total_volume_24h": round(total_vol, 2),
                    "call_volume_24h": round(call_vol, 2),
                    "put_volume_24h": round(put_vol, 2),
                    "pcr_volume": pcr_vol,
                    "avg_iv_pct": avg_iv,
                    "instrument_count": len(items),
                }
        except Exception:
            pass
    return {}

async def get_deribit_top_strikes(currency: str = "BTC", limit: int = 10) -> list:
    """En yüksek açık pozisyonlu strike'ları getir (opsiyon duvarları)"""
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.get(
                "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                params={"currency": currency, "kind": "option"}
            )
            if r.status_code == 200:
                items = r.json().get("result", [])
                # Strike bazında OI topla
                strike_map = {}
                for x in items:
                    name = x.get("instrument_name", "")
                    oi   = x.get("open_interest", 0)
                    parts = name.split("-")
                    if len(parts) >= 4:
                        strike = parts[2]
                        opt_type = parts[3]
                        key = f"{strike}-{opt_type}"
                        strike_map[key] = strike_map.get(key, 0) + oi
                # En yüksek OI'li strike'lar
                sorted_strikes = sorted(strike_map.items(), key=lambda x: x[1], reverse=True)
                result = []
                for sk, oi in sorted_strikes[:limit]:
                    parts = sk.split("-")
                    result.append({
                        "strike": parts[0],
                        "type": "CALL" if parts[1] == "C" else "PUT",
                        "open_interest": round(oi, 2)
                    })
                return result
        except Exception:
            pass
    return []

async def get_deribit_dvol(currency: str = "BTC") -> dict:
    """Deribit DVOL — kripto volatilite endeksi"""
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.get(
                "https://www.deribit.com/api/v2/public/get_volatility_index_data",
                params={"currency": currency, "start_timestamp": 0,
                        "end_timestamp": 99999999999999, "resolution": "3600"}
            )
            if r.status_code == 200:
                data = r.json().get("result", {})
                ticks = data.get("data", [])
                if ticks:
                    latest = ticks[-1]
                    return {
                        "currency": currency,
                        "dvol": round(latest[4], 2) if len(latest) > 4 else 0,
                        "timestamp": latest[0]
                    }
        except Exception:
            pass
    return {}

# ─── Coin tespiti ─────────────────────────────────────────────────────────
COIN_MAP = {
    "bitcoin": "bitcoin",    "btc": "bitcoin",
    "ethereum": "ethereum",  "eth": "ethereum",
    "bnb": "binancecoin",    "binance": "binancecoin",
    "solana": "solana",      "sol": "solana",
    "xrp": "ripple",         "ripple": "ripple",
    "cardano": "cardano",    "ada": "cardano",
    "avalanche": "avalanche-2", "avax": "avalanche-2",
    "polkadot": "polkadot",  "dot": "polkadot",
    "polygon": "matic-network", "matic": "matic-network",
    "dogecoin": "dogecoin",  "doge": "dogecoin",
    "chainlink": "chainlink","link": "chainlink",
    "litecoin": "litecoin",  "ltc": "litecoin",
    "uniswap": "uniswap",    "uni": "uniswap",
    "stellar": "stellar",    "xlm": "stellar",
    "tron": "tron",          "trx": "tron",
    "sui": "sui",            "aptos": "aptos",
    "near": "near",          "atom": "cosmos",
    "cosmos": "cosmos",      "pepe": "pepe",
    "shib": "shiba-inu",     "shiba": "shiba-inu",
}

def detect_coins(text: str) -> list:
    t = text.lower()
    found = []
    for kw, cid in COIN_MAP.items():
        if kw in t and cid not in found:
            found.append(cid)
    return found[:4]

def detect_deribit_currency(text: str) -> str:
    t = text.lower()
    if "eth" in t or "ethereum" in t:
        return "ETH"
    return "BTC"

# ─── System Prompt ───────────────────────────────────────────────────────────
async def canli_sistem_baglami() -> str:
    """Ana sohbet için Live Agent verilerini topla (görev 2: sayfalar iletişimde)."""
    try:
        return await _lider_baglam_topla()
    except Exception:
        return ""

SYSTEM_PROMPT = """Sen uzman bir kripto para, trading bot geliştirici ve opsiyon piyasası yapay zeka asistanısın. Her zaman Türkçe yanıt veriyorsun.
Ayrıca OAR Premium sisteminin parçasısın: /live sayfasındaki Lider Agent, bot sinyalleri, saatlik raporlar ve tarihsel backtest verilerine erişimin var. Bu veriler her mesajda sana CANLI SİSTEM VERİLERİ olarak iletilir — soruları cevaplarken bunları kullan.

KİMLİĞİN:
- Kripto spot ve türev piyasalarında derin bilgiye sahipsin
- Python, JavaScript/Node.js ve Pine Script trading bot geliştirmede uzmansın
- Teknik analiz, temel analiz ve on-chain analizde uzmansın
- Deribit opsiyon verilerini (IV, PCR, OI, DVOL) yorumlayabiliyorsun
- Grafik ve görselleri okuyup yorumlayabiliyorsun
- Sana verilen canlı veriler Binance, CoinGecko ve Deribit'ten geliyor

KOD ANALİZİ:
Kod dosyası yüklendiğinde şunları yap:
1. 📋 **Genel Bakış**: Kodun amacını ve yapısını özetle
2. 🐛 **Hatalar**: Syntax hatası, mantık hatası, potansiyel bug'ları listele
3. ⚡ **Optimizasyon**: Performans iyileştirme önerileri
4. 🔒 **Güvenlik**: API key açıkta mı? Risk yönetimi var mı?
5. 💡 **Geliştirme**: Eklenmesi önerilen özellikler
6. ✅ **Düzeltilmiş Kod**: Hataları giderilmiş versiyonu ver (kısa kodlarda)

Pine Script için özellikle:
- Strateji giriş/çıkış mantığını analiz et
- Backtest sonuçlarını etkileyen parametreleri belirt
- Over-fitting riskini değerlendir

OPSİYON VERİSİ YORUMLAMA:
- PCR > 1 = ayı baskısı, < 1 = boğa baskısı
- Yüksek IV = büyük hareket beklentisi
- DVOL = Deribit'in 30g volatilite endeksi

KURALLAR:
1. Canlı veri sağlandığında mutlaka kullan
2. Kod analiz ederken somut satır numarası ve düzeltme ver
3. Risk yönetimini her analizde vurgula
4. Yanıtları net başlıklarla düzenle, emoji kullan
5. Her analizin sonuna: "⚠️ Bu analiz yatırım tavsiyesi değildir."

KESİNLİK MODU (ÇOK ÖNEMLİ):
- "Sanırım", "muhtemelen", "olabilir", "öyle düşünüyorum", "galiba", "ihtimal" gibi belirsiz ifadeler KULLANMA
- Her iddianı matematiksel veriye, formüle veya kaynağa dayandır
- Bir hesap yapıyorsan adım adım göster, sonucu doğrula
- Kullanıcının ne istediğinden emin değilsen TAHMİN ETME — net soru sor: "Şunu mu kastediyorsun: X yoksa Y mi?"
- Bilmediğin bir şeyi biliyormuş gibi yapma. "Bu konuda elimde veri yok" de
- Verilerle çelişen bir şey söyleme; çelişki varsa açıkça belirt
- Yüzde, oran, fiyat verirken her zaman kaynağını söyle (Binance, CoinGecko, Deribit, hesaplama)
- Geçmiş konuşmalardan gelen HAFIZA bölümündeki bilgileri kesin bilgi olarak kullan — kullanıcı bunları sana öğretti

HAFIZA:
- Sana "HAFIZA" başlığı altında geçmiş konuşmalar ve kalıcı bilgiler verilir
- Bunlar kullanıcıyla yaptığın GERÇEK geçmiş konuşmalardır, kullan
- Kullanıcının daha önce söylediği tercihleri, stratejileri, kuralları hatırla ve uygula

CEVAP UZUNLUĞU (KRİTİK):
- KISA ve ÖZ yanıt ver — uzun paragraflar YASAK
- Her şeyi madde madde (-) yaz
- Basit sorulara 2-4 madde, analizlere maksimum 8-10 madde
- Gereksiz giriş cümlesi yazma ("Tabii ki!", "Elbette!" yok) — direkt cevaba gir
- Tekrar etme, özetleme yapma
- Sadece sorulana cevap ver, fazlasını ekleme

FORMAT: ## başlık, **kalın**, - liste, fiyatlar $"""

# ─── Endpoints ───────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    # Ana sayfa = Agent arayüzü (live.html). Eski sohbet /chat'te kaldı.
    p = Path("static/live.html")
    return p.read_text(encoding="utf-8") if p.exists() else HTMLResponse("<h1>live.html eksik</h1>")

@app.get("/chat", response_class=HTMLResponse)
async def chat_page():
    # Eski sohbet arayüzü (silinmedi, yedek olarak burada)
    p = Path("static/index.html")
    return p.read_text(encoding="utf-8") if p.exists() else HTMLResponse("<h1>index.html eksik</h1>")

@app.get("/api/market")
async def market():
    coins = await get_coingecko_markets(15)
    fg    = await get_fear_greed()
    gm    = await get_global_stats()
    return {"coins": coins, "fear_greed": fg, "global": gm}

@app.get("/api/deribit")
async def deribit_data(currency: str = "BTC"):
    """Deribit opsiyon özet verisi"""
    summary = await get_deribit_summary(currency)
    strikes = await get_deribit_top_strikes(currency, limit=10)
    dvol    = await get_deribit_dvol(currency)
    return {"summary": summary, "top_strikes": strikes, "dvol": dvol}

@app.get("/api/health")
async def health():
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key and Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return {
        "status": "ok",
        "model": GEMINI_MODEL,
        "api_key_set": bool(key),
        "preview": f"{key[:10]}..." if len(key) > 10 else "YOK"
    }

@app.get("/api/signals")
async def get_signals(refresh: bool = False):
    """Güncel sinyaller — opsiyonel olarak tara"""
    if refresh:
        signals = await scan_market("1h")
    else:
        signals = load_json(SIGNALS_FILE, [])
        if not signals:
            signals = await scan_market("1h")
    accuracy = get_accuracy_stats()
    return {"signals": signals, "accuracy": accuracy,
            "count": len(signals), "timestamp": __import__("datetime").datetime.utcnow().isoformat()}

@app.get("/api/backtest")
async def backtest(symbol: str = "BTCUSDT", timeframe: str = "4h"):
    result = await quick_backtest(symbol, timeframe)
    return result

@app.get("/api/memory")
async def get_memory():
    return get_accuracy_stats()

@app.post("/api/alerts")
async def create_alert(
    symbol: str = Form(...),
    condition: str = Form(...),
    target_price: float = Form(default=None),
    target_signal: str = Form(default=None),
):
    return add_alert(symbol, condition, target_price, target_signal)

@app.get("/api/alerts")
async def get_alerts():
    return load_json(ALERTS_FILE, [])

@app.get("/api/memory/stats")
async def memory_stats():
    return get_memory_stats()

@app.get("/api/memory/search")
async def memory_search(q: str):
    return {"results": search_memories(q, top_k=5)}

@app.post("/api/memory/add")
async def memory_add(content: str = Form(...), category: str = Form(default="genel")):
    return add_memory(content, category)

@app.delete("/api/memory/{memory_id}")
async def memory_delete(memory_id: int):
    return delete_memory(memory_id)

# ─── LIVE AGENT ENDPOINTS ────────────────────────────────────────────────────
# ── Lider Agent başlatma ──────────────────────────────────────────────────────
_leader_task = None

@app.on_event("startup")
async def startup_event():
    global _leader_task
    api_key = os.environ.get("GEMINI_API_KEY", "")
    from leader_agent import (
        sinyal_toplayici_loop, sinyal_degerlendirici_loop,
        saatlik_lider_raporu_loop, saatlik_backtest_loop, saatlik_research_loop
    )
    _leader_task = asyncio.create_task(sabah_raporu_loop(api_key))
    asyncio.create_task(sinyal_toplayici_loop())
    asyncio.create_task(sinyal_degerlendirici_loop())
    asyncio.create_task(saatlik_lider_raporu_loop(api_key))
    asyncio.create_task(saatlik_backtest_loop())
    asyncio.create_task(saatlik_research_loop())
    from feature_engine import zenginlestirici_loop, pattern_sinyal_loop
    asyncio.create_task(zenginlestirici_loop())
    asyncio.create_task(pattern_sinyal_loop())
    from market_context import baglam_loop
    asyncio.create_task(baglam_loop())
    from basari_skoru import skor_loop
    asyncio.create_task(skor_loop())
    print("[LiderAgent] ✅ Tüm agent görevleri başlatıldı (toplayıcı + değerlendirici + 3 saatlik rapor)")

# ── Lider Agent Endpoint'leri ─────────────────────────────────────────────────
@app.get("/api/leader/report")
async def leader_get_report():
    """Son lider agent raporunu getir."""
    return son_rapor()

@app.post("/api/leader/report")
async def leader_run_report():
    """Hemen rapor üret (beklemeden)."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    rapor = await rapor_uret(api_key)
    return rapor

@app.get("/api/leader/backtest")
async def leader_backtest():
    """Tüm botların backtest analizini döndür."""
    return backtest_sinyal_analizi()

@app.get("/api/leader/patterns")
async def leader_patterns():
    """Pattern ve öneri analizini döndür."""
    return research_analizi()

@app.get("/api/leader/summary")
async def leader_summary():
    """Sohbet arayüzü için kısa özet."""
    rapor = son_rapor()
    ozet = rapor.get("ozet", {})
    ai = rapor.get("ai_yorum", "")
    return {
        "toplam_sinyal": ozet.get("toplam_sinyal", 0),
        "genel_win_rate": ozet.get("genel_win_rate", 0),
        "en_iyi_bot": ozet.get("en_iyi_bot", "—"),
        "oneriler": ozet.get("oneriler", []),
        "ai_yorum": ai[:400] if ai else "",
        "tarih": rapor.get("tarih", ""),
    }

# ── Sunucu Tarafı Config (kalıcı — /var/data'da saklanır) ─────────────────────
CONFIG_FILE = DATA_DIR / "config.json"

def _config_oku() -> dict:
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _config_yaz(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

@app.get("/api/config")
async def config_get():
    cfg = _config_oku()
    return {
        "vercel_url": cfg.get("vercel_url", os.environ.get("VERCEL_URL", "https://project-vtcqr.vercel.app")),
        "bot_url": cfg.get("bot_url", os.environ.get("BOT_URL", "https://oar-sinyal-bot.onrender.com")),
    }

@app.post("/api/config")
async def config_set(req: Request):
    data = await req.json()
    cfg = _config_oku()
    if "vercel_url" in data:
        cfg["vercel_url"] = data["vercel_url"].rstrip("/")
    if "bot_url" in data:
        cfg["bot_url"] = data["bot_url"].rstrip("/")
    _config_yaz(cfg)
    return {"ok": True, "config": cfg}


# ── Vercel Proxy (CORS çözümü — backend üzerinden çek) ────────────────────────
async def _vercel_get(path: str):
    cfg = _config_oku()
    base = cfg.get("vercel_url", os.environ.get("VERCEL_URL", "https://project-vtcqr.vercel.app"))
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            r = await cl.get(f"{base}{path}")
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        return {"error": str(e)[:100]}
    return {"error": f"Vercel {r.status_code}"}

@app.get("/api/vercel/alarm-levels")
async def vercel_alarm_levels(currency: str = "BTC"):
    # ARTIK YERLİ — Deribit'ten doğrudan, Vercel'e gerek yok
    from options_engine import alarm_levels
    return await alarm_levels(currency)

@app.get("/api/vercel/opsiyon-cvd")
async def vercel_opsiyon_cvd(currency: str = "BTC"):
    from options_engine import opsiyon_cvd
    return await opsiyon_cvd(currency)

@app.get("/api/options/levels")
async def options_levels(currency: str = "BTC"):
    """Yerli opsiyon motoru — CW/PW/ZG/MaxPain vade dilimli."""
    from options_engine import alarm_levels
    return await alarm_levels(currency)

@app.get("/api/options/cvd")
async def options_cvd_ep(currency: str = "BTC"):
    from options_engine import opsiyon_cvd
    return await opsiyon_cvd(currency)

@app.get("/api/options/topografya")
async def options_topografya(currency: str = "BTC", vade: str = "all"):
    from options_engine import strike_topografya
    return await strike_topografya(currency, vade)

@app.get("/api/options/greekler")
async def options_greekler(currency: str = "BTC"):
    from options_engine import toplu_greekler
    return await toplu_greekler(currency)

@app.get("/api/options/skew")
async def options_skew(currency: str = "BTC"):
    from options_engine import iv_skew
    return await iv_skew(currency)

@app.get("/api/options/cvd-uclu")
async def options_cvd_uclu(currency: str = "BTC"):
    from options_engine import cvd_uclu
    return await cvd_uclu(currency)

@app.get("/api/options/islem-dagilimi")
async def options_islem_dagilimi(currency: str = "BTC"):
    from options_engine import islem_dagilimi
    return await islem_dagilimi(currency)

@app.get("/api/options/gex")
async def options_gex(currency: str = "BTC"):
    from options_engine import gex_ozet
    return await gex_ozet(currency)

@app.get("/api/vercel/orderflow")
async def vercel_orderflow(currency: str = "BTC"):
    return await _vercel_get(f"/api/orderflow?currency={currency}")

@app.get("/api/vercel/macro")
async def vercel_macro():
    return await _vercel_get("/api/macro")


# ── Sinyal Listesi (UI için — sunucu diskinden) ───────────────────────────────
@app.get("/api/leader/signals")
async def leader_signals(limit: int = 200):
    """OAR diskindeki toplanmış+değerlendirilmiş sinyalleri döndür."""
    sig_file = DATA_DIR / "oar_signals_log.json"
    try:
        if sig_file.exists():
            data = json.loads(sig_file.read_text(encoding="utf-8"))
            sinyaller = data.get("signals", []) if isinstance(data, dict) else data
            return {"signals": sinyaller[-limit:], "total": len(sinyaller)}
    except Exception as e:
        return {"signals": [], "total": 0, "error": str(e)[:80]}
    return {"signals": [], "total": 0}


@app.get("/api/leader/rapor-gecmisi")
async def leader_rapor_gecmisi(tip: str = None, limit: int = 24):
    """Saatlik rapor geçmişi — silinmez, tip: lider|backtest|research."""
    from leader_agent import rapor_gecmisi_al
    return {"raporlar": rapor_gecmisi_al(tip, limit)}

@app.post("/api/leader/tarihsel-backtest")
async def tarihsel_backtest_calistir(req: Request):
    """Strateji kurallarını geçmiş veriye uygula.
    Body: {strateji: ASIA_EKSTREM|CVD_OI_KOMBO|MA_TEMAS, sembol, gun}"""
    from historical_backtest import calistir
    data = await req.json()
    return await calistir(
        data.get("strateji", "ASIA_EKSTREM"),
        data.get("sembol", "BTCUSDT"),
        int(data.get("gun", 90))
    )

@app.get("/api/leader/tarihsel-backtest")
async def tarihsel_backtest_gecmis():
    from historical_backtest import gecmis_testler
    return {"testler": gecmis_testler()}

async def _lider_baglam_topla() -> str:
    """Lider Agent'ın ZEKASI: her soruda tüm canlı + tarihsel bağlamı topla."""
    parcalar = []

    # 1. Canlı fiyatlar
    try:
        async with httpx.AsyncClient(timeout=8) as cl:
            r = await cl.get("https://api.binance.com/api/v3/ticker/24hr",
                             params={"symbols": '["BTCUSDT","ETHUSDT","SOLUSDT"]'})
            for t in r.json():
                parcalar.append(f"{t['symbol']}: ${float(t['lastPrice']):,.0f} ({float(t['priceChangePercent']):+.1f}% 24h)")
    except Exception:
        pass

    # 2. Opsiyon seviyeleri (Vercel)
    try:
        levels = await _vercel_get("/api/alarm-levels")
        if isinstance(levels, dict) and not levels.get("error"):
            g = levels.get("genel", levels)
            cw, pw, zg = g.get("call_wall") or g.get("CW"), g.get("put_wall") or g.get("PW"), g.get("zero_gamma") or g.get("ZG")
            if cw: parcalar.append(f"Opsiyon: CW=${cw:,.0f} PW=${pw:,.0f} ZG=${zg:,.0f}" if pw and zg else f"CW=${cw:,.0f}")
    except Exception:
        pass

    # 3. Son sinyaller + bilgi botu bildirimleri
    try:
        sig_file = DATA_DIR / "oar_signals_log.json"
        if sig_file.exists():
            sigs = json.loads(sig_file.read_text()).get("signals", [])[-15:]
            if sigs:
                ozet = "; ".join(f"{s.get('bot','?')}: {s.get('symbol','?')} {s.get('direction','?')} [{s.get('outcome') or 'bekliyor'}]" for s in sigs[-8:])
                parcalar.append(f"Son sinyaller: {ozet}")
    except Exception:
        pass

    # 4. Kullanıcının öğrettiği bilgiler (knowledge bankası)
    try:
        kb_file = DATA_DIR / "knowledge.json"
        if kb_file.exists():
            kb = json.loads(kb_file.read_text())
            notlar = kb.get("notes", [])[-5:]
            if notlar:
                parcalar.append("Kullanıcının öğrettikleri: " + " | ".join(n.get("text", "")[:100] for n in notlar))
            docs = kb.get("documents", [])
            if docs:
                parcalar.append("Yüklü dokümanlar: " + ", ".join(d.get("name", "?") for d in docs[-5:]))
    except Exception:
        pass

    # 5. Son saatlik raporlar
    try:
        from leader_agent import rapor_gecmisi_al
        for r in rapor_gecmisi_al(limit=3):
            parcalar.append(f"[{r['tip']} raporu] {r['icerik'].get('metin','')[:150]}")
    except Exception:
        pass

    # 6. Tarihsel backtest sonuçları
    try:
        from historical_backtest import gecmis_testler
        for t in gecmis_testler(limit=3):
            parcalar.append(f"Tarihsel test: {t['strateji']} {t['sembol']} {t['gun']}g → WR %{t['win_rate']} ({t['toplam_sinyal']} sinyal, ort {t['ort_pnl_pct']:+.1f}%)")
    except Exception:
        pass

    # 7. Korku endeksi
    try:
        async with httpx.AsyncClient(timeout=6) as cl:
            r = await cl.get("https://api.alternative.me/fng/")
            d = r.json()["data"][0]
            parcalar.append(f"Korku Endeksi: {d['value']} ({d['value_classification']})")
    except Exception:
        pass

    return "\n".join(parcalar)


@app.get("/api/leader/kazanan-profil")
async def kazanan_profil():
    """Feature Engine'in öğrendiği WIN/LOSS ayırt edici profil."""
    from feature_engine import kazanan_profil_al, profil_ogren
    return profil_ogren()

@app.get("/api/teori")
async def teori_liste():
    """Theory Lab: tüm teoriler ve durumları."""
    from theory_lab import teori_listesi
    return {"teoriler": teori_listesi()}

@app.post("/api/teori/test")
async def teori_test(req: Request):
    """Teoriyi geçmiş veride test et. Body: {teori_id, sembol, gun}"""
    from theory_lab import teori_backtest
    d = await req.json()
    return await teori_backtest(d.get("teori_id","OAR-001"), d.get("sembol","BTCUSDT"), int(d.get("gun",365)))

@app.get("/api/devops")
async def devops_durum():
    """Render deploylar + env + GitHub commit'leri (token gerekli)."""
    from devops_monitor import devops_ozet
    return await devops_ozet()

@app.post("/api/theory/advanced")
async def theory_advanced(req: Request):
    """Gelişmiş Theory Lab backtest: coin/gün/fib/asia-saat seçimli.
    Body: {sembol, gun, fib, asia_baslangic, asia_bitis}"""
    from theory_engine import gelismis_backtest
    d = await req.json()
    return await gelismis_backtest(
        d.get("sembol","BTCUSDT"), int(d.get("gun",180)),
        float(d.get("fib",0.618)),
        int(d.get("asia_baslangic",0)), int(d.get("asia_bitis",4)))

@app.get("/api/theory/advanced")
async def theory_advanced_history():
    from theory_engine import gecmis_sonuclar
    return {"testler": gecmis_sonuclar()}

@app.post("/api/knowledge/import")
async def knowledge_import(req: Request):
    """Toplu kitap yükleme — SQLite DB (RAM dostu, 502 yok)."""
    from kitap_db import import_chunks
    data = await req.json()
    docs = data.get("documents", [])
    if not docs:
        return {"hata": "documents boş"}
    res = import_chunks(docs)
    return {"status": "ok", "eklenen_chunk": res["eklenen"],
            "toplam_kitap": res["toplam_kitap"], "toplam_chunk": res["toplam_chunk"]}

@app.get("/api/knowledge/kitaplar")
async def knowledge_kitaplar():
    """Yüklü kitaplar — SQLite'tan."""
    from kitap_db import istatistik
    return istatistik()

@app.post("/api/knowledge/kitap-temizle")
async def knowledge_kitap_temizle():
    """Tüm kitapları sil (yeniden yükleme için)."""
    from kitap_db import temizle_hepsi
    return temizle_hepsi()

@app.get("/api/knowledge/kitap-ara")
async def knowledge_kitap_ara(q: str, limit: int = 5):
    """Kitaplarda tam metin arama (FTS5)."""
    from kitap_db import ara
    return {"sonuclar": ara(q, limit)}

@app.get("/api/oar-fib")
async def oar_fib(symbol: str = "BTCUSDT"):
    """Bugünkü Asia Range (TR 03-07 = UTC 00-04) fib seviyeleri."""
    import httpx
    from datetime import datetime, timezone, timedelta
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.get("https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": symbol, "interval": "15m", "limit": 100})
            k = r.json()
        if not isinstance(k, list): return {"error": "veri yok"}
        now = datetime.now(timezone.utc)
        bugun = now.date()
        asia = []
        for x in k:
            t = datetime.fromtimestamp(x[0]/1000, tz=timezone.utc)
            # UTC 00:00-04:00 = TR 03:00-07:00
            if t.date() == bugun and 0 <= t.hour < 4:
                asia.append((float(x[2]), float(x[3])))
        if len(asia) < 4:
            # dün veri olabilir, son 16 muma bak
            asia = [(float(x[2]), float(x[3])) for x in k[-32:-16]]
        if not asia: return {"error": "asia range yok"}
        hi = max(a[0] for a in asia); lo = min(a[1] for a in asia)
        rng = hi - lo
        sev = {
            "0.0 (low)": round(lo, 1),
            "0.377": round(lo + rng*0.377, 1),
            "0.618": round(lo + rng*0.618, 1),
            "1.0 (high)": round(hi, 1),
            "-0.272": round(lo - rng*0.272, 1),
            "1.272": round(hi + rng*0.272, 1),
        }
        return {"symbol": symbol, "asia_high": round(hi,1), "asia_low": round(lo,1),
                "range_pct": round(rng/lo*100,2), "seviyeler": sev}
    except Exception as e:
        return {"error": str(e)[:80]}

@app.get("/api/indicators")
async def indicators_get(symbol: str = "BTCUSDT", interval: str = "5m"):
    """Indicator Engine — ~30 indikatör + 5m skor + yorum. Sistemin temeli."""
    from indicator_engine import analiz
    return await analiz(symbol, interval)

@app.get("/api/opsiyon-yorum")
async def opsiyon_yorum(currency: str = "BTC"):
    """Opsiyon genel durum analizi — tüm opsiyon verileri + opsiyon kitapları."""
    import httpx
    from options_engine import alarm_levels, opsiyon_cvd
    lv = await alarm_levels(currency)
    if lv.get("error"):
        return {"yorum": "Opsiyon verisi alınamadı.", "veri": {}}
    cvd = await opsiyon_cvd(currency)
    genel = lv.get("genel", {})
    spot = lv.get("spot")
    veri = {
        "spot": spot, "call_wall": genel.get("call_wall"), "put_wall": genel.get("put_wall"),
        "zero_gamma": genel.get("zero_gamma"), "max_pain": genel.get("max_pain"),
        "opsiyon_cvd": cvd.get("guncel"), "cvd_yon": cvd.get("yon"),
    }
    # Opsiyon kitaplarından bilgi
    kitap_notu = ""
    try:
        from kitap_db import ara
        ks = ara("gamma exposure call wall put wall zero gamma dealer hedging options", limit=2)
        if ks:
            kitap_notu = " | ".join(f"{s['title']}: {s['content'][:180]}" for s in ks)
    except Exception: pass

    api_key = os.environ.get("GEMINI_API_KEY", "")
    yorum = ""
    if api_key:
        prompt = f"""Sen opsiyon piyasası uzmanısın. {currency} opsiyon verilerini bilimsel/matematiksel yorumla
(4-5 cümle, vade yorumlaması dahil, Türkçe):

Spot: {spot}
Call Wall (direnç): {genel.get('call_wall')}
Put Wall (destek): {genel.get('put_wall')}
Zero Gamma (flip): {genel.get('zero_gamma')}
Max Pain: {genel.get('max_pain')}
Opsiyon CVD: {cvd.get('guncel')} (yön: {cvd.get('yon')})
Kısa vade (0-7g): {lv.get('kisa', {})}
Orta vade (8-45g): {lv.get('orta', {})}

Opsiyon kitaplarından: {kitap_notu[:400]}

Dealer gamma pozisyonu, spot-ZG ilişkisi, CW/PW bandı, CVD akışını değerlendir.
Kitap bilgisi varsa referans ver."""
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            async with httpx.AsyncClient(timeout=30) as cl:
                rr = await cl.post(url, json={"contents":[{"role":"user","parts":[{"text":prompt}]}],
                    "generationConfig":{"temperature":0.3,"maxOutputTokens":2048,"thinkingConfig":{"thinkingBudget":256}}})
                if rr.status_code == 200:
                    yorum = rr.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception: pass
    return {"veri": veri, "yorum": yorum}

@app.get("/api/piyasa-durumu")
async def piyasa_durumu():
    """Komuta Merkezi 'Piyasa Durumu' — opsiyon + makro + indikatör + kitap harmanı, AI yorumu."""
    import httpx
    from market_context import son_baglam
    ctx = son_baglam()
    veri = {}
    # İndikatör skoru
    try:
        from indicator_engine import analiz
        ind = await analiz("BTCUSDT", "5m")
        veri["indikator_skor"] = ind.get("skor", {}).get("skor")
        veri["indikator_yon"] = ind.get("skor", {}).get("yon")
        veri["fiyat"] = ind.get("fiyat")
    except Exception: pass
    # Opsiyon
    try:
        from options_engine import gex_ozet
        gex = await gex_ozet("BTC")
        if not gex.get("error"):
            veri["gamma_rejim"] = gex.get("gamma_rejim")
            veri["call_wall"] = gex.get("call_wall")
            veri["put_wall"] = gex.get("put_wall")
            veri["zero_gamma"] = gex.get("zero_gamma")
    except Exception: pass
    # Market context (regime + move source)
    if ctx:
        veri["rejim"] = ctx.get("regime", {}).get("rejim")
        veri["move_source"] = ctx.get("move_source", {}).get("kaynak")
        veri["oar_score"] = ctx.get("oar_score", {}).get("skor")
    # Korku endeksi
    try:
        async with httpx.AsyncClient(timeout=6) as cl:
            r = await cl.get("https://api.alternative.me/fng/")
            d = r.json()["data"][0]
            veri["korku"] = f"{d['value']} ({d['value_classification']})"
    except Exception: pass
    # Kitaplardan ilgili bilgi (mevcut duruma göre)
    kitap_notu = ""
    try:
        from kitap_db import ara
        sorgu = f"{veri.get('rejim','')} {veri.get('gamma_rejim','')} market regime gamma"
        ks = ara(sorgu, limit=2)
        if ks:
            kitap_notu = " | ".join(f"{s['title']}: {s['content'][:150]}" for s in ks)
    except Exception: pass

    # AI yorumu
    api_key = os.environ.get("GEMINI_API_KEY", "")
    yorum = ""
    if api_key:
        prompt = f"""Sen OAR Premium piyasa analistisin. Aşağıdaki canlı verilerle BTC için
genel piyasa durumu yorumu yaz (3-4 cümle, bilimsel, matematiksel, Türkçe):

İndikatör skoru: {veri.get('indikator_skor')} ({veri.get('indikator_yon')})
Piyasa rejimi: {veri.get('rejim')}
Move source: {veri.get('move_source')}
OAR Score: {veri.get('oar_score')}/100
Gamma rejim: {veri.get('gamma_rejim')}
CW/PW/ZG: {veri.get('call_wall')}/{veri.get('put_wall')}/{veri.get('zero_gamma')}
Korku endeksi: {veri.get('korku')}
Fiyat: {veri.get('fiyat')}
İlgili kitap bilgisi: {kitap_notu[:400]}

Opsiyon konumunu, indikatör skorunu, rejimi ve korku endeksini birlikte değerlendir.
Kitap bilgisi varsa referans ver. Pozisyon önerisi değil, durum tespiti yap."""
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            async with httpx.AsyncClient(timeout=30) as cl:
                rr = await cl.post(url, json={"contents":[{"role":"user","parts":[{"text":prompt}]}],
                    "generationConfig":{"temperature":0.3,"maxOutputTokens":2048,"thinkingConfig":{"thinkingBudget":256}}})
                if rr.status_code == 200:
                    yorum = rr.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception: pass
    return {"veri": veri, "yorum": yorum, "kitap_notu": kitap_notu[:300]}

@app.get("/api/makro")
async def makro_get(refresh: bool = False):
    """Makro ekonomi — 9 gösterge + BTC etki yorumu (BLS/FRED/Treasury, ücretsiz)."""
    from macro_engine import makro_veri
    data = await makro_veri(refresh)
    # Kitap destekli AI özet (opsiyonel, cache'li veride bir kez)
    return data

@app.get("/api/makro/ozet")
async def makro_ozet():
    """Makro AI özeti — kitaplardan destekli, Lider notu dahil."""
    import httpx
    from macro_engine import makro_veri
    data = await makro_veri()
    g = data.get("gostergeler", {})
    yorum = data.get("btcYorum", {})
    # Makro/ekonomi kitaplarından bilgi
    kitap_notu = ""
    try:
        from kitap_db import ara
        ks = ara("federal reserve interest rate inflation macro economy bitcoin liquidity", limit=2)
        if ks:
            kitap_notu = " | ".join(f"{s['title']}: {s['content'][:150]}" for s in ks)
    except Exception: pass
    api_key = os.environ.get("GEMINI_API_KEY", "")
    ozet = yorum.get("sentez", "")
    if api_key:
        ozet_veri = {k: (v.get("guncel") if v else None) for k, v in g.items()}
        prompt = f"""Sen makro ekonomi analistisin. Aşağıdaki ABD makro verilerini BTC açısından
2-3 cümlede özetle (Türkçe, bilimsel). Yön belirleyici tetikleyicileri belirt:

Veriler: {json.dumps(ozet_veri, ensure_ascii=False)}
Eğilim: {yorum.get('egilim')}
Kitaplardan: {kitap_notu[:300]}

Range-bound mu, katalist mi gerekiyor? Bir sonraki önemli veri ne?"""
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            async with httpx.AsyncClient(timeout=30) as cl:
                rr = await cl.post(url, json={"contents":[{"role":"user","parts":[{"text":prompt}]}],
                    "generationConfig":{"temperature":0.3,"maxOutputTokens":2048,"thinkingConfig":{"thinkingBudget":256}}})
                if rr.status_code == 200:
                    ozet = rr.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception: pass
    return {"ozet": ozet, "egilim": yorum.get("egilim"), "guncellendi": data.get("guncellendi")}

@app.get("/api/ticker")
async def ticker_get():
    """Sadece: BTC, ETH, SP500, Nasdaq, Altın, Gümüş, VIX (belge kuralı)."""
    import httpx
    out = []
    # Kripto — Binance (tek tek, kesin sadece BTC/ETH)
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            for sym_b, ad in [("BTCUSDT","BTC"), ("ETHUSDT","ETH")]:
                try:
                    r = await cl.get("https://api.binance.com/api/v3/ticker/24hr",
                        params={"symbol": sym_b})
                    t = r.json()
                    out.append({"sym": ad, "price": float(t["lastPrice"]),
                                "chg": round(float(t["priceChangePercent"]),2)})
                except Exception: continue
    except Exception: pass
    # Binance boş geldiyse CoinGecko yedeği
    if not any(x["sym"] in ("BTC","ETH") for x in out):
        try:
            async with httpx.AsyncClient(timeout=10) as cl:
                r = await cl.get("https://api.coingecko.com/api/v3/simple/price",
                    params={"ids":"bitcoin,ethereum","vs_currencies":"usd","include_24hr_change":"true"},
                    headers=_cg_headers())
                d = r.json()
                if "bitcoin" in d:
                    out.append({"sym":"BTC","price":d["bitcoin"]["usd"],"chg":round(d["bitcoin"].get("usd_24h_change",0),2)})
                if "ethereum" in d:
                    out.append({"sym":"ETH","price":d["ethereum"]["usd"],"chg":round(d["ethereum"].get("usd_24h_change",0),2)})
        except Exception: pass
    # Geleneksel — Yahoo Finance (ücretsiz, keysiz)
    yahoo = {"^GSPC":"SP500","^IXIC":"Nasdaq","GC=F":"Altın","SI=F":"Gümüş","^VIX":"VIX"}
    try:
        async with httpx.AsyncClient(timeout=12) as cl:
            for sym_y, ad in yahoo.items():
                try:
                    r = await cl.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym_y}",
                        params={"interval":"1d","range":"2d"},
                        headers={"User-Agent":"Mozilla/5.0"})
                    m = r.json()["chart"]["result"][0]["meta"]
                    fiyat = m.get("regularMarketPrice")
                    onc = m.get("chartPreviousClose") or m.get("previousClose")
                    chg = round((fiyat-onc)/onc*100,2) if (fiyat and onc) else 0
                    out.append({"sym": ad, "price": round(fiyat,2), "chg": chg})
                except Exception: continue
    except Exception: pass
    return {"items": out}

@app.get("/api/basari-skoru")
async def basari_skoru_get():
    """Bot başarı skoru — yüzde + dolar tablosu (win rate yerine)."""
    from basari_skoru import skor_tablosu
    return skor_tablosu()

@app.post("/api/basari-skoru/guncelle")
async def basari_skoru_guncelle():
    from basari_skoru import skorlari_guncelle
    return await skorlari_guncelle()

@app.get("/api/market-context")
async def market_context_get(sembol: str = "BTCUSDT", refresh: bool = False):
    """Market Regime + OAR Score + Move Source."""
    from market_context import son_baglam, baglam_guncelle
    if refresh:
        return await baglam_guncelle(sembol)
    ctx = son_baglam()
    return ctx if ctx else await baglam_guncelle(sembol)

@app.post("/api/leader/chat")
async def leader_chat(req: Request):
    """Lider Agent ile sohbet — canlı veri + tüm sistem bağlamıyla cevap verir."""
    data = await req.json()
    soru = data.get("soru", "").strip()
    if not soru:
        return {"cevap": "Soru boş."}
    api_key = os.environ.get("GEMINI_API_KEY", "")

    from leader_agent import backtest_sinyal_analizi, research_analizi, BOT_KATALOG
    backtest = backtest_sinyal_analizi()
    research = research_analizi()
    canli_baglam = await _lider_baglam_topla()

    # Kitap bilgisi ara (240+ trading kitabı SQLite'tan)
    kitap_baglam = ""
    kitap_var = False
    try:
        from kitap_db import ara as kitap_ara, istatistik
        stat = istatistik()
        sonuclar = kitap_ara(soru, limit=4)
        if sonuclar:
            kitap_var = True
            kitap_baglam = "\n\n══ İLGİLİ KİTAP BİLGİSİ (kütüphanedeki trading kitaplarından) ══\n"
            for s in sonuclar:
                kitap_baglam += f"\n[{s['title']}]: {s['content'][:400]}\n"
        elif stat.get("kitap_sayisi", 0) > 0:
            kitap_baglam = f"\n\n(Kütüphanede {stat['kitap_sayisi']} kitap var ama bu soruyla doğrudan eşleşen bölüm bulunamadı.)"
    except Exception:
        pass

    kitap_kural = ("Kütüphanedeki kitaplara ERİŞİMİN VAR — yukarıdaki 'KİTAP BİLGİSİ' bölümü sana o kitaplardan geldi, "
                   "cevabında bu bilgiyi kullan ve hangi kitaptan geldiğini belirt." if kitap_var else
                   "Bu soru için kitaplarda doğrudan eşleşme çıkmadı; genel bilginle cevapla, 'kitaplara erişemiyorum' DEME.")

    prompt = f"""Sen OAR Premium'un LİDER AGENT'ısın. Kripto trading sistemini yönetiyorsun.
Altındaki agentlar: Research Agent (analiz) ve Backtest Agent (test).
Yönettiğin botlar: {", ".join(BOT_KATALOG.keys())}

══ CANLI VERİLER (şu an) ══
{canli_baglam}
{kitap_baglam}
══ BACKTEST DURUMU ══
{json.dumps(backtest, ensure_ascii=False)[:1200]}

══ RESEARCH BULGULARI ══
{json.dumps({"bulgular": research.get("bulgular", []), "oneriler": research.get("oneriler", [])[:3]}, ensure_ascii=False)[:800]}

══ KULLANICI SORUSU ══
{soru}

Kurallar:
- Kesin rakamlarla, matematiksel konuş. Tahmin yapma.
- Canlı verileri kullan (fiyat, opsiyon seviyeleri, korku endeksi).
- Veri yoksa "henüz veri birikmedi" de, uydurma.
- {kitap_kural}
- Türkçe, net, madde gerektiren yerde madde kullan."""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                   "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096, "thinkingConfig": {"thinkingBudget": 256}}}
        async with httpx.AsyncClient(timeout=40) as cl:
            r = await cl.post(url, json=payload)
            if r.status_code == 200:
                return {"cevap": r.json()["candidates"][0]["content"]["parts"][0]["text"]}
            groq_key = os.environ.get("GROQ_API_KEY", "")
            if groq_key:
                gr = await cl.post("https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}"},
                    json={"model": "llama-3.3-70b-versatile",
                          "messages": [{"role": "user", "content": prompt}], "max_tokens": 2048})
                if gr.status_code == 200:
                    return {"cevap": gr.json()["choices"][0]["message"]["content"]}
    except Exception as e:
        return {"cevap": f"AI bağlantı hatası: {str(e)[:80]}"}
    return {"cevap": "AI yanıt veremedi — API key kontrolü gerekli."}

@app.get("/live", response_class=HTMLResponse)
async def live_page():
    p = Path("static/live.html")
    return p.read_text(encoding="utf-8") if p.exists() else HTMLResponse("<h1>live.html eksik</h1>")

@app.get("/api/ohlcv")
async def api_ohlcv(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 200):
    candles = await get_ohlcv(symbol, interval, min(limit, 500))
    return {"symbol": symbol, "candles": candles}

@app.get("/api/walls")
async def api_walls(currency: str = "BTC"):
    """PW (Put Wall), CW (Call Wall), ZG (Zero Gamma yaklaşımı) — Deribit OI'den"""
    strikes_raw = await get_deribit_top_strikes(currency, limit=40)
    if not strikes_raw:
        return {"pw": None, "cw": None, "zg": None, "levels": []}
    calls = [s for s in strikes_raw if s["type"] == "CALL"]
    puts  = [s for s in strikes_raw if s["type"] == "PUT"]
    cw = float(calls[0]["strike"]) if calls else None   # En yüksek call OI
    pw = float(puts[0]["strike"])  if puts  else None   # En yüksek put OI
    # ZG yaklaşımı: OI ağırlıklı orta nokta (gerçek gamma flip için greeks gerekir — bu yaklaşımdır)
    zg = None
    if cw and pw:
        c_oi = calls[0]["open_interest"]; p_oi = puts[0]["open_interest"]
        zg = round((cw * c_oi + pw * p_oi) / (c_oi + p_oi), 0)
    levels = [{"strike": float(s["strike"]), "type": s["type"],
               "oi": s["open_interest"]} for s in strikes_raw[:15]]
    return {"pw": pw, "cw": cw, "zg": zg, "levels": levels,
            "note": "ZG = OI ağırlıklı yaklaşım (greeks tabanlı değil)"}

@app.get("/api/cvd")
async def api_cvd(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 100):
    """CVD (Cumulative Volume Delta) — Binance taker buy verisinden"""
    async with httpx.AsyncClient(timeout=12) as cl:
        try:
            r = await cl.get("https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": min(limit, 500)})
            if r.status_code == 200:
                cvd, out = 0.0, []
                for k in r.json():
                    vol = float(k[5]); taker_buy = float(k[9])
                    delta = taker_buy - (vol - taker_buy)
                    cvd += delta
                    out.append({"ts": k[0], "delta": round(delta, 2), "cvd": round(cvd, 2)})
                return {"symbol": symbol, "data": out, "source": "Binance"}
        except Exception:
            pass
    return {"symbol": symbol, "data": [], "source": None}

@app.get("/api/bots")
async def api_bots_list():
    return {"sources": list_sources(), "stats": get_bot_stats(),
            "recent": get_recent_signals(40)}

@app.post("/api/bots")
async def api_bots_add(name: str = Form(...), url: str = Form(...)):
    return add_source(name, url)

@app.get("/api/bots/diagnose/{source_id}")
async def api_bots_diagnose(source_id: int):
    """Bir bot kaynağının bağlantısını test et — Render/Vercel/GitHub"""
    sources = list_sources()
    src = next((s for s in sources if s.get("id") == source_id), None)
    if not src:
        raise HTTPException(status_code=404, detail="Bot bulunamadı")
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cl:
            headers = {"User-Agent": "OAR-Premium/1.0"}
            r = await cl.get(src["url"], headers=headers)
            status = r.status_code
            try:
                data = r.json()
                sample = str(data)[:200]
                is_json = True
            except Exception:
                sample = r.text[:200]
                is_json = False
            return {
                "url": src["url"],
                "http_status": status,
                "is_json": is_json,
                "sample": sample,
                "hint": (
                    "✅ JSON geliyor — bot ekleyebilirsin" if is_json and status==200
                    else "⚠️ Render uyuyor olabilir, 30sn bekle" if status in (502,503)
                    else "❌ URL hatalı veya erişim yok" if status==404
                    else f"HTTP {status}"
                )
            }
    except Exception as e:
        return {"url": src["url"], "error": str(e),
                "hint": "Render ücretsiz plan kapalıysa uyandırması 30-60sn sürer"}

@app.delete("/api/bots/{source_id}")
async def api_bots_delete(source_id: int):
    return delete_source(source_id)

@app.post("/api/bots/poll")
async def api_bots_poll(hours: int = 24, threshold_pct: float = 1.0):
    current = await poll_all_sources()
    await evaluate_signals(hours=hours, threshold_pct=threshold_pct)
    return {"current_signals": current, "stats": get_bot_stats(),
            "recent": get_recent_signals(40)}

@app.get("/api/conversations/recent")
async def conversations_recent(n: int = 30):
    """Son n yazışmayı döndür — sayfa açılışında sohbeti geri yükler"""
    from knowledge import load_conversations
    conv = load_conversations()
    exchanges = conv.get("exchanges", [])[-n:]
    return {"exchanges": [
        {"user": e["user"], "assistant": e["assistant"], "timestamp": e.get("timestamp","")}
        for e in exchanges
    ], "total": len(conv.get("exchanges", []))}

@app.post("/api/notes")
async def notes_add(title: str = Form(...), content: str = Form(...)):
    return add_note(title, content)

@app.get("/api/notes")
async def notes_list():
    return {"notes": get_notes()}

@app.get("/api/knowledge")
async def knowledge_list(category: str = None):
    result = list_knowledge(category)
    result["memory_stats"] = get_memory_stats()
    return result

@app.post("/api/knowledge/note")
async def knowledge_add_note(
    content: str = Form(...),
    category: str = Form(default="genel"),
    tags: str = Form(default=""),
):
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    return add_note(content, category, tag_list)

def extract_pdf_text(raw: bytes) -> str:
    """PDF → TXT: pypdf + pdfminer.six fallback, hata toleranslı"""
    import io
    # Yöntem 1: pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw), strict=False)
        pages = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
                if t.strip():
                    pages.append(t)
            except Exception:
                continue
        text = "\n\n".join(pages)
        if text.strip():
            return text
    except Exception as e:
        print(f"[PDF] pypdf hatası: {e}")
    # Yöntem 2: pdfminer.six (daha güçlü metin çıkarma)
    try:
        from pdfminer.high_level import extract_text as pm_extract
        text = pm_extract(io.BytesIO(raw))
        if text and text.strip():
            return text
    except Exception as e:
        print(f"[PDF] pdfminer hatası: {e}")
    # Yöntem 3: pdfplumber (tablo destekli)
    try:
        import pdfplumber, io as _io
        pages = []
        with pdfplumber.open(_io.BytesIO(raw)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t.strip():
                    pages.append(t)
        text = "\n\n".join(pages)
        if text.strip():
            return text
    except Exception:
        pass
    return ""

@app.post("/api/knowledge/document")
async def knowledge_add_document(
    title: str = Form(default=""),
    category: str = Form(default="genel"),
    file: UploadFile = File(...),
):
    raw = await file.read()
    fname = (file.filename or "").lower()
    if not title.strip():
        title = (file.filename or "Doküman").rsplit(".", 1)[0]

    if fname.endswith(".pdf"):
        try:
            content = extract_pdf_text(raw)
            if not content.strip():
                raise HTTPException(status_code=400,
                    detail=f"'{file.filename}' taranmış (resim) PDF — metin çıkarılamadı. "
                           "Sohbete yükleyin, Gemini görüntü olarak okur.")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"PDF hatası ({file.filename}): {str(e)[:120]}")
    else:
        try:
            content = raw.decode("utf-8", errors="ignore")
        except Exception:
            content = raw.decode("latin-1", errors="ignore")

    if not content.strip():
        raise HTTPException(status_code=400, detail="Dosya boş veya okunamadı")
    result = add_document(title, content, category, source=file.filename)
    return result

@app.post("/api/knowledge/search")
async def knowledge_search(query: str = Form(...), category: str = Form(default=None)):
    results = search_knowledge(query, top_k=8, category=category or None)
    return {"results": results, "count": len(results)}

@app.delete("/api/knowledge/document/{title}")
async def knowledge_delete_doc(title: str):
    return delete_document(title)

@app.delete("/api/knowledge/note/{note_id}")
async def knowledge_delete_note(note_id: int):
    return delete_note(note_id)


@app.post("/api/chat")
async def chat(
    message: str = Form(...),
    history: str = Form(default="[]"),
    file: Optional[UploadFile] = File(default=None),
    stream: str = Form(default="0"),
):
    api_key = get_gemini_key()

    # ── Dosya işleme ──
    file_parts = []
    file_label = ""
    detected_code_lang = ""

    CODE_EXTENSIONS = {
        ".py":   ("python",     "🐍 Python"),
        ".js":   ("javascript", "🟨 JavaScript"),
        ".ts":   ("typescript", "🔷 TypeScript"),
        ".jsx":  ("javascript", "⚛️ React JSX"),
        ".tsx":  ("typescript", "⚛️ React TSX"),
        ".pine": ("pine",       "📊 Pine Script"),
        ".pine4":("pine",       "📊 Pine Script v4"),
        ".pine5":("pine",       "📊 Pine Script v5"),
        ".mq4":  ("mql4",       "📈 MQL4"),
        ".mq5":  ("mql5",       "📈 MQL5"),
        ".json": ("json",       "📋 JSON"),
        ".csv":  ("csv",        "📊 CSV"),
        ".txt":  ("text",       "📄 Metin"),
        ".md":   ("markdown",   "📝 Markdown"),
        ".yaml": ("yaml",       "⚙️ YAML"),
        ".yml":  ("yaml",       "⚙️ YAML"),
        ".toml": ("toml",       "⚙️ TOML"),
        ".env":  ("text",       "🔑 Env"),
        ".sh":   ("bash",       "💻 Shell"),
        ".bat":  ("batch",      "💻 Batch"),
        ".sql":  ("sql",        "🗄️ SQL"),
        ".html": ("html",       "🌐 HTML"),
        ".css":  ("css",        "🎨 CSS"),
        ".rs":   ("rust",       "🦀 Rust"),
        ".go":   ("go",         "🐹 Go"),
        ".cpp":  ("cpp",        "⚙️ C++"),
        ".c":    ("c",          "⚙️ C"),
    }

    LANG_INSTRUCTIONS = {
        "python":     "Bu bir Python bot/script dosyası. Kodu detaylıca analiz et: mantık hataları, optimizasyon fırsatları, güvenlik açıkları, kripto trading ile ilgili sorunlar.",
        "javascript": "Bu bir JavaScript/Node.js dosyası. Async/await kullanımı, hata yönetimi, API çağrıları ve trading mantığını analiz et.",
        "typescript": "Bu bir TypeScript dosyası. Tip güvenliği, interface kullanımı ve trading mantığını analiz et.",
        "pine":       "Bu bir TradingView Pine Script dosyası. İndikatör/strateji mantığını, giriş-çıkış sinyallerini, risk yönetimini analiz et.",
        "mql4":       "Bu bir MetaTrader 4 MQL4 dosyası. EA veya indikatör mantığını analiz et.",
        "mql5":       "Bu bir MetaTrader 5 MQL5 dosyası. EA veya indikatör mantığını analiz et.",
        "json":       "Bu bir JSON konfigürasyon dosyası. Yapıyı ve parametreleri analiz et.",
        "csv":        "Bu bir CSV veri dosyası. Veriyi özetle ve analiz et.",
    }

    if file and file.filename:
        raw = await file.read()
        fname = file.filename.lower()
        ext   = "." + fname.rsplit(".", 1)[-1] if "." in fname else ""

        if any(fname.endswith(x) for x in [".jpg", ".jpeg", ".png", ".gif", ".webp"]):
            mt = ("image/jpeg" if fname.endswith((".jpg", ".jpeg")) else
                  "image/png"  if fname.endswith(".png") else
                  "image/gif"  if fname.endswith(".gif") else "image/webp")
            b64 = base64.standard_b64encode(raw).decode()
            file_parts = [{"inline_data": {"mime_type": mt, "data": b64}}]
            file_label = f"\n[📸 Grafik/Resim yüklendi: {file.filename}]"

        elif fname.endswith(".pdf"):
            # ÇİFT YÖNTEM: metin çıkar (her durumda çalışır) + küçükse görüntü olarak da gönder
            pdf_text = extract_pdf_text(raw)
            if len(raw) < 4_000_000:  # 4MB altı: Gemini'ye görüntü olarak da ver
                b64 = base64.standard_b64encode(raw).decode()
                file_parts = [{"inline_data": {"mime_type": "application/pdf", "data": b64}}]
            if pdf_text.strip():
                kisaltma = pdf_text[:30000]
                file_label = (f"\n[📄 PDF yüklendi: {file.filename} — metin içeriği aşağıda]\n"
                              f"--- PDF METNİ BAŞLANGIÇ ---\n{kisaltma}\n--- PDF METNİ SON ---")
            elif file_parts:
                file_label = f"\n[📄 PDF yüklendi (taranmış/görsel): {file.filename}]"
            else:
                file_label = (f"\n[⚠️ PDF okunamadı: {file.filename} — hem metin çıkarma hem boyut "
                              f"({len(raw)//1024//1024}MB > 4MB) başarısız. Kullanıcıya bunu açıkla.]")

        elif ext in CODE_EXTENSIONS:
            lang_key, lang_label = CODE_EXTENSIONS[ext]
            detected_code_lang = lang_key
            try:
                # UTF-8, sonra latin-1 dene
                try:
                    code_text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    code_text = raw.decode("latin-1", errors="ignore")

                # Çok büyük dosyaları böl
                max_chars = 15000
                truncated = ""
                if len(code_text) > max_chars:
                    code_text = code_text[:max_chars]
                    truncated = f"\n[⚠️ Dosya çok büyük, ilk {max_chars} karakter gösteriliyor]"

                lang_hint = LANG_INSTRUCTIONS.get(lang_key, "Bu bir kod dosyası. Detaylıca analiz et.")
                file_label = (
                    f"\n[{lang_label} dosyası yüklendi: {file.filename} "
                    f"({len(raw)} byte, {code_text.count(chr(10))+1} satır)]{truncated}\n"
                    f"[Talimat: {lang_hint}]\n"
                    f"```{lang_key}\n{code_text}\n```"
                )
            except Exception as e:
                file_label = f"\n[{lang_label} dosyası yüklendi: {file.filename}] (okuma hatası: {e})"

        else:
            # Bilinmeyen uzantı — metin olarak okumayı dene
            try:
                txt = raw.decode("utf-8", errors="ignore")[:12000]
                file_label = f"\n[📎 Dosya: {file.filename}]\n```\n{txt}\n```"
            except Exception:
                file_label = f"\n[📎 Dosya yüklendi: {file.filename}]"

    # ── Bilgi Bankası — ilgili içeriği otomatik getir ──
    kb_context = get_full_context(message, max_chars=4000)
    # 240+ kitaptan ilgili bilgi (SQLite FTS5)
    try:
        from kitap_db import ara as _kitap_ara
        _ks = _kitap_ara(message, limit=3)
        if _ks:
            kb_context += "\n\n[Kitap bilgisi]:\n" + "\n".join(f"({s['title']}): {s['content'][:300]}" for s in _ks)
    except Exception:
        pass

    # ── DÜZELTME ÖĞRENME: kullanıcı düzeltirse yüksek öncelikli kaydet ──
    correction_markers = ["yanlış", "hayır öyle değil", "düzelt", "hatalı", "doğrusu şu",
                          "öyle değil", "yanlış anladın", "tekrar bak"]
    if any(m in message.lower()[:80] for m in correction_markers):
        try:
            hist_check = json.loads(history)
            if hist_check:
                last_ai = next((h["content"] for h in reversed(hist_check)
                               if h.get("role") == "assistant"), "")
                if last_ai:
                    add_note(
                        f"DÜZELTME — Kullanıcı şu cevabımı düzeltti. Yanlış cevap: '{last_ai[:200]}'. "
                        f"Kullanıcının düzeltmesi: '{message[:300]}'. Bu hatayı bir daha yapma.",
                        category="düzeltmeler"
                    )
        except Exception:
            pass

    # ── "Bunu bil:" veya "Not ekle:" ile başlayan mesajları otomatik kaydet ──
    auto_saved = ""
    msg_lower_stripped = message.strip()
    for prefix in ["bunu bil:", "not ekle:", "öğren:", "hatırla:", "kaydet:"]:
        if msg_lower_stripped.lower().startswith(prefix):
            note_content = msg_lower_stripped[len(prefix):].strip()
            if note_content:
                add_note(note_content, category="öğrenilen")
                auto_saved = f"\n[✅ Kalıcı hafızaya kaydedildi: '{note_content[:60]}...']"
            break
    msg_lower = message.lower()
    crypto_kws = ["btc","eth","kripto","fiyat","piyasa","coin","altcoin","usdt","borsa",
                  "analiz","chart","grafik","market","bitcoin","ethereum","sol","bnb",
                  "xrp","ada","doge","avax","link","matic","pepe","shib","dominans",
                  "korku","açgözlülük","fear","greed","pump","dump","bull","bear",
                  "opsiyon","option","pcr","iv","volatil","deribit","dvol","call","put",
                  "strike","vade","expiry","implied","açık pozisyon","open interest"]

    fetch = any(k in msg_lower for k in crypto_kws) or bool(file_parts)

    crypto_ctx = ""
    if fetch:
        # Spot veri — HEPSİ PARALEL (hız için)
        detected = detect_coins(message)
        if not detected:
            detected = ["bitcoin", "ethereum"]
        detected = detected[:3]

        # CoinGecko + F&G + Global paralel
        cg_results, fg, gm = await asyncio.gather(
            asyncio.gather(*[get_coingecko_coin(cid) for cid in detected]),
            get_fear_greed(),
            get_global_stats(),
        )

        # Borsa fiyatları paralel
        bn_results = await asyncio.gather(
            *[get_binance_ticker(cg.get("symbol", "")) if cg else asyncio.sleep(0, result={})
              for cg in cg_results]
        )

        coin_blocks = []
        for cg, bn in zip(cg_results, bn_results):
            if not cg:
                continue
            block = (
                f"### {cg['name']} ({cg['symbol']})\n"
                f"- Fiyat (CoinGecko): ${cg['price_usd']:,.4f}\n"
            )
            if bn.get("price"):
                block += f"- Fiyat ({bn.get('source','Borsa')}): ${bn['price']:,.4f}\n"
            block += (
                f"- Değişim 1s/24s/7g/30g: {cg['change_1h']:+.2f}% / {cg['change_24h']:+.2f}% / "
                f"{cg['change_7d']:+.2f}% / {cg['change_30d']:+.2f}%\n"
                f"- Piyasa Değeri: ${cg['market_cap_usd']/1e9:.2f}B | ATH: ${cg['ath_usd']:,.2f} "
                f"({cg['ath_change_pct']:.1f}%)\n"
            )
            coin_blocks.append(block)

        crypto_ctx = "\n\n━━━ CANLI SPOT VERİSİ ━━━\n"
        crypto_ctx += "\n".join(coin_blocks)
        if fg.get("today"):
            t, y = fg["today"], fg.get("yesterday", {})
            crypto_ctx += (f"\n**Korku/Açgözlülük:** Bugün {t['value']}/100 ({t['label']}) | "
                           f"Dün {y.get('value','?')}/100 ({y.get('label','?')})\n")
        if gm:
            crypto_ctx += (f"**Piyasa:** ${gm['total_market_cap_trillion']:.2f}T | "
                           f"BTC Dom: {gm['btc_dominance_pct']}% | "
                           f"24s: {gm['market_cap_change_24h_pct']:+.2f}%\n")

        # Deribit opsiyon verisi
        deribit_kws = ["opsiyon","option","pcr","iv","volatil","deribit","dvol",
                       "call","put","strike","vade","implied","açık pozisyon","open interest"]
        fetch_deribit = any(k in msg_lower for k in deribit_kws)

        # BTC veya ETH soruluyorsa her zaman Deribit ekle
        if not fetch_deribit and any(k in msg_lower for k in ["btc","bitcoin","eth","ethereum"]):
            fetch_deribit = True

        if fetch_deribit:
            cur = detect_deribit_currency(message)
            summary, strikes, dvol = await asyncio.gather(
                get_deribit_summary(cur),
                get_deribit_top_strikes(cur, limit=8),
                get_deribit_dvol(cur),
            )

            if summary:
                crypto_ctx += f"\n━━━ DERİBİT OPSİYON VERİSİ ({cur}) ━━━\n"
                crypto_ctx += (
                    f"- Toplam Açık Pozisyon (OI): {summary['total_open_interest']:,.0f} {cur}\n"
                    f"- Call OI: {summary['call_open_interest']:,.0f} | Put OI: {summary['put_open_interest']:,.0f}\n"
                    f"- PCR (OI bazlı): {summary['pcr_oi']} {'🔴 Ayı' if summary['pcr_oi']>1 else '🟢 Boğa'} baskısı\n"
                    f"- PCR (Hacim bazlı): {summary['pcr_volume']}\n"
                    f"- 24s Opsiyon Hacmi: {summary['total_volume_24h']:,.0f} {cur}\n"
                    f"- Ortalama IV: %{summary['avg_iv_pct']}\n"
                    f"- Aktif Enstrüman: {summary['instrument_count']}\n"
                )
            if dvol.get("dvol"):
                crypto_ctx += f"- DVOL (30g volatilite endeksi): {dvol['dvol']}\n"
            if strikes:
                crypto_ctx += f"\n**En Yüksek OI'li Strike'lar ({cur}):**\n"
                for s in strikes[:8]:
                    bar = "█" * min(int(s['open_interest']/max(strikes[0]['open_interest'],1)*10), 10)
                    crypto_ctx += f"  ${s['strike']} {s['type']} — OI: {s['open_interest']:,.0f} {cur} {bar}\n"

        crypto_ctx += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

    # ── Hafıza & Bilgi Bankası Entegrasyonu ──
    memory_ctx = ""
    learned_something = False

    # Öğretme niyeti var mı?
    is_learning, learn_category = detect_learn_intent(message)
    if is_learning:
        add_memory(message, category=learn_category)
        learned_something = True
        memory_ctx += "\n[✅ Bu bilgi hafızana kaydedildi]\n"

    # Yüklenen dosyayı bilgi bankasına ekle (kod değilse)
    if file_label and not detected_code_lang and file and file.filename:
        fname_clean = file.filename.replace(" ", "_")
        # Dosya içeriğini al
        try:
            raw2 = Path("uploads") / file.filename
            content_for_index = ""
            if file_label and "```" in file_label:
                content_for_index = file_label.split("```")[1] if "```" in file_label else ""
            if content_for_index:
                result = index_document(fname_clean, content_for_index, doc_type="upload")
                memory_ctx += f"\n[📚 '{file.filename}' bilgi bankasına eklendi ({result.get('chunks',0)} bölüm)]\n"
        except Exception:
            pass

    # İlgili hafıza ve bilgi bankasını getir
    memory_context = build_context_for_query(message)
    if memory_context:
        memory_ctx += "\n" + memory_context

    # ── Mesaj geçmişi ──
    try:
        hist = json.loads(history)[-20:]
    except Exception:
        hist = []

    contents = []
    for h in hist:
        role = "user" if h["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": h["content"]}]})

    user_text  = message + file_label + auto_saved + kb_context + crypto_ctx + memory_ctx
    user_parts = file_parts + [{"text": user_text}]
    contents.append({"role": "user", "parts": user_parts})

    # ── STREAMING MODU ──
    if stream == "1":
        meta = {
            "type": "meta",
            "had_crypto": bool(crypto_ctx),
            "had_file": bool(file_label),
            "had_kb": bool(kb_context),
            "auto_saved": bool(auto_saved),
            "code_lang": detected_code_lang,
        }
        async def event_gen():
            full_reply = []
            yield json.dumps(meta, ensure_ascii=False) + "\n"
            try:
                _baglam = await canli_sistem_baglami()
                _sp = SYSTEM_PROMPT + (f"\n\n══ CANLI SİSTEM VERİLERİ ══\n{_baglam}" if _baglam else "")
                async for chunk in stream_ai(api_key, contents, _sp):
                    full_reply.append(chunk)
                    yield json.dumps({"type": "delta", "text": chunk}, ensure_ascii=False) + "\n"
                final = "".join(full_reply)
                try:
                    log_exchange(message, final)
                except Exception:
                    pass
                yield json.dumps({"type": "done",
                                  "model": globals().get("_last_model_used", GEMINI_MODEL)},
                                 ensure_ascii=False) + "\n"
            except HTTPException as he:
                yield json.dumps({"type": "error", "detail": he.detail},
                                 ensure_ascii=False) + "\n"
        return StreamingResponse(event_gen(), media_type="application/x-ndjson")

    # ── NORMAL MOD (backtest vb. için) ──
    _baglam2 = await canli_sistem_baglami()
    _sp2 = SYSTEM_PROMPT + (f"\n\n══ CANLI SİSTEM VERİLERİ ══\n{_baglam2}" if _baglam2 else "")
    reply = await call_gemini(api_key, contents, _sp2)

    # ── OTOMATİK KALICI KAYIT — her yazışma sonsuza dek hafızada ──
    try:
        log_exchange(message, reply)
    except Exception as e:
        print(f"[Hafıza] Kayıt hatası: {e}")

    # Cevabı da hafızaya kaydet (önemli bilgi içeriyorsa)
    if learned_something:
        add_memory(f"Kullanıcı sorusu: {message[:200]}\nCevap özeti: {reply[:300]}",
                   category="konuşma")

    return {
        "reply": reply,
        "had_crypto": bool(crypto_ctx),
        "had_file": bool(file_label),
        "had_kb": bool(kb_context),
        "auto_saved": bool(auto_saved),
        "code_lang": detected_code_lang,
        "model": globals().get("_last_model_used", GEMINI_MODEL),
    }

if __name__ == "__main__":
    import uvicorn
    print("🚀 Crypto AI Agent (Gemini + Deribit) başlatılıyor...")
    print("🌐 http://localhost:8000")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
