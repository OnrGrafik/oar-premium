"""
oar_trade_analiz.py — PROFESYONEL İŞLEM ANALİZİ (şampiyonlardan daha fazla bilgi çıkar)
═══════════════════════════════════════════════════════════════════════════════════════
Dış değerlendirmenin (prop-trader gözü) DEĞERLİ + DÜŞÜK-SERAP-RİSKLİ önerilerini uygular.
FELSEFE: yeni feature EKLEMEZ (sabit veride overfit fabrikası — DSR bunu cezalandırır);
mevcut ŞAMPİYON işlemlerinden DAHA FAZLA BİLGİ ÇIKARIR. Şampiyon koduna DOKUNMAZ (ANAYASA #8).

5 katman (her şampiyon için — fade + trend):
  1) MAE/MFE  — her işlemin en-kötü/en-iyi gidişi (R cinsinden). "Stop fazla mı dar,
                TP erken mi?" (değerlendirmenin en kritik dediği #6).
  2) TAM METRİK — PF, beklenti, WR, ort/medyan tutma süresi, Recovery, Ulcer, MAR,
                  Kelly%, SQN, Payoff, Edge Ratio (#5).
  3) EXIT OPT.  — AYNI giriş, N farklı çıkış (base/1R/2R/3R/trailing/time/VWAP/BE/mid) →
                  her biri SERAP-TESTLİ (DSR≥0.95 aranır). Edge çoğu zaman çıkıştadır (#7).
  4) CYCLE WF   — dönem-dönem (2019/covid/2021-bull/2022-bear/2023/ETF-2024) WR+PF+beklenti;
                  edge her rejimde tutuyor mu (#9).
  5) FALSIFICATION — her işleme invalidasyon puanı (işleme KARŞI kanıt sayısı); yüksek-
                  invalidasyon işlemleri daha mı çok kaybediyor → filtre adayı (#11).

Şampiyon işlem serisi: confirm._analiz (aggTrades — .serap_cache'ten okunur, YENİDEN
işlemez) → portföy bloklarıyla _filtre. Forward fiyat yolu klines'tan (hızlı) yeniden
yürünür (MAE/MFE + exit varyantları için). No-lookahead GEREKMEZ: bu POST-TRADE analiz.

Çalıştırma:  python oar_trade_analiz.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
KALICI çıktı: trade_analiz_sonuc.json (repo kökü, git-senkron).
"""
import argparse
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from oar_local_backtest import (_klines_oku, _ms_olcekle, fib_seviyeleri,
                                tp_sl_seviyeleri, tp_sl_breakout,
                                GUN_MS, SAAT_MS, ASIA_BIT_UTC, NY_CLOSE_UTC,
                                FEE_PCT, SLIP_PCT)

SONUC_FILE = Path(__file__).resolve().parent / "trade_analiz_sonuc.json"


# ═══════════════════════════════════════════════════════════════════════════════
#  GÜN VERİSİ (klines — hızlı; forward fiyat yolu + fibs)
# ═══════════════════════════════════════════════════════════════════════════════
def _gun_verisi(sembol, bas, bit):
    """Her gün: Asia H/L → fibs + post-bar (ts, close, high, low, vol). MAE/MFE/exit için."""
    k = _klines_oku(sembol, bas, bit)
    if k is None or not len(k):
        return {}
    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    OT_MIN, OT_MAX = 1_400_000_000_000, 2_000_000_000_000
    k = k[(k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)]
    k["gun"] = (k["open_time"] // GUN_MS).astype("int64")
    k["saat"] = (k["open_time"] % GUN_MS) / SAAT_MS
    gunler = {}
    for g, gk in k.groupby("gun"):
        asia = gk[gk["saat"] < ASIA_BIT_UTC]
        post = gk[(gk["saat"] >= ASIA_BIT_UTC) & (gk["saat"] < NY_CLOSE_UTC)].sort_values("open_time")
        if asia.empty or len(post) < 10:
            continue
        a_h = float(asia["high"].max()); a_l = float(asia["low"].min())
        gunler[int(g)] = {
            "fibs": fib_seviyeleri(a_l, a_h),
            "ts": post["open_time"].to_numpy(),
            "close": post["close"].to_numpy(dtype=float),
            "high": post["high"].to_numpy(dtype=float),
            "low": post["low"].to_numpy(dtype=float),
            "vol": post["volume"].to_numpy(dtype=float),
        }
    return gunler


# ═══════════════════════════════════════════════════════════════════════════════
#  TEK İŞLEMİN ANALİZİ: MAE/MFE + tüm exit varyantları (forward yol boyunca)
# ═══════════════════════════════════════════════════════════════════════════════
def _islem_analiz(giris, yon, tp, sl, seg_close, seg_high, seg_low, seg_vol):
    """
    Girişten itibaren forward bar yolu → base outcome + MAE/MFE (R) + tutma süresi +
    exit varyantları. R = |giriş − SL| (risk birimi). Maliyet fee+slip düşülür.
    """
    if len(seg_close) == 0:
        return None
    R = abs(giris - sl) or (giris * 0.005)
    yon_s = 1.0 if yon == "LONG" else -1.0
    net_pct = lambda cikis: round((yon_s * (cikis - giris) / giris * 100) - FEE_PCT - SLIP_PCT, 4)

    # ── BASE (şampiyon TP/SL, close-bazlı — degerlendir_tpsl ile aynı) ──
    base_cikis = seg_close[-1]; base_bar = len(seg_close) - 1
    for i, c in enumerate(seg_close):
        if yon == "SHORT":
            if c <= tp: base_cikis = tp; base_bar = i; break
            if c >= sl: base_cikis = sl; base_bar = i; break
        else:
            if c >= tp: base_cikis = tp; base_bar = i; break
            if c <= sl: base_cikis = sl; base_bar = i; break

    # ── MAE/MFE (base çıkışına kadar, R cinsinden) ──
    yol = seg_close[: base_bar + 1]
    if yon == "LONG":
        mae = (giris - float(np.min(seg_low[: base_bar + 1]))) / R      # en kötü aleyhte (R)
        mfe = (float(np.max(seg_high[: base_bar + 1])) - giris) / R     # en iyi lehte (R)
    else:
        mae = (float(np.max(seg_high[: base_bar + 1])) - giris) / R
        mfe = (giris - float(np.min(seg_low[: base_bar + 1]))) / R

    # ── EXIT VARYANTLARI (aynı giriş, farklı çıkış) ──
    exits = {"base": net_pct(base_cikis)}
    for isim, rR in (("TP_1R", 1.0), ("TP_2R", 2.0), ("TP_3R", 3.0)):
        hedef = giris + yon_s * rR * R
        cikis = seg_close[-1]
        for c in seg_close:
            vur_tp = (c >= hedef) if yon == "LONG" else (c <= hedef)
            vur_sl = (c <= sl) if yon == "LONG" else (c >= sl)
            if vur_tp: cikis = hedef; break
            if vur_sl: cikis = sl; break
        exits[isim] = net_pct(cikis)
    # time-stop (60 bar = 1 saat)
    ts_bar = min(60, len(seg_close) - 1)
    exits["time_60"] = net_pct(seg_close[ts_bar])
    # breakeven sonra 1R: 1R'ye ulaşınca stop girişe çekilir
    be = sl; cikis_be = seg_close[-1]
    for c in seg_close:
        if (yon == "LONG" and c >= giris + R) or (yon == "SHORT" and c <= giris - R):
            be = giris
        vur_sl = (c <= be) if yon == "LONG" else (c >= be)
        vur_tp = (c >= tp) if yon == "LONG" else (c <= tp)
        if vur_tp: cikis_be = tp; break
        if vur_sl: cikis_be = be; break
    exits["BE_1R"] = net_pct(cikis_be)
    # trailing %0.6
    trail_pct = 0.006; tepe = giris; cikis_tr = seg_close[-1]
    for c in seg_close:
        if yon == "LONG":
            tepe = max(tepe, c)
            if c <= tepe * (1 - trail_pct): cikis_tr = c; break
            if c <= sl: cikis_tr = sl; break
        else:
            tepe = min(tepe, c)
            if c >= tepe * (1 + trail_pct): cikis_tr = c; break
            if c >= sl: cikis_tr = sl; break
    exits["trail_0.6"] = net_pct(cikis_tr)
    # VWAP-çıkış: fiyat gün-içi VWAP'ı ters yönde keserse çık
    cv = cpv = 0.0; cikis_vw = seg_close[-1]
    for i, c in enumerate(seg_close):
        cv += seg_vol[i]; cpv += ((seg_high[i] + seg_low[i] + c) / 3) * seg_vol[i]
        vwap = cpv / cv if cv > 0 else c
        if (yon == "LONG" and c < vwap) or (yon == "SHORT" and c > vwap):
            if i >= 3: cikis_vw = c; break   # ilk barlarda tetikleme
        if (yon == "LONG" and c >= tp) or (yon == "SHORT" and c <= tp): cikis_vw = tp; break
        if (yon == "LONG" and c <= sl) or (yon == "SHORT" and c >= sl): cikis_vw = sl; break
    exits["VWAP_exit"] = net_pct(cikis_vw)

    return {"exits": exits, "mae_R": round(mae, 3), "mfe_R": round(mfe, 3),
            "hold_bar": base_bar + 1}


# ═══════════════════════════════════════════════════════════════════════════════
#  METRİK SETİ (prop-firma) — bir net-% serisine
# ═══════════════════════════════════════════════════════════════════════════════
def _metrik_seti(pcts, holds=None, maes=None, mfes=None):
    p = np.asarray(pcts, dtype=float); n = len(p)
    if n < 3:
        return {"n": n}
    kaz = p[p > 0]; kay = p[p <= 0]
    tk, tky = float(kaz.sum()), float(kay.sum())
    pf = (tk / abs(tky)) if tky < 0 else float("inf")
    mu = float(p.mean()); sd = float(p.std(ddof=1)) or 1e-9
    payoff = (float(kaz.mean()) / abs(float(kay.mean()))) if len(kay) and float(kay.mean()) != 0 else None
    wr = len(kaz) / n
    # Kelly: W − (1−W)/R
    kelly = (wr - (1 - wr) / payoff) if payoff else None
    # compound equity maxDD + Ulcer
    eq = 1.0; tepe = 1.0; maxdd = 0.0; kare = 0.0; m = 0
    for x in p:
        eq *= (1 + x / 100.0); tepe = max(tepe, eq)
        dd = (tepe - eq) / tepe if tepe > 0 else 0.0
        maxdd = max(maxdd, dd); kare += dd * dd; m += 1
    ulcer = math.sqrt(kare / m) * 100 if m else 0.0
    toplam = float(p.sum())
    return {
        "n": n, "wr": round(100 * wr, 1), "beklenti": round(mu, 4),
        "toplam_net": round(toplam, 1),
        "pf": round(pf, 2) if pf != float("inf") else None,
        "payoff": round(payoff, 2) if payoff else None,
        "kelly_pct": round(100 * kelly, 1) if kelly is not None else None,
        "sqn": round(math.sqrt(n) * mu / sd, 2),                 # System Quality Number
        "maxdd_pct": round(maxdd * 100, 1),
        "recovery_factor": round(toplam / (maxdd * 100), 2) if maxdd > 0 else None,
        "ulcer_index": round(ulcer, 2),
        "mar": round((toplam / n) / (maxdd * 100), 3) if maxdd > 0 else None,
        "ort_hold_bar": round(float(np.mean(holds)), 1) if holds else None,
        "medyan_hold_bar": round(float(np.median(holds)), 1) if holds else None,
        "ort_mae_R": round(float(np.mean(maes)), 3) if maes else None,
        "ort_mfe_R": round(float(np.mean(mfes)), 3) if mfes else None,
        "edge_ratio": round(float(np.mean(mfes)) / float(np.mean(maes)), 2) if (maes and mfes and np.mean(maes) > 0) else None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  FALSIFICATION: her işleme invalidasyon puanı (işleme KARŞI kanıt sayısı)
# ═══════════════════════════════════════════════════════════════════════════════
def _invalidasyon(c):
    """Adayın KENDİ feature'larından, işleme karşı kanıt say (mevcut alanlar; uydurma yok)."""
    yon = c.get("yon"); skor = 0
    cvd = c.get("cvd_delta", 0) or 0
    # CVD işleme ters
    if (yon == "SHORT" and cvd > 0) or (yon == "LONG" and cvd < 0): skor += 1
    # sürülen yöne fade (tehlike): SHORT ama seans yukarı sürülüyor / LONG ama aşağı
    if yon == "SHORT" and c.get("seans_absorp_up"): skor += 1
    if yon == "LONG" and c.get("seans_absorp_dn"): skor += 1
    # trend gününde fade (mean-reversion riski)
    if c.get("mod") == "fade" and c.get("range_rejimi") is False: skor += 1
    # POC yanlış tarafta
    poc, f = c.get("poc"), c.get("fiyat")
    if poc and f:
        if (yon == "SHORT" and f < poc) or (yon == "LONG" and f > poc): skor += 1
    # reclaim yok (tuzak dönüşü teyidi yok)
    if c.get("reclaim") is False: skor += 1
    return skor


# ═══════════════════════════════════════════════════════════════════════════════
#  CYCLE WALK-FORWARD: dönem-dönem performans
# ═══════════════════════════════════════════════════════════════════════════════
DONEMLER = [
    ("2019 pre-covid", "2019-01-01", "2020-02-29"),
    ("2020 covid+toparlanma", "2020-03-01", "2020-12-31"),
    ("2021 bull", "2021-01-01", "2021-12-31"),
    ("2022 bear", "2022-01-01", "2022-12-31"),
    ("2023 toparlanma", "2023-01-01", "2023-12-31"),
    ("2024+ ETF/halving", "2024-01-01", "2025-06-30"),
]


def _cycle_wf(kayitlar):
    out = []
    for ad, b, e in DONEMLER:
        bms = int(datetime.fromisoformat(b).replace(tzinfo=timezone.utc).timestamp() * 1000)
        ems = int(datetime.fromisoformat(e).replace(tzinfo=timezone.utc).timestamp() * 1000)
        seg = [k for k in kayitlar if bms <= k["ts"] < ems]
        if len(seg) < 5:
            out.append({"donem": ad, "n": len(seg), "not": "yetersiz"}); continue
        m = _metrik_seti([k["pct"] for k in seg])
        out.append({"donem": ad, "n": m["n"], "wr": m["wr"], "pf": m["pf"], "beklenti": m["beklenti"]})
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  ANA
# ═══════════════════════════════════════════════════════════════════════════════
def _sampiyon_trades(semboller, bas, bit, taze=False):
    """Şampiyon işlem kümesi (confirm havuzu .serap_cache'ten → portföy bloklarıyla _filtre)."""
    from oar_serap_testi import _cache_oku, _cache_yaz
    ck = "sampiyon_havuz_" + "_".join(semboller) + f"_{bas}_{bit}"
    havuz = None if taze else _cache_oku(ck)
    if havuz is None:
        from oar_sampiyon_confirm import _analiz
        havuz = []
        for s in semboller:
            adaylar, _ = _analiz(s, bas, bit)
            havuz.extend(adaylar)
        _cache_yaz(ck, havuz)
    from oar_kesif import _filtre
    pf = json.loads((Path(__file__).resolve().parent / "oar_sampiyon_portfoy.json").read_text(encoding="utf-8"))
    out = {}
    for st in pf.get("stiller", []):
        out[st["stil"]] = (_filtre(havuz, st["bloklar"]), st["bloklar"])
    return out


def calistir(semboller, bas, bit, taze=False, telegram=False):
    print("═══ İŞLEM ANALİZİ — MAE/MFE + metrik + exit + cycle + falsification ═══", flush=True)
    gun_veri = {s: _gun_verisi(s, bas, bit) for s in semboller}
    sistemler = _sampiyon_trades(semboller, bas, bit, taze=taze)

    sonuc = {"tarih": datetime.now(timezone.utc).isoformat(), "aralik": f"{bas}..{bit}", "sistemler": {}}
    for stil, (trades, bloklar) in sistemler.items():
        if len(trades) < 10:
            print(f"  · {stil}: n={len(trades)} — atlandı", flush=True); continue
        print(f"  · {stil} [{'+'.join(bloklar)}]: n={len(trades)} işlem analiz ediliyor…", flush=True)
        exit_seri = {}   # exit adı → [pct]
        holds = []; maes = []; mfes = []; base_pcts = []
        inval_kova = {}  # invalidasyon skoru → [pct]
        for c in trades:
            g = gun_veri.get(c.get("_sembol"), {}).get(c.get("_gun"))
            if not g:
                continue
            # giriş bar index (ts eşleşmesi)
            idx = int(np.searchsorted(g["ts"], c["ts"]))
            if idx >= len(g["close"]):
                continue
            giris = c["fiyat"]; yon = c["yon"]; oran = c["fib"]
            tp, sl = (tp_sl_breakout if c.get("mod") == "trend" else tp_sl_seviyeleri)(oran, giris, g["fibs"])
            r = _islem_analiz(giris, yon, tp, sl,
                              g["close"][idx + 1:], g["high"][idx + 1:], g["low"][idx + 1:], g["vol"][idx + 1:])
            if not r:
                continue
            for ad, v in r["exits"].items():
                exit_seri.setdefault(ad, []).append(v)
            holds.append(r["hold_bar"]); maes.append(r["mae_R"]); mfes.append(r["mfe_R"])
            base_pcts.append(r["exits"]["base"])
            inval_kova.setdefault(_invalidasyon(c), []).append(r["exits"]["base"])

        # 1+2) metrik seti (base)
        metrik = _metrik_seti(base_pcts, holds, maes, mfes)
        # 3) exit karşılaştırması + serap testi
        from oar_serap_testi import serap_karnesi
        exit_ozet = {}
        for ad, seri in exit_seri.items():
            m = _metrik_seti(seri)
            ts_list = [c["ts"] for c in trades][: len(seri)]
            k = serap_karnesi(f"{stil}:{ad}", seri, ts_list, 9)   # 9 exit denemesi
            exit_ozet[ad] = {"pf": m.get("pf"), "beklenti": m.get("beklenti"),
                             "toplam_net": m.get("toplam_net"), "sqn": m.get("sqn"),
                             "dsr": (k.get("deflated_sharpe") or {}).get("dsr"),
                             "perm_p": k.get("permutasyon_p")}
        # 4) cycle walk-forward
        cycle = _cycle_wf([{"ts": c["ts"], "pct": p} for c, p in zip(trades, base_pcts)])
        # 5) falsification: invalidasyon skoru → performans
        inval = {}
        for skor in sorted(inval_kova):
            seri = inval_kova[skor]
            m = _metrik_seti(seri)
            inval[str(skor)] = {"n": m.get("n"), "wr": m.get("wr"), "beklenti": m.get("beklenti"), "pf": m.get("pf")}

        sonuc["sistemler"][stil] = {"bloklar": bloklar, "metrik": metrik,
                                    "exit_karsilastirma": exit_ozet, "cycle_wf": cycle,
                                    "falsification": inval}
        _yazdir(stil, metrik, exit_ozet, cycle, inval)

    SONUC_FILE.write_text(json.dumps(sonuc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[Analiz] KALICI kayıt → {SONUC_FILE.name} (commit+push et → lider+ben okur)", flush=True)
    if telegram:
        _telegram(sonuc)
    return sonuc


def _yazdir(stil, m, exits, cycle, inval):
    print(f"\n{'─'*90}\n▸ {stil}", flush=True)
    print(f"  METRİK: PF {m.get('pf')} · beklenti {m.get('beklenti')} · WR%{m.get('wr')} · SQN {m.get('sqn')} · "
          f"Payoff {m.get('payoff')} · Kelly%{m.get('kelly_pct')} · Recovery {m.get('recovery_factor')} · "
          f"Ulcer {m.get('ulcer_index')} · MAR {m.get('mar')}", flush=True)
    print(f"  MAE/MFE: ort MAE {m.get('ort_mae_R')}R · ort MFE {m.get('ort_mfe_R')}R · Edge Ratio {m.get('edge_ratio')} · "
          f"ort tutma {m.get('ort_hold_bar')} bar (medyan {m.get('medyan_hold_bar')})", flush=True)
    print(f"  EXIT KARŞILAŞTIRMA (base + varyantlar; DSR≥0.95 gerçek):", flush=True)
    for ad, e in sorted(exits.items(), key=lambda x: -(x[1].get("toplam_net") or -1e9)):
        print(f"    {ad:<12} PF {str(e.get('pf')):<6} beklenti {str(e.get('beklenti')):<9} "
              f"toplam {str(e.get('toplam_net')):<9} SQN {str(e.get('sqn')):<6} DSR {e.get('dsr')} perm-p {e.get('perm_p')}", flush=True)
    print(f"  CYCLE WALK-FORWARD (edge her dönemde tutuyor mu):", flush=True)
    for c in cycle:
        if c.get("n", 0) >= 5:
            print(f"    {c['donem']:<24} n{c['n']:<4} WR%{c['wr']:<5} PF {c['pf']} beklenti {c['beklenti']}", flush=True)
        else:
            print(f"    {c['donem']:<24} n{c.get('n',0)} (yetersiz)", flush=True)
    print(f"  FALSIFICATION (invalidasyon skoru → performans; yüksek skor = daha kötü olmalı):", flush=True)
    for skor, v in inval.items():
        print(f"    invalidasyon={skor}: n{v['n']} WR%{v['wr']} beklenti {v['beklenti']} PF {v['pf']}", flush=True)


def _telegram(sonuc):
    try:
        import asyncio
        from ajan_merkez import bildir
        ad = ", ".join(sonuc["sistemler"].keys())
        ozet = f"İşlem analizi ({sonuc['aralik']}): {ad}. MAE/MFE + exit-opt + cycle-WF + falsification → trade_analiz_sonuc.json"
        asyncio.run(bildir("İşlem Analizi", "backtest", ozet,
                           detay="Prop-trader metrikleri; exit varyantları serap-testli. Şampiyona dokunulmadı (analiz)."))
    except Exception as e:
        print(f"[Analiz] telegram hata: {e}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--taze", action="store_true")
    ap.add_argument("--telegram", action="store_true")
    a = ap.parse_args()
    calistir([s.strip() for s in a.symbol.split(",") if s.strip()],
             a.bas, a.bit, taze=a.taze, telegram=a.telegram)
