"""
Theory Lab — OAR Premium Teori Kütüphanesi
═══════════════════════════════════════════════════════════
Teoriler: Draft → Testing → Confirmed / Rejected
Reddedilenler de saklanır — aynı şey tekrar araştırılmaz.

OAR Asia parametrik backtest: Asia Range (TR 03-07) üzerine
fib seviyesi teorilerini geçmiş veride test eder.
"""
import os, json, asyncio, httpx
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
TEORI_FILE = DATA_DIR / "teori_lab.json"
FAPI = "https://fapi.binance.com"

def _load():
    try:
        return json.loads(TEORI_FILE.read_text()) if TEORI_FILE.exists() else {"teoriler": []}
    except Exception: return {"teoriler": []}
def _save(d): TEORI_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2))
def _now(): return datetime.now(timezone.utc).isoformat()

# ── BAŞLANGIÇ TEORİLERİ (OAR-001..010) ─────────────────────────────
BASLANGIC = [
    {"id": "OAR-001", "hipotez": "Asia Range %37.7 seviyesinden dönüş", "fib": 0.377, "yontem": "donus"},
    {"id": "OAR-002", "hipotez": "Asia Range %61.8 seviyesinden dönüş", "fib": 0.618, "yontem": "donus"},
    {"id": "OAR-005", "hipotez": "0.377 dönüşü + haftalık VWAP üstü", "fib": 0.377, "yontem": "donus_vwap"},
    {"id": "OAR-006", "hipotez": "0.618 dönüşü + haftalık VWAP üstü", "fib": 0.618, "yontem": "donus_vwap"},
    {"id": "OAR-007", "hipotez": "Asia High sweep → 1.377 hedef", "fib": 1.377, "yontem": "sweep_high"},
    {"id": "OAR-008", "hipotez": "Asia Low sweep → -1.377 hedef", "fib": -1.377, "yontem": "sweep_low"},
    {"id": "OAR-009", "hipotez": "Asia ekstrem -1.272 LONG teması", "fib": -1.272, "yontem": "ekstrem"},
    {"id": "OAR-010", "hipotez": "Asia ekstrem 2.272 SHORT teması", "fib": 2.272, "yontem": "ekstrem"},
]

def teorileri_baslat():
    db = _load()
    mevcut = {t["id"] for t in db["teoriler"]}
    for b in BASLANGIC:
        if b["id"] not in mevcut:
            db["teoriler"].append({**b, "durum": "Draft", "olusturma": _now(),
                                   "testler": [], "not": ""})
    _save(db)
    return db

def teori_listesi():
    db = teorileri_baslat()
    return db["teoriler"]

def teori_guncelle(teori_id: str, durum: str = None, not_ekle: str = ""):
    db = _load()
    for t in db["teoriler"]:
        if t["id"] == teori_id:
            if durum: t["durum"] = durum
            if not_ekle: t["not"] = not_ekle
            t["guncelleme"] = _now()
    _save(db)

async def _klines_tum(cl, sym, interval, gun):
    bitir = int(datetime.now(timezone.utc).timestamp()*1000)
    cursor = bitir - gun*86400_000
    tum = []
    for _ in range(80):
        r = await cl.get(f"{FAPI}/fapi/v1/klines",
            params={"symbol": sym, "interval": interval, "startTime": cursor, "limit": 1000})
        d = r.json()
        if not isinstance(d, list) or not d: break
        tum.extend(d)
        if d[-1][0] >= bitir-60_000 or len(d) < 1000: break
        cursor = d[-1][0]+1
        await asyncio.sleep(0.12)
    return tum

async def teori_backtest(teori_id: str, sym: str = "BTCUSDT", gun: int = 365) -> dict:
    """OAR teorisini geçmiş veride test et, sonucu teoriye kaydet."""
    db = teorileri_baslat()
    teori = next((t for t in db["teoriler"] if t["id"] == teori_id), None)
    if not teori: return {"hata": f"{teori_id} bulunamadı"}
    fib = teori["fib"]; yontem = teori["yontem"]

    async with httpx.AsyncClient(timeout=30) as cl:
        k15 = await _klines_tum(cl, sym, "15m", gun)
    if len(k15) < 500: return {"hata": "veri yetersiz"}

    # Günlere ayır
    gunler = {}
    for i, k in enumerate(k15):
        t = datetime.fromtimestamp(k[0]/1000, tz=timezone.utc)
        gunler.setdefault(t.date(), []).append((i, k, t))

    ornekler = []
    for tarih, mumlar in sorted(gunler.items()):
        asia = [(i,k) for i,k,t in mumlar if 0 <= t.hour < 4]  # TR 03-07
        if len(asia) < 10: continue
        hi = max(float(k[2]) for _,k in asia)
        lo = min(float(k[3]) for _,k in asia)
        rng = hi - lo
        if rng <= 0 or rng/lo*100 < 0.5: continue
        mid = lo + rng*0.5
        sev = lo + rng*fib  # teori seviyesi

        islem_mumlari = [(i,k,t) for i,k,t in mumlar if 4 <= t.hour < 20]
        for i,k,t in islem_mumlari:
            h, l, cl_ = float(k[2]), float(k[3]), float(k[4])
            tetik = False; yon = None
            if yontem in ("donus","donus_vwap","ekstrem"):
                if l <= sev <= h:
                    tetik = True
                    yon = "LONG" if fib < 0.5 else "SHORT"
            elif yontem == "sweep_high" and h > hi:
                tetik = True; yon = "SHORT"; sev = hi
            elif yontem == "sweep_low" and l < lo:
                tetik = True; yon = "LONG"; sev = lo
            if not tetik: continue
            # Değerlendirme: 16 mum (4 saat) sonra
            j = min(i+16, len(k15)-1)
            giris, cikis = cl_, float(k15[j][4])
            pct = (cikis-giris)/giris*100
            if yon == "SHORT": pct = -pct
            out = "WIN" if pct >= 0.5 else "LOSS" if pct <= -0.5 else "FLAT"
            ornekler.append({"t": t.isoformat()[:16], "yon": yon, "out": out, "pct": round(pct,2)})
            break  # günde 1 örnek

    w = sum(1 for o in ornekler if o["out"]=="WIN")
    l_ = sum(1 for o in ornekler if o["out"]=="LOSS")
    top = w + l_
    pnls = [o["pct"] for o in ornekler if o["out"] in ("WIN","LOSS")]
    kar = sum(x for x in pnls if x > 0); zarar = abs(sum(x for x in pnls if x < 0))
    pf = round(kar/zarar, 2) if zarar else 0

    sonuc = {
        "teori_id": teori_id, "sembol": sym, "gun": gun, "tarih": _now(),
        "ornek": len(ornekler), "win": w, "loss": l_,
        "win_rate": round(w/top*100,1) if top else 0,
        "profit_factor": pf,
        "ort_pnl": round(sum(pnls)/len(pnls),2) if pnls else 0,
    }
    # Teoriye kaydet + otomatik durum
    teori["testler"].append(sonuc)
    if top >= 30:
        if sonuc["win_rate"] >= 60 and pf >= 1.5: teori["durum"] = "Confirmed"
        elif sonuc["win_rate"] < 45 or pf < 1.0:  teori["durum"] = "Rejected"
        else: teori["durum"] = "Testing"
    else:
        teori["durum"] = "Testing"
    _save(db)
    return {**sonuc, "yeni_durum": teori["durum"], "hipotez": teori["hipotez"]}
