"""
oar_autonomous_backtest.py — Otonom OAR Backtest Motoru
═══════════════════════════════════════════════════════════
Leader Agent tarafından yönetilir.
- Her gün otomatik çalışır
- Yeni bilgi öğrendikçe istediği zaman çalışır
- Tüm veri kaynaklarını kullanır:
  • Binance: 5m klines, OI, Funding, Taker buy/sell
  • Deribit: GEX, Call/Put Wall, Max Pain, DVOL
  • Kiyotaka: VPFR (POC, VAH, VAL), TPO, Likidasyonlar
  • Makro: DVOL rejimi, volatilite ortamı
  • Whale/Retail: OI delta yönü
  • Çeyreklik/Yıllık kapanışlar: kurumsal hareket bölgeleri
- Puanlama: 0-100
- En iyi sistemi Leader'a raporlar
"""

import os, json, asyncio, time, uuid, statistics, httpx
from pathlib import Path
from datetime import datetime, timezone, timedelta

DATA_DIR  = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or ("/var/data" if Path("/var/data").exists() else "data"))
BT_FILE   = DATA_DIR / "oar_otonom_bt.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)

FAPI = "https://fapi.binance.com"

# ── Parametre Uzayı ───────────────────────────────────────────────────────────
# Leader Agent bu parametreleri deneyerek en iyisini bulur
PARAM_GRID = [
    {"id":"std",         "touch":0.003, "eval_h":4,  "taker_esik":50.5, "gex_filter":True,  "vpfr_filter":True,  "liq_filter":False},
    {"id":"gex_heavy",   "touch":0.003, "eval_h":4,  "taker_esik":50.5, "gex_filter":True,  "vpfr_filter":False, "liq_filter":False},
    {"id":"vpfr_heavy",  "touch":0.003, "eval_h":6,  "taker_esik":50.5, "gex_filter":False, "vpfr_filter":True,  "liq_filter":False},
    {"id":"liq_heavy",   "touch":0.003, "eval_h":4,  "taker_esik":50.5, "gex_filter":False, "vpfr_filter":False, "liq_filter":True},
    {"id":"tam_filtre",  "touch":0.003, "eval_h":6,  "taker_esik":51.0, "gex_filter":True,  "vpfr_filter":True,  "liq_filter":True},
    {"id":"genis",       "touch":0.005, "eval_h":8,  "taker_esik":50.0, "gex_filter":False, "vpfr_filter":False, "liq_filter":False},
    {"id":"hassas",      "touch":0.002, "eval_h":4,  "taker_esik":52.0, "gex_filter":True,  "vpfr_filter":True,  "liq_filter":False},
    {"id":"uzun_eval",   "touch":0.003, "eval_h":12, "taker_esik":50.5, "gex_filter":True,  "vpfr_filter":False, "liq_filter":False},
]

# ── DB ────────────────────────────────────────────────────────────────────────
def _yukle():
    try: return json.loads(BT_FILE.read_text()) if BT_FILE.exists() else {"runs":[],"en_iyi":None,"son_guncelleme":None}
    except: return {"runs":[],"en_iyi":None,"son_guncelleme":None}

def _kaydet(d): BT_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2))

# ── Puan Hesabı ───────────────────────────────────────────────────────────────
def puan(stats: dict) -> int:
    if not stats or stats.get("n",0) < 5: return 0
    p  = 0
    wr = stats.get("wr", 0)
    sh = stats.get("sharpe", 0)
    n  = stats.get("n", 0)
    dd = abs(stats.get("max_dd", 100))
    cal= stats.get("calmar", 0)
    p += 40 if wr>=70 else 30 if wr>=60 else 18 if wr>=50 else 0
    p += 25 if sh>=2.0 else 20 if sh>=1.5 else 14 if sh>=1.0 else 8 if sh>=0.5 else 0
    p += 15 if n>=50  else 10 if n>=30   else 6  if n>=15   else 0
    p += 10 if dd<3   else 7  if dd<5    else 4  if dd<8    else 0
    p += 10 if cal>=3 else 7  if cal>=2  else 4  if cal>=1  else 0
    return min(p, 100)

def puan_etiketi(p: int) -> str:
    return "MÜKEMMEL" if p>=80 else "İYİ" if p>=65 else "ORTA" if p>=50 else "ZAYIF" if p>=35 else "KULLANILMAZ"

# ── Veri Çekiciler ────────────────────────────────────────────────────────────
async def _klines5m(sym, gun):
    end_ms = int(time.time() * 1000)
    st_ms  = end_ms - gun * 86400_000
    rows   = []
    cur    = st_ms
    async with httpx.AsyncClient(timeout=20) as cl:
        while cur < end_ms:
            r = await cl.get(f"{FAPI}/fapi/v1/klines",
                params={"symbol":sym,"interval":"5m","startTime":cur,"limit":1500})
            if r.status_code != 200: break
            d = r.json()
            if not d: break
            rows.extend(d)
            cur = d[-1][0] + 1
            if len(d) < 1500: break
            await asyncio.sleep(0.15)
    return rows

async def _oi_hist(sym, gun):
    end_ms = int(time.time() * 1000)
    st_ms  = end_ms - min(gun,29) * 86400_000
    rows   = []
    cur    = st_ms
    async with httpx.AsyncClient(timeout=20) as cl:
        while cur < end_ms:
            r = await cl.get(f"{FAPI}/futures/data/openInterestHist",
                params={"symbol":sym,"period":"30m","startTime":cur,"limit":500})
            d = r.json()
            if not isinstance(d,list) or not d: break
            rows.extend(d)
            cur = d[-1]["timestamp"] + 1
            if len(d) < 500: break
            await asyncio.sleep(0.1)
    return {d["timestamp"]: float(d.get("sumOpenInterest",0)) for d in rows}

async def _gex_snapshot():
    """Deribit GEX anlık — filtreleme için rejim ve duvarlar."""
    try:
        from options_engine import gex_ozet, alarm_levels
        gex = await gex_ozet("BTC")
        lv  = await alarm_levels("BTC")
        return {
            "rejim":     gex.get("gamma_rejim","?"),
            "net_gex":   gex.get("net_gex",0),
            "call_wall": lv.get("genel",{}).get("call_wall"),
            "put_wall":  lv.get("genel",{}).get("put_wall"),
            "zero_gamma":lv.get("genel",{}).get("zero_gamma"),
            "max_pain":  gex.get("max_pain"),
            "dvol":      gex.get("dvol_btc"),
        }
    except Exception:
        return {}

async def _vpfr_gun(sym, day_start_s):
    """O günün VPFR verisi — POC, VAH, VAL."""
    try:
        from kiyotaka_engine import get_volume_profile
        key = os.environ.get("KIYOTAKA_API_KEY","")
        if not key: return {}
        return await get_volume_profile(sym, day_start_s, 86400, key)
    except Exception:
        return {}

async def _likidasyon_gun(sym, ts_ms):
    """O mumdan önceki 1 saatteki likidasyon."""
    try:
        from kiyotaka_engine import get_liquidations
        key = os.environ.get("KIYOTAKA_API_KEY","")
        if not key: return {}
        return await get_liquidations(sym, int(ts_ms/1000)-3600, 3600, key)
    except Exception:
        return {}

def _ceyreklik_kapanislar(klines):
    """Klines içindeki çeyreklik/yıllık kapanış seviyelerini çıkar."""
    kapanislar = []
    for k in klines:
        ts = datetime.fromtimestamp(k[0]/1000, tz=timezone.utc)
        # Çeyreklik son gün: 3,6,9,12. ayların son günü yakını
        if ts.month in (3,6,9,12) and ts.day >= 28:
            kapanislar.append(float(k[4]))
        # Yıllık kapanış: Aralık son günleri
        elif ts.month == 12 and ts.day >= 28:
            kapanislar.append(float(k[4]))
    return list(set(kapanislar))

# ── İstatistik ────────────────────────────────────────────────────────────────
def _stat(sinyaller):
    tam = [s for s in sinyaller if s.get("out") in ("WIN","LOSS")]
    if not tam: return {"n":0}
    w    = sum(1 for s in tam if s["out"]=="WIN")
    pnls = [s.get("pct",0) for s in tam]
    tot  = sum(pnls)
    eq=pk=dd=0
    for p in pnls:
        eq+=p; pk=max(pk,eq); dd=max(dd,pk-eq)
    sh=0
    if len(pnls)>1:
        mu=statistics.mean(pnls); std=statistics.stdev(pnls)
        sh=round((mu/std)*(252**0.5),3) if std>0 else 0
    by_fib={}
    for s in tam:
        fk=str(s.get("fib","?"))
        if fk not in by_fib: by_fib[fk]={"n":0,"w":0,"pnl":0.0}
        by_fib[fk]["n"]+=1; by_fib[fk]["pnl"]+=s.get("pct",0)
        if s["out"]=="WIN": by_fib[fk]["w"]+=1
    for fk in by_fib:
        t=by_fib[fk]["n"]
        by_fib[fk]["wr"]=round(by_fib[fk]["w"]/t*100,1) if t else 0
    return {
        "n":len(tam), "w":w, "l":len(tam)-w,
        "wr":round(w/len(tam)*100,1),
        "pnl":round(tot,3),
        "max_dd":round(dd,3),
        "sharpe":sh,
        "calmar":round(tot/dd,3) if dd>0 else 0,
        "by_fib":by_fib,
        "filt": len([s for s in sinyaller if s.get("out")=="FILT"]),
    }

# ── Ana Backtest ──────────────────────────────────────────────────────────────
async def _backtest_gun(sym, klines, oi_map, gex_snap, vpfr_cache,
                        gun_tarih, params):
    """Tek gün backtest — tüm filtreler."""
    dt       = gun_tarih
    day_s_ms = int(dt.replace(tzinfo=timezone.utc).timestamp()*1000)
    asia_end = day_s_ms + 4*3600_000

    # Asia range
    asia_k = [k for k in klines if day_s_ms <= k[0] < asia_end]
    if len(asia_k) < 8: return []
    H = max(float(k[2]) for k in asia_k)
    L = min(float(k[3]) for k in asia_k)
    R = H - L
    if L <= 0 or R/L*100 < 0.8: return []

    fibs = {
        "2.272":  (L+R*2.272,  "SHORT"),
        "2.618":  (L+R*2.618,  "SHORT"),
        "-1.272": (L+R*-1.272, "LONG"),
        "-1.618": (L+R*-1.618, "LONG"),
    }

    # VPFR (bu gün için — önbellekten)
    vpfr = vpfr_cache.get(dt.date().isoformat(), {})

    # GEX filtre eşiği
    gex_neg = "NEGATİF" in gex_snap.get("rejim","")
    call_wall = gex_snap.get("call_wall", 0) or 0
    put_wall  = gex_snap.get("put_wall",  0) or 0

    gun_k    = [k for k in klines if asia_end <= k[0] < day_s_ms+86400_000]
    verilen  = set()
    sinyaller= []

    for i, k in enumerate(gun_k):
        ot,o,hi,lo,cl,vol,*rest = k
        tbv = float(k[9]) if len(k)>9 else vol*0.5

        for fib_key,(fib_fiyat,direction) in fibs.items():
            if fib_key in verilen: continue
            tol = fib_fiyat * params["touch"]
            if not (lo-tol <= fib_fiyat <= hi+tol): continue
            verilen.add(fib_key)

            filtre_log = {}

            # ── Taker ──
            buy_pct = (tbv/float(vol)*100) if float(vol)>0 else 50
            filtre_log["taker_buy_pct"] = round(buy_pct,1)
            if direction=="LONG"  and buy_pct < params["taker_esik"]: sinyaller.append({"fib":fib_key,"out":"FILT","neden":"taker","pct":0}); continue
            if direction=="SHORT" and buy_pct > 100-params["taker_esik"]: sinyaller.append({"fib":fib_key,"out":"FILT","neden":"taker","pct":0}); continue

            # ── OI Delta ──
            oi_ts = sorted(oi_map.keys())
            oi_s_list = [t for t in oi_ts if t <= ot]
            oi_o_list = [t for t in oi_ts if t <= ot-1800_000]
            if oi_s_list and oi_o_list:
                oi_s = oi_map[oi_s_list[-1]]; oi_o = oi_map[oi_o_list[-1]]
                oi_delta = (oi_s-oi_o)/oi_o*100 if oi_o>0 else 0
                filtre_log["oi_delta"] = round(oi_delta,2)
                if oi_delta < -0.5: sinyaller.append({"fib":fib_key,"out":"FILT","neden":"oi","pct":0}); continue

            # ── GEX Filtresi ──
            if params["gex_filter"] and call_wall and put_wall:
                if direction=="SHORT" and fib_fiyat < call_wall*0.995:
                    sinyaller.append({"fib":fib_key,"out":"FILT","neden":"gex_cw","pct":0}); continue
                if direction=="LONG"  and fib_fiyat > put_wall*1.005:
                    sinyaller.append({"fib":fib_key,"out":"FILT","neden":"gex_pw","pct":0}); continue
                filtre_log["gex_ok"] = True

            # ── VPFR Filtresi ──
            if params["vpfr_filter"] and vpfr and not vpfr.get("error"):
                poc = vpfr.get("poc", 0)
                vah = vpfr.get("vah", 0)
                val = vpfr.get("val", 0)
                if poc:
                    poc_mesafe = abs(fib_fiyat-poc)/poc*100
                    filtre_log["poc_mesafe_pct"] = round(poc_mesafe,2)
                    # POC'dan çok uzaksa (>2%) sinyal zayıf
                    if poc_mesafe > 2.0 and (val and vah):
                        if direction=="LONG"  and fib_fiyat > val*1.01:
                            sinyaller.append({"fib":fib_key,"out":"FILT","neden":"vpfr_va","pct":0}); continue
                        if direction=="SHORT" and fib_fiyat < vah*0.99:
                            sinyaller.append({"fib":fib_key,"out":"FILT","neden":"vpfr_va","pct":0}); continue

            # ── Değerlendirme ──
            tp = L if direction=="LONG" else H
            sl_mult = 1 - 0.005 if direction=="LONG" else 1 + 0.005
            sl = fib_fiyat * sl_mult
            out = pct = None

            for fk in gun_k[i+1:i+1+params["eval_h"]*12]:
                fc = float(fk[4])
                if direction=="LONG":
                    if fc >= tp: pct=round((fc-fib_fiyat)/fib_fiyat*100,3); out="WIN"; break
                    if fc <= sl: pct=round((fc-fib_fiyat)/fib_fiyat*100,3); out="LOSS"; break
                else:
                    if fc <= tp: pct=round((fib_fiyat-fc)/fib_fiyat*100,3); out="WIN"; break
                    if fc >= sl: pct=round((fib_fiyat-fc)/fib_fiyat*100,3); out="LOSS"; break

            if out is None:
                idx = min(i+params["eval_h"]*12, len(gun_k)-1)
                fc  = float(gun_k[idx][4])
                pct = round((fc-fib_fiyat)/fib_fiyat*100*(1 if direction=="LONG" else -1),3)
                out = "WIN" if pct>0 else "LOSS"

            sinyaller.append({
                "tarih": dt.strftime("%Y-%m-%d"),
                "fib":   fib_key,
                "yon":   direction,
                "giris": round(fib_fiyat,2),
                "tp":    round(tp,2),
                "asia_hi":round(H,2),
                "asia_lo":round(L,2),
                "rng_pct":round(R/L*100,2),
                "out":   out,
                "pct":   pct,
                "filtreler": filtre_log,
            })

    return sinyaller

# ── Tek Parametre Seti Backtest ───────────────────────────────────────────────
async def backtest_tek(sym, klines, oi_map, gex_snap, gun, params):
    """Verilen parametre setiyle gun sayısı kadar backtest."""
    end_dt   = datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    start_dt = end_dt - timedelta(days=gun)

    # VPFR önbelleği — Kiyotaka limiti korumak için gün başına 1 çağrı
    vpfr_cache = {}
    key = os.environ.get("KIYOTAKA_API_KEY","")
    if params.get("vpfr_filter") and key:
        # Son 7 günlük VPFR çek (daha eskisi için önbellek kullan)
        from kiyotaka_engine import get_volume_profile
        for i in range(min(gun,7)):
            d = end_dt - timedelta(days=i)
            d_s = int(d.timestamp())
            vp = await get_volume_profile(sym, d_s, 86400, key)
            vpfr_cache[d.date().isoformat()] = vp
            await asyncio.sleep(0.5)

    tum_sinyaller = []
    cur = start_dt
    while cur < end_dt:
        sinyaller = await _backtest_gun(sym, klines, oi_map, gex_snap,
                                         vpfr_cache, cur, params)
        tum_sinyaller.extend(sinyaller)
        cur += timedelta(days=1)

    return _stat(tum_sinyaller), tum_sinyaller

# ── Tam Otonom Backtest ───────────────────────────────────────────────────────
async def otonom_backtest_calistir(sym="BTCUSDT", gun=30, max_paralel=2,
                                    sebep="zamanli"):
    """
    Leader Agent tarafından çağrılır.
    Tüm parametre setlerini dener, en iyisini raporlar.
    sebep: 'zamanli' | 'yeni_bilgi' | 'manual'
    """
    print(f"[OtonomBT] {sym} {gun}g başlıyor — sebep: {sebep}")
    t0 = time.time()

    # Veri çek
    print(f"[OtonomBT] Veri çekiliyor...")
    klines, oi_map, gex_snap = await asyncio.gather(
        _klines5m(sym, gun),
        _oi_hist(sym, gun),
        _gex_snapshot(),
        return_exceptions=True,
    )
    if isinstance(klines, Exception) or not klines:
        return {"hata": "Klines çekilemedi"}
    if isinstance(oi_map, Exception): oi_map = {}
    if isinstance(gex_snap, Exception): gex_snap = {}

    print(f"[OtonomBT] {len(klines)} mum, GEX: {gex_snap.get('rejim','?')}")

    # Parametre grid
    sem     = asyncio.Semaphore(max_paralel)
    sonuclar= []

    async def _test(p):
        async with sem:
            stats, sigs = await backtest_tek(sym, klines, oi_map, gex_snap, gun, p)
            p_puan = puan(stats)
            print(f"[OtonomBT] {p['id']}: WR%{stats.get('wr',0)} Sh{stats.get('sharpe',0)} Puan:{p_puan}")
            return {
                "id":     p["id"],
                "params": p,
                "stats":  stats,
                "puan":   p_puan,
                "seviye": puan_etiketi(p_puan),
                "son_10": [s for s in sigs if s.get("out") in ("WIN","LOSS")][-10:],
            }

    tasks   = [_test(p) for p in PARAM_GRID]
    sonuclar= await asyncio.gather(*tasks, return_exceptions=True)
    sonuclar= [s for s in sonuclar if isinstance(s,dict) and not s.get("hata")]
    sonuclar.sort(key=lambda s: s["puan"], reverse=True)

    en_iyi = sonuclar[0] if sonuclar else None

    # Fib karşılaştırma
    fib_ozet = {}
    for s in sonuclar[:3]:
        for fk, fv in s["stats"].get("by_fib",{}).items():
            if fk not in fib_ozet or fv.get("wr",0) > fib_ozet[fk].get("wr",0):
                fib_ozet[fk] = {**fv, "params_id": s["id"]}

    sure = round(time.time()-t0, 1)
    run = {
        "run_id":       uuid.uuid4().hex[:8],
        "tarih":        datetime.now(timezone.utc).isoformat(),
        "sembol":       sym,
        "gun":          gun,
        "sebep":        sebep,
        "sure_sn":      sure,
        "gex_snapshot": gex_snap,
        "test_sayisi":  len(sonuclar),
        "en_iyi":       en_iyi,
        "fib_ozet":     fib_ozet,
        "sirali":       sonuclar[:5],
    }

    # Kaydet
    db = _yukle()
    db["runs"].append({k:v for k,v in run.items() if k!="sirali"})
    db["runs"] = db["runs"][-50:]
    db["en_iyi"] = en_iyi
    db["son_guncelleme"] = run["tarih"]
    _kaydet(db)

    print(f"[OtonomBT] Tamamlandı {sure}s — En iyi: {en_iyi['id'] if en_iyi else '-'} Puan:{en_iyi['puan'] if en_iyi else 0}")
    return run

# ── Periyodik Döngü ───────────────────────────────────────────────────────────
async def otonom_backtest_loop():
    """
    Günlük sabah 08:00 UTC otomatik çalışır.
    Yeni kural bankası güncellemelerinde de tetiklenir.
    """
    await asyncio.sleep(600)  # Startup'tan 10dk sonra başla

    son_kural_sayisi = 0

    while True:
        try:
            now = datetime.now(timezone.utc)

            # Sabah 08:00 UTC — günlük run
            if now.hour == 8 and now.minute < 10:
                await otonom_backtest_calistir("BTCUSDT", 30, sebep="zamanli_gunluk")
                await asyncio.sleep(600)  # Çift çalışmayı önle
                continue

            # Kural bankası değişti mi? (Kullanıcı yeni şey öğretti)
            try:
                from oar_rules import kural_sayisi
                mevcut = kural_sayisi().get("toplam_kural", 0)
                if mevcut > son_kural_sayisi + 2:  # 2+ yeni kural → tekrar test
                    print(f"[OtonomBT] Yeni kurallar öğrenildi ({son_kural_sayisi}→{mevcut}) — backtest tetiklendi")
                    await otonom_backtest_calistir("BTCUSDT", 15, sebep="yeni_bilgi")
                    son_kural_sayisi = mevcut
            except Exception:
                pass

        except Exception as e:
            print(f"[OtonomBT Loop] Hata: {e}")

        await asyncio.sleep(300)  # 5 dk'da bir kontrol

# ── API Yardımcıları ──────────────────────────────────────────────────────────
def son_sonuc():
    db = _yukle()
    en_iyi = db.get("en_iyi")
    runs   = db.get("runs",[])
    return {
        "son_guncelleme": db.get("son_guncelleme"),
        "toplam_run":     len(runs),
        "en_iyi":         en_iyi,
        "son_5_run":      runs[-5:],
        "rapor":          _rapor(db),
    }

def _rapor(db) -> str:
    en_iyi = db.get("en_iyi")
    if not en_iyi: return "Henüz otonom backtest yapılmadı."
    s  = en_iyi.get("stats",{})
    p  = en_iyi.get("params",{})
    fib_str = "\n".join(
        f"  Fib {fk}: WR%{fv.get('wr',0)} | {fv.get('n',0)} sinyal | PnL%{round(fv.get('pnl',0),2)}"
        for fk,fv in sorted(s.get("by_fib",{}).items(), key=lambda x:-x[1].get("wr",0))
    )
    return (
        f"OAR OTONOM BACKTEST — {db.get('son_guncelleme','?')[:10]}\n"
        f"En İyi: [{en_iyi.get('id')}] Puan:{en_iyi.get('puan',0)}/100 ({en_iyi.get('seviye','')})\n"
        f"WR:%{s.get('wr',0)} | Sharpe:{s.get('sharpe',0)} | PnL%:{s.get('pnl',0)} | MaxDD:%{s.get('max_dd',0)}\n"
        f"Parametreler: touch={p.get('touch')} eval={p.get('eval_h')}h "
        f"GEX={'ON' if p.get('gex_filter') else 'OFF'} "
        f"VPFR={'ON' if p.get('vpfr_filter') else 'OFF'}\n"
        f"Fib Seviyeleri:\n{fib_str}"
    )
