"""
oar_nedensel_denetim.py — LOOKAHEAD DENETİMİ: şampiyonun edge'i GERÇEK mi, gelecek-bilgisi mi?
════════════════════════════════════════════════════════════════════════════════════════════
KULLANICI HEDEFİ: "geçmiş VE GELECEKTE çalışacak en iyi OAR'ı kuralım."
Bunun ön şartı: bugünkü şampiyonun edge'inin GERÇEKTEN nedensel (causal) olduğunu bilmek.

🔴 BULGU (koddan, oar_local_backtest.py:711-731): şampiyonun İKİ giriş bloğu da GİRİŞ
   BARINDAN SONRAKİ barlara bakıyor — yani giriş anında var OLMAYAN bilgiyle işlem seçiyor:

     for j, (ts, fiyat) in enumerate(...):        # giriş barı j, giriş fiyatı = fiyat
         ilk5    = close_list[j+1 : j+6]          # SONRAKİ 5 bar
         ileri15 = close_list[j+1 : j+16]         # SONRAKİ 15 bar
         absorp  = (vol_z >= 1.0) and (max(ilk5) <= fiyat*1.001)     → footprint_absorpsiyon
         reclaim = any(c < seviye*0.997 for c in ileri15)            → footprint_trapped
         out, net = degerlendir_tpsl(fiyat, yon, tp, sl, ileri)      # PnL de bar j'den

   FADE  = poc_taraf + footprint_absorpsiyon(absorp)  + footprint_kalicilik
   TREND = poc_taraf + footprint_trapped(reclaim)     + gun_bias_uyum
   → FADE'in ve TREND'in birer bloğu LOOKAHEAD taşıyor. (footprint_kalicilik ve poc_taraf
     TEMİZ: yalnız [0..j] barlarını kullanıyor.)

⚠️ NEDEN SERAP TESTLERİ BUNU YAKALAMADI: DSR / permütasyon / bootstrap / Monte-Carlo /
   walk-forward hepsi İŞLEM KÜMESİ üzerinde çalışır. Küme zaten gelecek bilgisiyle
   seçildiyse, tüm bu testler YANLI örneği doğrular. Lookahead istatistiksel bir sorun
   değil, VERİ KURGUSU hatasıdır — istatistikle yakalanmaz.

📊 MEKANİZMA KANITI (sentetik, hiç edge OLMAYAN saf rastgele fiyatta):
   filtresiz → beklenti −0.094 / PF 0.67  ·  aynı lookahead filtresiyle → +0.071 / PF 1.34
   Yani bu filtre YOKTAN edge üretebiliyor.

BU MODÜL NE YAPAR (ANAYASA #8 GÜVENLİ — şampiyon koduna DOKUNMAZ, yalnız OKUR):
  Aynı günlerde aynı adayları İKİ tanımla üretir ve şampiyon bloklarıyla filtreler:
    A) MEVCUT  : absorp/reclaim ileri barlardan (bugünkü tanım)
    B) NEDENSEL: absorp/reclaim yalnız [0..j] barlarından (giriş anında BİLİNEBİLİR)
  Çıktı: her ikisi için n / WR / PF / beklenti / maxDD / OOS + serap bateri (DSR≥0.95).
  KARAR: B'de edge büyük ölçüde kayboluyorsa şampiyonun geçmiş başarısı büyük ölçüde
  gelecek-bilgisidir → canlıda çalışmaz (canlı bakiyelerin düşmesi de bununla uyumlu).

NEDENSEL TANIMLAR (doğal karşılıkları — geleceğin AYNASI, geçmişe bakan hali):
  absorp_nedensel  : vol_z≥1 (giriş barı) VE ÖNCEKİ 5 barda fiyat fade yönünde tutunmuş
                     (SHORT: son 5 barın max'ı girişi %0.1'den fazla aşmamış)
  reclaim_nedensel : ÖNCEKİ 15 barda seviye süpürülüp geri alınmış (sweep+reclaim GEÇMİŞTE)

KOMUT (PC — parquet orada):
  python oar_nedensel_denetim.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
"""
import argparse
import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone

SONUC_FILE = Path(__file__).resolve().parent / "nedensel_denetim_sonuc.json"


def _now():
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════════════
#  ADAY ÜRETİMİ — şampiyonun kendi boru hattı, ama absorp/reclaim İKİ tanımla
# ═══════════════════════════════════════════════════════════════════════════════
def adaylar_iki_tanimla(gunler: dict, eval_saat: int = 4, cvd_pencere: int = 15,
                        tol: float = 0.10, min_range: float = 1.0) -> list:
    """
    oar_local_backtest.aday_sinyaller_uret ile AYNI akış; tek fark: her adaya
    hem MEVCUT (ileri-bar) hem NEDENSEL (geçmiş-bar) absorp/reclaim iliştirilir.
    Şampiyon kodu DEĞİŞTİRİLMEZ — buradaki kopya yalnız ölçüm içindir.
    """
    import oar_local_backtest as olb

    adaylar = []
    for gun, g in (gunler or {}).items():
        try:
            fibs = g.get("fibs")
            poc = g.get("poc")
            if not fibs:
                continue
            genlik = g.get("genlik_pct")
            if genlik is not None and genlik < min_range:
                continue
            ts_list, close_list = g.get("post_ts"), g.get("post_close")
            if not ts_list or not close_list:
                continue
            delta_map = g.get("delta_map", {}) or {}
            vol_map = g.get("vol_map", {}) or {}
            vol_ort = g.get("vol_ort", 0.0) or 0.0
            vol_std = g.get("vol_std", 1.0) or 1.0
            balina_esik = g.get("delta_abs_esik", 0.0) or 0.0
            cvd_map = g.get("cvd_map", {}) or {}

            alinan = set()
            bd_run = 0.0
            bd_lvl = None
            for j, (ts, fiyat) in enumerate(zip(ts_list, close_list)):
                _d = olb._dk_deger(delta_map, ts)
                if abs(_d) > abs(bd_run):
                    bd_run = _d
                    bd_lvl = fiyat
                oran = olb.temas_eden_fib(fiyat, fibs, tol)
                if oran is None or oran in alinan:
                    continue
                alinan.add(oran)
                yon = olb.fib_yonu(oran)
                seviye = fibs[oran]
                vol = olb._dk_deger(vol_map, ts)
                vol_z = (vol - vol_ort) / vol_std if vol_std else 0.0

                # ── A) MEVCUT TANIM (ileri barlar = LOOKAHEAD)
                ilk5 = close_list[j + 1: j + 6] + [fiyat]
                ileri15 = close_list[j + 1: j + 16]
                # ── B) NEDENSEL TANIM (yalnız [0..j] — giriş anında bilinebilir)
                gec5 = close_list[max(0, j - 5): j] + [fiyat]
                gec15 = close_list[max(0, j - 15): j]

                if yon == "SHORT":
                    absorp_a = (vol_z >= 1.0) and (max(ilk5) <= fiyat * 1.001)
                    absorp_b = (vol_z >= 1.0) and (max(gec5) <= fiyat * 1.001)
                    reclaim_a = any(c < seviye * 0.997 for c in ileri15)
                    reclaim_b = (any(c > seviye * 1.003 for c in gec15)
                                 and fiyat < seviye * 1.001)     # süpürüldü→geri alındı (geçmiş)
                else:
                    absorp_a = (vol_z >= 1.0) and (min(ilk5) >= fiyat * 0.999)
                    absorp_b = (vol_z >= 1.0) and (min(gec5) >= fiyat * 0.999)
                    reclaim_a = any(c > seviye * 1.003 for c in ileri15)
                    reclaim_b = (any(c < seviye * 0.997 for c in gec15)
                                 and fiyat > seviye * 0.999)

                ileri = close_list[j + 1: j + 1 + eval_saat * 60]
                tp, sl = olb.tp_sl_seviyeleri(oran, fiyat, fibs)
                out, net = olb.degerlendir_tpsl(fiyat, yon, tp, sl, ileri)

                kayit = {
                    "gun": gun, "ts": int(ts), "yon": yon, "fib": oran, "fiyat": fiyat,
                    "poc": poc, "vol_z": round(vol_z, 3), "sonuc": out, "net": net,
                    # temiz bloklar (ikisinde de aynı)
                    "gun_bias_uyum": g.get("gun_bias_uyum"),
                    # iki tanım
                    "absorp_a": bool(absorp_a), "absorp_b": bool(absorp_b),
                    "reclaim_a": bool(reclaim_a), "reclaim_b": bool(reclaim_b),
                }
                # footprint_kalicilik (TEMİZ — yalnız [0..j])
                if bd_lvl is not None and balina_esik > 0 and abs(bd_run) >= balina_esik:
                    kayit["kalicilik"] = bool(fiyat <= bd_lvl * 1.001) if yon == "SHORT" \
                        else bool(fiyat >= bd_lvl * 0.999)
                else:
                    kayit["kalicilik"] = None
                adaylar.append(kayit)
        except Exception:
            continue
    return adaylar


# ═══════════════════════════════════════════════════════════════════════════════
#  ŞAMPİYON FİLTRESİ (iki tanımla) + metrik
# ═══════════════════════════════════════════════════════════════════════════════
def _poc_taraf(k) -> bool:
    poc, f = k.get("poc"), k.get("fiyat")
    if not poc or not f:
        return True
    return f >= poc if k.get("yon") == "SHORT" else f <= poc


def sampiyon_filtre(adaylar: list, stil: str, tanim: str) -> list:
    """stil: fade|trend · tanim: a (mevcut/lookahead) | b (nedensel)."""
    out = []
    for k in adaylar:
        if not _poc_taraf(k):
            continue
        if stil == "fade":
            if not k.get(f"absorp_{tanim}"):
                continue
            if k.get("kalicilik") is not True:
                continue
        else:
            if not k.get(f"reclaim_{tanim}"):
                continue
            if k.get("gun_bias_uyum") is not True:
                continue
        out.append(k)
    return out


def metrik(islemler: list) -> dict:
    n = len(islemler)
    if not n:
        return {"n": 0}
    netler = [i.get("net", 0.0) for i in islemler]
    kaz = sum(x for x in netler if x > 0)
    kay = -sum(x for x in netler if x < 0)
    # kronolojik equity → maxDD
    sirali = sorted(islemler, key=lambda x: x.get("ts", 0))
    eq, tepe, dd = 0.0, 0.0, 0.0
    for i in sirali:
        eq += i.get("net", 0.0)
        tepe = max(tepe, eq)
        dd = min(dd, eq - tepe)
    kesme = int(n * 0.7)
    oos = [i.get("net", 0.0) for i in sirali[kesme:]]
    return {
        "n": n,
        "wr": round(sum(1 for x in netler if x > 0) / n * 100, 1),
        "pf": round(kaz / kay, 2) if kay > 0 else (99.0 if kaz > 0 else 0.0),
        "beklenti": round(sum(netler) / n, 4),
        "maxdd": round(dd, 2),
        "oos_beklenti": round(sum(oos) / len(oos), 4) if oos else 0.0,
        "oos_n": len(oos),
    }


def serap(islemler: list, n_deneme: int = 300) -> dict:
    """Serap bateri (varsa) — nedensel sürümde edge kalıyor mu."""
    try:
        from oar_serap_testi import serap_karnesi
        return serap_karnesi([i.get("net", 0.0) for i in islemler], n_deneme=n_deneme)
    except Exception:
        return {}


def rapor(sonuc: dict) -> str:
    L = ["🔬 *NEDENSEL DENETİM* — şampiyonun edge'i gerçek mi, gelecek-bilgisi mi?",
         f"Aday havuzu: {sonuc.get('aday_sayisi', 0)}", ""]
    L.append("A) MEVCUT tanım = absorp/reclaim SONRAKİ barlardan (giriş anında YOK)")
    L.append("B) NEDENSEL tanım = yalnız giriş anına kadarki barlar")
    L.append("")
    for stil in ("fade", "trend"):
        s = sonuc.get(stil, {})
        a, b = s.get("a", {}), s.get("b", {})
        ad = "FADE (ekstrem_donus_fade)" if stil == "fade" else "TREND (kirilim_devam_trend)"
        L.append(f"━━━ {ad} ━━━")
        for et, m in (("A mevcut ", a), ("B nedensel", b)):
            if not m.get("n"):
                L.append(f"  {et}: işlem yok")
                continue
            L.append(f"  {et}: n={m['n']:<5} WR%{m['wr']:<5} PF {m['pf']:<5} "
                     f"beklenti {m['beklenti']:+.4f}  maxDD {m['maxdd']:.1f}  "
                     f"OOS {m['oos_beklenti']:+.4f}")
        if a.get("n") and b.get("n"):
            dpf = b["pf"] - a["pf"]
            dbek = b["beklenti"] - a["beklenti"]
            L.append(f"  → NEDENSELE GEÇİNCE: PF {dpf:+.2f} · beklenti {dbek:+.4f} · "
                     f"işlem {b['n'] - a['n']:+d}")
            if b["pf"] < 1.0 or b["beklenti"] <= 0:
                L.append("  🔴 NEDENSEL SÜRÜMDE EDGE YOK → geçmiş başarı büyük ölçüde "
                         "GELECEK-BİLGİSİ. Bu şampiyon canlıda çalışmaz.")
            elif b["pf"] < a["pf"] * 0.6:
                L.append("  🟡 Edge'in BÜYÜK KISMI lookahead'den geliyor; kalan kısım "
                         "serap testinden geçmeli.")
            else:
                L.append("  🟢 Edge büyük ölçüde NEDENSEL — lookahead katkısı sınırlı.")
        sr = s.get("serap_b") or {}
        if sr:
            L.append(f"  nedensel serap: DSR {sr.get('deflated_sharpe', {}).get('dsr', '?')} · "
                     f"CI-alt {sr.get('bootstrap_beklenti_ci', {}).get('alt', '?')}")
        L.append("")
    L.append("_Not: lookahead İSTATİSTİKSEL bir kusur değil, veri kurgusu kusurudur — "
             "DSR/permütasyon/bootstrap/walk-forward yanlı kümeyi doğrular, yakalayamaz. "
             "Şampiyon koduna DOKUNULMADI; bu yalnız ÖLÇÜMDÜR (ANAYASA #8)._")
    return "\n".join(L)


def calistir(semboller, bas, bit, telegram=False):
    import oar_local_backtest as olb
    from oar_sampiyon_confirm import _klines_oku, _aggt_ay_yollari
    tum = []
    for sym in semboller:
        print(f"[nedensel] {sym}: klines + aggTrades hazırlanıyor (ay ay)…", flush=True)
        try:
            klines = _klines_oku(sym, bas, bit)
            yollar = _aggt_ay_yollari(sym, bas, bit)
            if klines is None or not yollar:
                print(f"[nedensel] ⚠ {sym}: veri yok (parquet yolu?) — atlandı", flush=True)
                continue
            try:
                metrics = olb._metrics_oku(sym, bas, bit)
            except Exception:
                metrics = None
            gunler = olb._gun_hazirla(klines, yollar, metrics)
        except Exception as e:
            print(f"[nedensel] ⚠ {sym} hazırlık hatası: {str(e)[:110]}", flush=True)
            continue
        n0 = len(tum)
        tum.extend(adaylar_iki_tanimla(gunler))
        print(f"[nedensel] {sym}: {len(tum)-n0} aday", flush=True)
    if not tum:
        print("[nedensel] aday üretilemedi (parquet/veri yolu?).", flush=True)
        return {}
    sonuc = {"uretim": _now(), "aday_sayisi": len(tum)}
    for stil in ("fade", "trend"):
        A = sampiyon_filtre(tum, stil, "a")
        B = sampiyon_filtre(tum, stil, "b")
        sonuc[stil] = {"a": metrik(A), "b": metrik(B), "serap_b": serap(B)}
    SONUC_FILE.write_text(json.dumps(sonuc, ensure_ascii=False, indent=2), encoding="utf-8")
    metin = rapor(sonuc)
    print(metin, flush=True)
    if telegram:
        try:
            from ajan_merkez import bildir
            asyncio.run(bildir("Nedensel Denetim", "backtest",
                               "Şampiyon lookahead denetimi", detay=metin[:500],
                               ham_metin=metin))
        except Exception as e:
            print(f"[nedensel] telegram: {e}", flush=True)
    return sonuc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--kendi-test", action="store_true",
                    help="parquet gerektirmez — lookahead mekanizmasını sentetik veriyle gösterir")
    a = ap.parse_args()
    if a.kendi_test:
        import random
        random.seed(11)
        def kos(n=4000, lookahead=True):
            kazanc = []
            for _ in range(n):
                yol = [0.0]
                for _i in range(60):
                    yol.append(yol[-1] + random.gauss(0, 0.12))
                giris = yol[0]
                if lookahead and max(yol[1:6]) > giris + 0.12:
                    continue
                tp, sl = giris - 1.2, giris + 0.6
                s = 0.0
                for p in yol[1:]:
                    if p <= tp:
                        s = 1.2; break
                    if p >= sl:
                        s = -0.6; break
                kazanc.append(s)
            k = sum(x for x in kazanc if x > 0); z = -sum(x for x in kazanc if x < 0)
            return len(kazanc), sum(kazanc) / len(kazanc), (k / z if z else 99)
        print("SAF RASTGELE fiyat (edge YOK) — lookahead filtresinin tek başına etkisi:")
        for la in (False, True):
            n, bek, pf = kos(lookahead=la)
            print(f"  {'lookahead FİLTRELİ' if la else 'filtresiz (dürüst)'}: "
                  f"n={n:<5} beklenti {bek:+.4f}  PF {pf:.2f}")
        print("\n→ Fark tamamen geleceğe bakmaktan geliyor; gerçek edge YOK.")
    else:
        calistir([s.strip() for s in a.symbol.split(",") if s.strip()], a.bas, a.bit, a.telegram)
