"""
autonomous_researcher.py — OAR Otonom Araştırma Sistemi
════════════════════════════════════════════════════════
Tamamen otonom çalışır:
1. Asia Range + Fib üzerinde hipotez üretir
2. Mevcut veriyle hızlı backtest koşturur
3. Her sistemi 0-100 puan verir
4. En yüksek puanlı sistemleri Leader'a raporlar

Puanlama:
  Win Rate (40pt) + Sharpe (25pt) + Trade Sayısı (15pt) + Max DD (10pt) + Calmar (10pt)

Lokal runner'dan da çağrılabilir (ağır hesaplar için).
"""
import os, json, asyncio, time, uuid, statistics
from pathlib import Path
from datetime import datetime, timezone, timedelta

DATA_DIR    = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or ("/var/data" if Path("/var/data").exists() else "data"))
ARAŞTIRMA_DOSYASI = DATA_DIR / "oar_arastirma.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Sabit parametreler ────────────────────────────────────────────────────────
FIB_ORANLARI = [2.272, 2.618, -1.272, -1.618]

PARAMETRE_GRID = [
    # touch_pct, eval_saat, taker_esik, btc_min_range, filtre_seti
    (0.002, 4, 50.5, 0.8, "minimal"),
    (0.003, 4, 51.0, 1.0, "standart"),
    (0.003, 8, 52.0, 1.0, "standart_uzun"),
    (0.004, 4, 50.0, 0.8, "genis"),
    (0.003, 4, 53.0, 1.2, "katı"),
    (0.005, 8, 50.0, 0.8, "cok_genis"),
    (0.002, 6, 52.0, 1.0, "hassas"),
    (0.003, 4, 51.0, 0.8, "btc_esik_dusuk"),
]

# ── Yardımcı: DB ──────────────────────────────────────────────────────────────
def _yükle():
    try: return json.loads(ARAŞTIRMA_DOSYASI.read_text()) if ARAŞTIRMA_DOSYASI.exists() else {"sistemler":[], "en_iyi":None, "guncelleme":None}
    except: return {"sistemler":[], "en_iyi":None, "guncelleme":None}

def _kaydet(d):
    ARAŞTIRMA_DOSYASI.write_text(json.dumps(d, ensure_ascii=False, indent=2))

# ── Puanlama ──────────────────────────────────────────────────────────────────
def puan_hesapla(stats: dict) -> int:
    """
    0-100 arası OAR Sistem Puanı:
      Win Rate  (40): %70+ = 40, %60+ = 30, %50+ = 18, altı = 0
      Sharpe    (25): 2.0+ = 25, 1.5+ = 20, 1.0+ = 14, 0.5+ = 8, altı = 0
      Trade     (15): 50+ = 15, 30+ = 10, 15+ = 6, altı = 0
      Max DD    (10): <3% = 10, <5% = 7, <8% = 4, üstü = 0
      Calmar    (10): 3.0+ = 10, 2.0+ = 7, 1.0+ = 4, altı = 0
    """
    if not stats or stats.get("toplam_sinyal", 0) < 5: return 0
    p = 0
    wr   = stats.get("win_rate", 0)
    sh   = stats.get("sharpe", 0)
    tr   = stats.get("toplam_sinyal", 0)
    dd   = abs(stats.get("max_drawdown", 100))
    cal  = stats.get("calmar", 0)

    p += 40 if wr>=70 else 30 if wr>=60 else 18 if wr>=50 else 0
    p += 25 if sh>=2.0 else 20 if sh>=1.5 else 14 if sh>=1.0 else 8 if sh>=0.5 else 0
    p += 15 if tr>=50 else 10 if tr>=30 else 6 if tr>=15 else 0
    p += 10 if dd<3 else 7 if dd<5 else 4 if dd<8 else 0
    p += 10 if cal>=3.0 else 7 if cal>=2.0 else 4 if cal>=1.0 else 0
    return min(p, 100)

def puan_etiketi(puan: int) -> str:
    if puan >= 80: return "⭐ MÜKEMMEL"
    if puan >= 65: return "✅ İYİ"
    if puan >= 50: return "⚠️ ORTA"
    if puan >= 35: return "❌ ZAYIF"
    return "🚫 KULLANILMAZ"

# ── Hızlı istatistik hesabı ───────────────────────────────────────────────────
def _istatistik(sinyaller: list) -> dict:
    tamamlanan = [s for s in sinyaller if s.get("outcome") in ("WIN","LOSS")]
    filt       = [s for s in sinyaller if s.get("outcome") == "FILTERED"]
    if not tamamlanan: return {"toplam_sinyal":0}

    w    = sum(1 for s in tamamlanan if s["outcome"]=="WIN")
    pnls = [s.get("pct",0) for s in tamamlanan]
    total= sum(pnls)

    # Max drawdown
    eq=0; pk=0; dd=0
    for p in pnls:
        eq+=p; pk=max(pk,eq); dd=max(dd,pk-eq)

    # Sharpe
    sh = 0
    if len(pnls)>1:
        mu=statistics.mean(pnls); std=statistics.stdev(pnls)
        sh = round((mu/std)*(252**0.5),3) if std>0 else 0

    calmar = round(total/dd,3) if dd>0 else 0

    # Fib kırılımı
    by_fib = {}
    for s in tamamlanan:
        fk = str(s.get("fib","?"))
        if fk not in by_fib: by_fib[fk]={"total":0,"wins":0,"pnl":0.0}
        by_fib[fk]["total"]+=1; by_fib[fk]["pnl"]+=s.get("pct",0)
        if s["outcome"]=="WIN": by_fib[fk]["wins"]+=1
    for fk in by_fib:
        t=by_fib[fk]["total"]
        by_fib[fk]["win_rate"]=round(by_fib[fk]["wins"]/t*100,1) if t else 0

    return {
        "toplam_sinyal":   len(tamamlanan),
        "filtrelenen":     len(filt),
        "win":             w,
        "loss":            len(tamamlanan)-w,
        "win_rate":        round(w/len(tamamlanan)*100,1),
        "toplam_pnl_pct":  round(total,3),
        "max_drawdown":    round(dd,3),
        "sharpe":          sh,
        "calmar":          calmar,
        "by_fib":          by_fib,
    }

# ── Hızlı Backtest (veri DB'den okunur) ──────────────────────────────────────
async def hizli_backtest(sym: str, gun: int, touch_pct: float,
                          eval_saat: int, taker_esik: float,
                          btc_min_range: float) -> dict:
    """
    historical_backtest.bt_oar_tam'ı parametrelerle çağırır.
    Render'da hafif tutmak için gun<=30 zorunlu.
    """
    try:
        from historical_backtest import bt_oar_tam, OAR_DEFAULT_PARAMS
        import copy
        params = copy.deepcopy(OAR_DEFAULT_PARAMS)
        params.update({
            "touch_pct":           touch_pct,
            "eval_hours":          eval_saat,
            "taker_threshold_pct": taker_esik,
            "btc_min_range_pct":   btc_min_range,
            "filter_taker":        False,  # Geçmiş veri için kapalı
            "filter_oi":           False,
        })
        return await bt_oar_tam(sym, gun, params)
    except Exception as e:
        return {"hata": str(e)[:80]}

# ── Tek Sistem Testi ──────────────────────────────────────────────────────────
async def sistem_test(sym: str, gun: int, params_tuple: tuple) -> dict:
    touch_pct, eval_saat, taker_esik, btc_min_range, etiket = params_tuple
    run_id = f"{sym}_{etiket}_{uuid.uuid4().hex[:6]}"

    sonuc = await hizli_backtest(sym, gun, touch_pct, eval_saat,
                                  taker_esik, btc_min_range)
    if sonuc.get("hata"):
        return {"run_id":run_id,"puan":0,"hata":sonuc["hata"]}

    stats = {
        "toplam_sinyal":  sonuc.get("toplam_sinyal",0),
        "filtrelenen":    sonuc.get("filtrelenen",0),
        "win_rate":       sonuc.get("win_rate",0),
        "toplam_pnl_pct": sonuc.get("toplam_pnl_pct",0),
        "max_drawdown":   sonuc.get("max_drawdown",0),
        "sharpe":         sonuc.get("sharpe",0),
        "calmar":         sonuc.get("calmar",0),
        "by_fib":         sonuc.get("by_fib",{}),
    }

    puan = puan_hesapla(stats)
    return {
        "run_id":     run_id,
        "sembol":     sym,
        "gun":        gun,
        "params":     {
            "touch_pct":    touch_pct,
            "eval_saat":    eval_saat,
            "taker_esik":   taker_esik,
            "btc_min_range":btc_min_range,
            "etiket":       etiket,
        },
        "stats":      stats,
        "puan":       puan,
        "seviye":     puan_etiketi(puan),
        "tarih":      datetime.now(timezone.utc).isoformat(),
    }

# ── Tam Grid Araştırması ──────────────────────────────────────────────────────
async def grid_arastir(sym: str = "BTCUSDT", gun: int = 30,
                        max_paralel: int = 2) -> dict:
    """
    Tüm parametre kombinasyonlarını test eder.
    Render Starter için max_paralel=2 (CPU koruması).
    Lokal için max_paralel=8.
    """
    print(f"[Araştırma] {sym} {gun}g grid başlıyor ({len(PARAMETRE_GRID)} sistem)...")
    sonuclar = []

    # Semafor ile eş zamanlı istek sınırla
    sem = asyncio.Semaphore(max_paralel)

    async def _test(p):
        async with sem:
            return await sistem_test(sym, gun, p)

    tasks = [_test(p) for p in PARAMETRE_GRID]
    sonuclar = await asyncio.gather(*tasks, return_exceptions=True)

    # Hata olanları filtrele
    gecerli = [s for s in sonuclar if isinstance(s, dict) and not s.get("hata")]
    gecerli.sort(key=lambda s: s["puan"], reverse=True)

    en_iyi = gecerli[0] if gecerli else None

    # Fib bazında özet: hangi fib hangi parametre setinde en iyi?
    fib_ozet = {}
    for s in gecerli:
        for fk, fv in s.get("stats",{}).get("by_fib",{}).items():
            if fk not in fib_ozet or fv.get("win_rate",0) > fib_ozet[fk]["win_rate"]:
                fib_ozet[fk] = {
                    "win_rate": fv.get("win_rate",0),
                    "pnl":      fv.get("pnl",0),
                    "etiket":   s["params"]["etiket"],
                    "puan":     s["puan"],
                }

    ozet = {
        "sembol":       sym,
        "gun":          gun,
        "tarih":        datetime.now(timezone.utc).isoformat(),
        "test_sayisi":  len(gecerli),
        "en_iyi":       en_iyi,
        "fib_ozet":     fib_ozet,
        "sirali":       gecerli[:5],  # En iyi 5
    }

    # Kaydet
    db = _yükle()
    db["sistemler"].extend(gecerli)
    db["sistemler"] = db["sistemler"][-100:]  # Son 100 test
    db["en_iyi"] = en_iyi
    db["guncelleme"] = datetime.now(timezone.utc).isoformat()
    _kaydet(db)

    print(f"[Araştırma] Tamamlandı. En iyi: {en_iyi['puan'] if en_iyi else 0} puan / {en_iyi['params']['etiket'] if en_iyi else '-'}")
    return ozet

# ── Hipotez Üretici (AI çağrısı olmadan — kural tabanlı) ─────────────────────
def hipotez_uret(sonuclar: list) -> list:
    """
    Backtest sonuçlarından kural tabanlı hipotezler çıkar.
    (AI API çağrısı yapmadan, Starter plan için)
    """
    if not sonuclar: return []
    hipotezler = []

    # En iyi vs en kötü karşılaştır
    en_iyi  = max(sonuclar, key=lambda s: s["puan"])
    en_kotu = min(sonuclar, key=lambda s: s["puan"])

    # Touch pct farkı
    if en_iyi["params"]["touch_pct"] != en_kotu["params"]["touch_pct"]:
        hipotezler.append({
            "tip":    "PARAMETRE",
            "metin":  f"Touch toleransı %{en_iyi['params']['touch_pct']*100:.1f} en iyi sonuç veriyor "
                      f"(puan: {en_iyi['puan']} vs {en_kotu['puan']})",
            "oncelik": "YÜKSEK" if abs(en_iyi["puan"]-en_kotu["puan"])>20 else "ORTA",
        })

    # Eval saat farkı
    if en_iyi["params"]["eval_saat"] != en_kotu["params"]["eval_saat"]:
        hipotezler.append({
            "tip":    "SÜRE",
            "metin":  f"{en_iyi['params']['eval_saat']}h değerlendirme süresi "
                      f"{en_kotu['params']['eval_saat']}h'den daha başarılı",
            "oncelik": "ORTA",
        })

    # Fib bazında en iyi
    for s in sonuclar[:3]:
        by_fib = s.get("stats",{}).get("by_fib",{})
        for fk, fv in by_fib.items():
            if fv.get("win_rate",0) >= 65:
                hipotezler.append({
                    "tip":    "FIB",
                    "metin":  f"Fib {fk} seviyesi {fv['win_rate']}% win rate ile öne çıkıyor "
                              f"({s['params']['etiket']} parametresinde, {fv['total']} sinyal)",
                    "oncelik": "YÜKSEK" if fv.get("win_rate",0)>=70 else "ORTA",
                })

    return hipotezler[:8]

# ── Rapor Üret ────────────────────────────────────────────────────────────────
def rapor_olustur(ozet: dict) -> str:
    """Leader Agent'a gönderilecek metin raporu."""
    en_iyi = ozet.get("en_iyi")
    if not en_iyi:
        return "Araştırma sonucu yok."

    s = en_iyi["stats"]
    p = en_iyi["params"]

    fib_satirlari = "\n".join(
        f"  Fib {fk}: WR%{fv['win_rate']} | PnL%{fv['pnl']:.1f}"
        for fk,fv in sorted(s.get("by_fib",{}).items(), key=lambda x:-x[1].get("win_rate",0))
    )

    sirali = "\n".join(
        f"  #{i+1} [{s['seviye']}] Puan:{s['puan']} | WR%{s['stats']['win_rate']} | "
        f"Sharpe:{s['stats']['sharpe']} | {s['params']['etiket']}"
        for i,s in enumerate(ozet.get("sirali",[]))
    )

    hipler = "\n".join(
        f"  [{h['tip']}] {h['metin']}"
        for h in hipotez_uret(ozet.get("sirali",[]))
    )

    return f"""═══ OAR OTONOM ARAŞTIRMA RAPORU ═══
Sembol: {ozet['sembol']} | Test: {ozet['gun']} gün | {ozet['test_sayisi']} sistem

🥇 EN İYİ SİSTEM [{en_iyi['seviye']}] — PUAN: {en_iyi['puan']}/100
  Parametreler: touch=%{p['touch_pct']*100:.1f} eval={p['eval_saat']}h taker>{p['taker_esik']} btc_min=%{p['btc_min_range']}
  Win Rate: %{s['win_rate']} | Sharpe: {s['sharpe']} | Calmar: {s['calmar']}
  Toplam PnL: %{s['toplam_pnl_pct']} | Max DD: %{s['max_drawdown']} | {s['toplam_sinyal']} sinyal

FİB SEVİYELERİ:
{fib_satirlari}

TÜM SİSTEMLER SIRALI:
{sirali}

HİPOTEZLER:
{hipler}
═══════════════════════════════════"""

# ── Periodik Döngü ────────────────────────────────────────────────────────────
async def arastirma_loop(aralik_saat: int = 6, lokal_mod: bool = False):
    """
    Her aralik_saat saatte bir araştırma yapar.
    lokal_mod=True → daha uzun gun ve daha fazla paralel.
    """
    while True:
        try:
            gun        = 60 if lokal_mod else 30
            paralel    = 4  if lokal_mod else 2
            ozet = await grid_arastir("BTCUSDT", gun, paralel)
            rapor = rapor_olustur(ozet)
            print(rapor)

            # Leader agent'a bildir
            try:
                from leader_agent import _MEMORY as mem
                if mem: mem.set("oar_arastirma_raporu", rapor)
            except Exception:
                pass

        except Exception as e:
            print(f"[Araştırma] Hata: {e}")

        await asyncio.sleep(aralik_saat * 3600)

# ── API için yardımcı ─────────────────────────────────────────────────────────
def son_arastirma() -> dict:
    db = _yükle()
    return {
        "en_iyi":     db.get("en_iyi"),
        "guncelleme": db.get("guncelleme"),
        "son_5":      sorted(db.get("sistemler",[]), key=lambda s: s.get("puan",0), reverse=True)[:5],
        "rapor":      rapor_olustur({"sembol":"BTCUSDT","gun":30,
                                      "test_sayisi":len(db.get("sistemler",[])),
                                      "en_iyi":db.get("en_iyi"),
                                      "sirali":sorted(db.get("sistemler",[]),
                                                       key=lambda s:s.get("puan",0),reverse=True)[:5]})
                      if db.get("en_iyi") else "Henüz araştırma yapılmadı.",
    }
