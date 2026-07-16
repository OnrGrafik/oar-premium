"""
oar_serap_testi.py — SERAP (mirage) TESTİ: edge gerçek mi, şans mı?
═══════════════════════════════════════════════════════════════════════════════════════
AMAÇ (kullanıcı): "Şampiyonlarda edge ve likidasyon ölçümü var mı; diğer backtestlerde
serap görmeyelim." → TÜM sistemleri (2 şampiyon + elenen adaylar) TEK komutla, aynı
bilimsel bateriden geçir; sonucu KALICI kaydet ki TEKRAR backtest gerekmesin.

BATERİ (her sistemin realize net-% işlem serisine uygulanır — hepsi kanıt-temelli):
  1) Yıllıklandırılmış Sharpe  — işlem FREKANSINA göre doğru annualizasyon (√252 SABİTİ DEĞİL).
  2) Deflated Sharpe (DSR)     — Bailey & López de Prado: N deneme içinde şansla çıkan
                                 en iyi Sharpe'ı DÜŞER. DSR = P(gerçek SR>0 | N deneme).
                                 DSR≥0.95 → çoklu-karşılaştırmayı GEÇER (serap değil).
  3) Permütasyon p-değeri      — işaret-çevirme (sign-flip): "yön edge'i şansla mı?" p<0.05.
  4) Bootstrap beklenti GA     — işlem-başına net %'nin %95 güven aralığı; alt sınır>0 →
                                 beklenti sağlam pozitif (tek ortalama değil, dağılım).
  5) Monte-Carlo equity        — işlem SIRASINI B kez yeniden örnekle; 1x/3x/5x'te
                                 likidasyon oranı + maxDD dağılımı → "hayatta kalma
                                 şanslı sıralamaya mı bağlıydı?" (kanar/likidasyon testi).
  6) Benjamini-Hochberg FDR    — TÜM sistemlerin p'lerine çoklu-test düzeltmesi (q=0.05):
                                 kaç deneme yaptıysak ona göre hangi sistem AYAKTA kalır.

KARAR (per sistem):
  ✅ GERÇEK EDGE : DSR≥0.95 ∧ bootstrap alt sınırı>0 ∧ FDR-sonrası p anlamlı ∧ 5x'te likidasyon YOK
  ⚠ MARJİNAL    : p anlamlı ama DSR<0.95 ya da CI alt≤0 (zayıf/şüpheli)
  ❌ SERAP       : p anlamsız ya da beklenti CI alt sınırı≤0 (şans/overfit)

KALICI ÇIKTI: serap_testi_sonuc.json (repo kökü, git-senkron) → bir kez koş, push et,
lider+ben okur; bir daha backtest GEREKMEZ. Ham işlem serileri .serap_cache/ altına
pickle'lanır → tekrar koşarsan ağır aggTrades'i YENİDEN İŞLEMEZ (kaldığı yerden).

Çalıştırma:
  python oar_serap_testi.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
  (birkaç saat sürebilir — şampiyon aggTrades'i ay ay işler; ilerleme yazılır)
  Tekrar çalıştırma cache'ten okur → dakikalar. --taze ile cache'i yok say.
"""
import argparse
import json
import math
import os
import pickle
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from statistics import NormalDist

_N = NormalDist()
EULER = 0.5772156649015329        # Euler-Mascheroni sabiti (DSR beklenen-maks için)
SONUC_FILE = Path(__file__).resolve().parent / "serap_testi_sonuc.json"
CACHE_DIR = Path(__file__).resolve().parent / ".serap_cache"


# ═══════════════════════════════════════════════════════════════════════════════
#  İSTATİSTİK BATERİSİ (saf — numpy + NormalDist; test edilebilir, yeniden kullanılır)
# ═══════════════════════════════════════════════════════════════════════════════
def _temel(pcts: np.ndarray) -> dict:
    """n, WR, profit factor, beklenti, toplam net, compound 1x maxDD."""
    n = len(pcts)
    if n == 0:
        return {"n": 0}
    kaz = pcts[pcts > 0]; kay = pcts[pcts <= 0]
    tk, tky = float(kaz.sum()), float(kay.sum())
    pf = (tk / abs(tky)) if tky < 0 else float("inf")
    # compound 1x maxDD (çarpımsal — equity sim ile TUTARLI tek tanım)
    eq = 1.0; tepe = 1.0; maxdd = 0.0
    for p in pcts:
        eq *= (1.0 + p / 100.0)
        tepe = max(tepe, eq)
        maxdd = max(maxdd, (tepe - eq) / tepe if tepe > 0 else 0.0)
    return {
        "n": n,
        "wr": round(100.0 * len(kaz) / n, 1),
        "beklenti": round(float(pcts.mean()), 4),
        "toplam_net": round(float(pcts.sum()), 1),
        "pf": round(pf, 2) if pf != float("inf") else None,
        "maxdd_1x_pct": round(maxdd * 100, 1),
    }


def _sharpe_yillik(pcts: np.ndarray, ts: np.ndarray) -> dict:
    """İşlem-başı Sharpe + FREKANSA göre yıllıklandırma (√252 sabiti DEĞİL)."""
    n = len(pcts)
    if n < 3:
        return {"sharpe_islem": None, "sharpe_yillik": None, "islem_yil": None}
    mu = float(pcts.mean()); sd = float(pcts.std(ddof=1))
    sr_islem = mu / sd if sd > 0 else 0.0
    yil = (ts.max() - ts.min()) / (365.25 * 86400 * 1000)   # ts ms
    islem_yil = n / yil if yil > 0 else 0.0
    return {
        "sharpe_islem": round(sr_islem, 4),
        "sharpe_yillik": round(sr_islem * math.sqrt(islem_yil), 3) if islem_yil > 0 else None,
        "islem_yil": round(islem_yil, 1),
    }


def _deflated_sharpe(pcts: np.ndarray, n_deneme: int) -> dict:
    """
    Deflated Sharpe Ratio (Bailey & López de Prado 2014). İşlem-başı SR'yi,
    skew/kurtosis ve N DENEME sayısına göre düzeltir. DSR = P(gerçek SR>0 | N deneme).
    N deneme = bu sistemi bulmak için denenen konfigürasyon sayısı (çoklu-karşılaştırma
    girişi). DSR≥0.95 → şansla çıkma ihtimali düşük.
    """
    n = len(pcts)
    if n < 10:
        return {"dsr": None, "not": "n<10 — DSR güvenilir değil"}
    mu = float(pcts.mean()); sd = float(pcts.std(ddof=1))
    if sd <= 0:
        return {"dsr": None, "not": "std=0"}
    sr = mu / sd
    z = (pcts - mu) / sd
    skew = float((z ** 3).mean())
    kurt = float((z ** 4).mean())          # Pearson (normal=3)
    # SR tahmincisinin standart sapması (Lo 2002 / Bailey)
    var_sr = (1 - skew * sr + ((kurt - 1) / 4.0) * sr * sr) / (n - 1)
    if var_sr <= 0:
        return {"dsr": None, "not": "var_sr≤0"}
    sigma_sr = math.sqrt(var_sr)
    # N deneme altında GÜRÜLTÜDEN beklenen en iyi SR (sıfır-ortalama null)
    N = max(int(n_deneme), 2)
    e_max = sigma_sr * ((1 - EULER) * _N.inv_cdf(1 - 1.0 / N)
                        + EULER * _N.inv_cdf(1 - 1.0 / (N * math.e)))
    dsr = _N.cdf((sr - e_max) / sigma_sr)
    return {"dsr": round(dsr, 4), "sr_islem": round(sr, 4),
            "beklenen_maks_sr_gurultu": round(e_max, 4),
            "skew": round(skew, 3), "kurtosis": round(kurt, 3), "n_deneme": N}


def _permutasyon_p(pcts: np.ndarray, B: int = 20000, tohum: int = 42) -> float:
    """
    İşaret-çevirme permütasyon testi: H0 = işlemlerin ortalaması 0 (simetrik null).
    Her işlemin işaretini rastgele çevir, toplamı ölç. p = gözlenen toplamdan
    ≥ olan permütasyon oranı. Yön edge'i şansla mı, gerçek mi?
    """
    n = len(pcts)
    if n < 3:
        return 1.0
    rng = np.random.default_rng(tohum)
    gozlenen = float(pcts.sum())
    isaret = rng.choice(np.array([-1.0, 1.0]), size=(B, n))
    perm_toplam = (isaret * pcts).sum(axis=1)
    # tek yönlü (edge pozitif mi): +1 düzeltmesi (Phipson-Smyth)
    return float((np.sum(perm_toplam >= gozlenen) + 1) / (B + 1))


def _bootstrap_ci(pcts: np.ndarray, B: int = 20000, tohum: int = 7) -> dict:
    """Beklentinin (işlem-başı net %) bootstrap %95 güven aralığı."""
    n = len(pcts)
    if n < 5:
        return {"alt": None, "ust": None}
    rng = np.random.default_rng(tohum)
    idx = rng.integers(0, n, size=(B, n))
    ort = pcts[idx].mean(axis=1)
    return {"alt": round(float(np.percentile(ort, 2.5)), 4),
            "ust": round(float(np.percentile(ort, 97.5)), 4)}


def _mc_equity(pcts: np.ndarray, kaldirac: float, B: int = 5000, tohum: int = 13) -> dict:
    """
    Monte-Carlo: işlem SIRASINI yeniden örnekle (bootstrap) → 1000$ compound equity.
    Tek işlemde ≤ −(100/kaldıraç)% → LİKİDASYON (equity sıfırlanır). Döner: likidasyon
    oranı + maxDD dağılımı → hayatta kalma şanslı sıralamaya mı bağlıydı?
    """
    n = len(pcts)
    if n < 5:
        return {"likidasyon_orani": None}
    rng = np.random.default_rng(tohum)
    esik = -100.0 / kaldirac
    lik = 0; maxdds = []; finaller = []
    for _ in range(B):
        seri = pcts[rng.integers(0, n, size=n)]        # sıra + kompozisyon örneklemesi
        eq = 1000.0; tepe = 1000.0; mdd = 0.0; battı = False
        for p in seri:
            if p <= esik:
                eq = 0.0; battı = True; break
            eq *= (1.0 + kaldirac * p / 100.0)
            tepe = max(tepe, eq)
            mdd = max(mdd, (tepe - eq) / tepe if tepe > 0 else 0.0)
        if battı:
            lik += 1
        maxdds.append(mdd * 100); finaller.append(eq)
    return {
        "likidasyon_orani": round(100.0 * lik / B, 2),
        "maxdd_medyan_pct": round(float(np.median(maxdds)), 1),
        "maxdd_p95_pct": round(float(np.percentile(maxdds, 95)), 1),
        "final_medyan": round(float(np.median(finaller)), 2),
        "final_p05": round(float(np.percentile(finaller, 5)), 2),
    }


def _bh_fdr(ad_p: list, q: float = 0.05) -> dict:
    """Benjamini-Hochberg FDR: [(ad,p),...] → {ad: geçti_mi}. Çoklu-test düzeltmesi."""
    m = len(ad_p)
    if m == 0:
        return {}
    sirali = sorted(ad_p, key=lambda x: x[1])
    esik_k = 0
    for k, (_, p) in enumerate(sirali, start=1):
        if p <= q * k / m:
            esik_k = k
    gecen = {sirali[i][0] for i in range(esik_k)}
    return {ad: (ad in gecen) for ad, _ in ad_p}


def serap_karnesi(ad: str, pcts_list: list, ts_list: list, n_deneme: int) -> dict:
    """Bir sistemin realize işlem serisine TÜM bateriyi uygula → tam karne."""
    pcts = np.asarray(pcts_list, dtype=float)
    ts = np.asarray(ts_list, dtype=float)
    kart = {"ad": ad, "temel": _temel(pcts), **_sharpe_yillik(pcts, ts)}
    kart["deflated_sharpe"] = _deflated_sharpe(pcts, n_deneme)
    kart["permutasyon_p"] = round(_permutasyon_p(pcts), 5)
    kart["bootstrap_beklenti_ci"] = _bootstrap_ci(pcts)
    kart["mc_equity"] = {f"{k}x": _mc_equity(pcts, k) for k in (1, 3, 5)}
    return kart


def _karar(kart: dict, fdr_gecti: bool) -> str:
    dsr = (kart.get("deflated_sharpe") or {}).get("dsr")
    ci_alt = (kart.get("bootstrap_beklenti_ci") or {}).get("alt")
    p = kart.get("permutasyon_p", 1.0)
    lik5 = ((kart.get("mc_equity") or {}).get("5x") or {}).get("likidasyon_orani")
    if dsr is None or ci_alt is None:
        return "❓ YETERSİZ VERİ (n küçük)"
    gercek = (dsr >= 0.95 and ci_alt > 0 and p < 0.05 and fdr_gecti and (lik5 == 0))
    if gercek:
        return "✅ GERÇEK EDGE"
    if p < 0.05 and ci_alt > 0:
        return "⚠ MARJİNAL (anlamlı ama DSR/likidasyon zayıf)"
    return "❌ SERAP (şans/overfit — beklenti güvenilmez)"


# ═══════════════════════════════════════════════════════════════════════════════
#  SİSTEM TOPLAYICILAR (mevcut GERÇEK makineyi çağırır — proxy değil; cache'lenir)
# ═══════════════════════════════════════════════════════════════════════════════
def _cache_yaz(anahtar: str, veri):
    CACHE_DIR.mkdir(exist_ok=True)
    with open(CACHE_DIR / f"{anahtar}.pkl", "wb") as f:
        pickle.dump(veri, f)


def _cache_oku(anahtar: str):
    yol = CACHE_DIR / f"{anahtar}.pkl"
    if yol.exists():
        try:
            with open(yol, "rb") as f:
                return pickle.load(f)
        except Exception:
            return None
    return None


def _sistemleri_topla(semboller, bas, bit, taze=False) -> dict:
    """
    Her sistemin realize (ts, net%) serisini üret. Şampiyonlar aggTrades (ağır);
    elenen adaylar klines (hızlı). n_deneme = o sistemi bulmak için denenen konfig
    sayısı (DSR çoklu-karşılaştırma girişi — serabı elemenin ANAHTARI).
    """
    sistemler = {}   # ad -> {"seri":[(ts,pct)...], "n_deneme":int}

    # ── ŞAMPİYONLAR (2) — aggTrades havuzu bir kez, iki blok kümesiyle _filtre ──
    ck = "sampiyon_havuz_" + "_".join(semboller) + f"_{bas}_{bit}"
    havuz = None if taze else _cache_oku(ck)
    if havuz is None:
        try:
            from oar_sampiyon_confirm import _analiz as _sampiyon_analiz
            havuz = []
            for s in semboller:
                adaylar, _cset = _sampiyon_analiz(s, bas, bit)
                havuz.extend(adaylar)
            _cache_yaz(ck, havuz)
        except Exception as e:
            print(f"[Serap] şampiyon havuz hatası: {e}", flush=True)
            havuz = []
    if havuz:
        from oar_kesif import _filtre
        pf = json.loads((Path(__file__).resolve().parent / "oar_sampiyon_portfoy.json")
                        .read_text(encoding="utf-8"))
        for st in pf.get("stiller", []):
            trades = _filtre(havuz, st["bloklar"])
            seri = [(c["ts"], c["pct"]) for c in trades]
            # n_deneme = kesfet'in taradığı blok-kombinasyon sayısı (aday_n değil).
            # Konservatif: blok kütüphanesinden üretilen kombinasyon adedi ≈ birkaç yüz.
            sistemler[f"ŞAMPİYON:{st['stil']}"] = {"seri": seri, "n_deneme": 300}

    # ── ELENEN ADAYLAR (klines — hızlı; her biri kendi _analiz'iyle) ──
    for s in semboller:
        # stil-3 / stil-4
        ck2 = f"stil_{s}_{bas}_{bit}"
        st = None if taze else _cache_oku(ck2)
        if st is None:
            try:
                from oar_stil_backtest import _analiz as _stil_analiz
                st = _stil_analiz(s, bas, bit); _cache_yaz(ck2, st)
            except Exception as e:
                print(f"[Serap] stil {s} hata: {e}", flush=True); st = {"stil3": [], "stil4": []}
        sistemler.setdefault("STİL-3 (1.377 dönüş)", {"seri": [], "n_deneme": 4})["seri"].extend(st.get("stil3", []))
        sistemler.setdefault("STİL-4 (EQ kalıcılık)", {"seri": [], "n_deneme": 4})["seri"].extend(st.get("stil4", []))

        # micro-scalp (seviye bazında)
        ck3 = f"micro_{s}_{bas}_{bit}"
        mc = None if taze else _cache_oku(ck3)
        if mc is None:
            try:
                from oar_micro_scalp import _analiz as _micro_analiz
                mc = _micro_analiz(s, bas, bit); _cache_yaz(ck3, mc)
            except Exception as e:
                print(f"[Serap] micro {s} hata: {e}", flush=True); mc = {}
            # mc: {seviye: [(ts,net)...]} veya liste — modüle göre normalize et
        if isinstance(mc, dict):
            for sev, lst in mc.items():
                ad = f"MICRO fib {sev}"
                sistemler.setdefault(ad, {"seri": [], "n_deneme": 12})["seri"].extend(lst)
        elif isinstance(mc, list):
            sistemler.setdefault("MICRO-SCALP (tümü)", {"seri": [], "n_deneme": 12})["seri"].extend(mc)

    # fib paslaşma (koridor bazında) — sembol-üstü keşif; ayrı topla
    for s in semboller:
        ck4 = f"paslasma_{s}_{bas}_{bit}"
        ps = None if taze else _cache_oku(ck4)
        if ps is None:
            try:
                import oar_fib_paslasma as fp
                gunler = fp._gunler(s, bas, bit)
                koridorlar = fp._koridor_kesif(gunler)   # [((i,j), sayi), ...]
                ps = {}
                for kor, _sayi in koridorlar[:3]:
                    i, j = kor
                    kayit = fp._koridor_scalp(gunler, i, j)   # {'hacimli':[(ts,net)],'hacimsiz':[...]}
                    ps[f"{i}<->{j}"] = kayit
                _cache_yaz(ck4, ps)
            except Exception as e:
                print(f"[Serap] paslaşma {s} hata: {e}", flush=True); ps = {}
        if isinstance(ps, dict):
            for kor, kayit in ps.items():
                seri = _paslasma_seri(kayit)
                if seri:
                    sistemler.setdefault(f"PASLAŞMA {kor}", {"seri": [], "n_deneme": 6})["seri"].extend(seri)

    return sistemler


def _paslasma_seri(kayit):
    """Paslaşma modülü çıktısını (ts,pct) serisine indir (şekli ne olursa olsun)."""
    if isinstance(kayit, dict):
        # hacimli listesi tercih (kanıt bu koldaydı); yoksa tüm listeleri birleştir
        for k in ("hacimli", "islemler", "trades"):
            if isinstance(kayit.get(k), list) and kayit[k]:
                return [(t[0], t[1]) if isinstance(t, (list, tuple)) else (0, t) for t in kayit[k]]
        birles = []
        for v in kayit.values():
            if isinstance(v, list):
                birles += [(t[0], t[1]) if isinstance(t, (list, tuple)) else (0, t) for t in v]
        return birles
    if isinstance(kayit, list):
        return [(t[0], t[1]) if isinstance(t, (list, tuple)) else (0, t) for t in kayit]
    return []


# ═══════════════════════════════════════════════════════════════════════════════
#  ANA
# ═══════════════════════════════════════════════════════════════════════════════
def calistir(semboller, bas, bit, taze=False, telegram=False) -> dict:
    print("═══ SERAP TESTİ — tüm sistemler tek bateriden ═══", flush=True)
    sistemler = _sistemleri_topla(semboller, bas, bit, taze=taze)

    karneler = {}
    for ad, veri in sistemler.items():
        seri = veri["seri"]
        if len(seri) < 5:
            print(f"  · {ad}: n={len(seri)} — atlandı (yetersiz)", flush=True)
            continue
        pcts = [p for _, p in seri]; ts = [t for t, _ in seri]
        print(f"  · {ad}: n={len(seri)} — bateri uygulanıyor…", flush=True)
        karneler[ad] = serap_karnesi(ad, pcts, ts, veri["n_deneme"])

    # Çoklu-test düzeltmesi: TÜM sistemlerin permütasyon p'lerine BH-FDR
    ad_p = [(ad, k["permutasyon_p"]) for ad, k in karneler.items()]
    fdr = _bh_fdr(ad_p, q=0.05)
    for ad, k in karneler.items():
        k["fdr_gecti"] = fdr.get(ad, False)
        k["karar"] = _karar(k, k["fdr_gecti"])

    sonuc = {
        "tarih": datetime.now(timezone.utc).isoformat(),
        "aralik": f"{bas}..{bit}", "semboller": semboller,
        "sistem_sayisi": len(karneler),
        "aciklama": ("Her sistem realize net-% serisine: yıllık Sharpe + Deflated Sharpe "
                     "(N-deneme düzeltmeli) + permütasyon p + bootstrap beklenti CI + "
                     "Monte-Carlo likidasyon; TÜM p'lere BH-FDR (q=0.05)."),
        "karneler": karneler,
    }
    SONUC_FILE.write_text(json.dumps(sonuc, ensure_ascii=False, indent=2), encoding="utf-8")
    _yazdir(karneler)
    print(f"\n[Serap] KALICI kayıt → {SONUC_FILE.name} (commit+push et → lider+ben okur, "
          "tekrar backtest GEREKMEZ)", flush=True)

    if telegram:
        _telegram_raporla(karneler, f"{bas}..{bit}")
    return sonuc


def _yazdir(karneler: dict):
    print("\n" + "═" * 100)
    print(f"{'SİSTEM':<30}{'n':>6}{'WR':>6}{'PF':>6}{'bekl':>8}{'SR-yıl':>8}{'DSR':>7}{'perm-p':>8}{'CI-alt':>8}{'liq5x':>7}  KARAR")
    print("─" * 100)
    for ad, k in sorted(karneler.items(), key=lambda x: -((x[1].get('deflated_sharpe') or {}).get('dsr') or 0)):
        t = k["temel"]; ds = k.get("deflated_sharpe") or {}; ci = k.get("bootstrap_beklenti_ci") or {}
        liq = ((k.get("mc_equity") or {}).get("5x") or {}).get("likidasyon_orani")
        print(f"{ad:<30}{t.get('n',0):>6}{t.get('wr',0):>6}"
              f"{(t.get('pf') if t.get('pf') is not None else 0):>6}"
              f"{t.get('beklenti',0):>8}{(k.get('sharpe_yillik') or 0):>8}"
              f"{(ds.get('dsr') if ds.get('dsr') is not None else 0):>7}"
              f"{k.get('permutasyon_p',1):>8}{(ci.get('alt') if ci.get('alt') is not None else 0):>8}"
              f"{(liq if liq is not None else -1):>7}  {k.get('karar','')}")
    print("═" * 100)
    print("DSR≥0.95 + CI-alt>0 + perm-p<0.05 (FDR) + liq5x=0 → GERÇEK EDGE. Aksi serap/marjinal.")


def _telegram_raporla(karneler: dict, aralik: str):
    try:
        import asyncio
        from ajan_merkez import bildir
        gercek = [ad for ad, k in karneler.items() if k["karar"].startswith("✅")]
        serap = [ad for ad, k in karneler.items() if k["karar"].startswith("❌")]
        ozet = (f"Serap testi ({aralik}): {len(karneler)} sistem. "
                f"GERÇEK EDGE: {', '.join(gercek) or 'yok'}. SERAP: {len(serap)} sistem elendi.")
        detay = "DSR+permütasyon+bootstrap+MC-likidasyon+BH-FDR ile doğrulandı. Kalıcı: serap_testi_sonuc.json"
        asyncio.run(bildir("Serap Testi", "backtest", ozet, detay=detay))
    except Exception as e:
        print(f"[Serap] telegram hata: {e}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--taze", action="store_true", help="cache'i yok say, sıfırdan üret")
    ap.add_argument("--telegram", action="store_true")
    a = ap.parse_args()
    calistir([s.strip() for s in a.symbol.split(",") if s.strip()],
             a.bas, a.bit, taze=a.taze, telegram=a.telegram)
