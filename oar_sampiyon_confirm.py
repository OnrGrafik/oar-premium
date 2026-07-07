"""
oar_sampiyon_confirm.py — ŞAMPİYON (fade+htf_vpfr) + KIRILIM-DEVAM CONFIRM birleşik backtest
═══════════════════════════════════════════════════════════════════════════════════════
SORU (kullanıcı): Hipotez sonucu doğru olsa da, ŞAMPİYONU hipotezle BİRLEŞTİRİP backtest
etmeden değiştirmeyelim. Confirm de olsa SAĞLAM olmalı.

KRİTİK: Şampiyon = FADE (ekstremde tersine dönüş). Kazanan hipotez = KIRILIM-DEVAM (Asia-high
kırıp 1.618'e gider). Bunlar ZIT. Yani "birleştirme" toplama değil, FİLTRE:
  → Kırılım-devam günlerinde ÜST ekstremi fade'leme (kırılan tepeyi shortlayıp ezilme).

Bu modül ŞAMPİYONUN GERÇEK seçim makinesini kullanır (proxy DEĞİL): aday_sinyaller_uret
+ kesfet (en sağlam blok kombinasyonu) + _filtre. ANAYASA #8: şampiyon KODUNA DOKUNMAZ,
sadece okur/çağırır. (Önceki sürüm elle 'fade+htf_vpfr_ok' filtresi kuruyordu → gerçek
şampiyon değildi, PF 0.63 çıkmıştı; bu düzeltildi — artık kesfet'in bulduğu şampiyon.)

3 senaryo net PF/beklenti/maxDD/OOS ile karşılaştırılır:
  A) ŞAMPİYON (kesfet'in bulduğu en sağlam blok kümesi)   — gerçek şampiyon
  B) A + CONFIRM FİLTRE (kırılım-devam günü üst fade'i AT) — hipotezle korunmuş şampiyon
  C) B + kırılım-devam kolu (o günlerde trend-LONG işlem)  — şampiyon + hipotez birlikte

confirm = genlik%1-2 + Asia-HIGH hacim-destekli kırılım (kanıtlı hipotez, klines'tan).
Yalnız B ≥ A (PF↑, maxDD↓, OOS sağlam) ise confirm şampiyona eklemeye DEĞER.

Çalıştırma:  python oar_sampiyon_confirm.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
NOT: aggTrades gerektirir (şampiyon fade çıktısı için); ay ay ilerleme yazar.
"""
import argparse
from collections import deque
from datetime import datetime

from oar_local_backtest import (_klines_oku, _aggt_ay_yollari, _metrics_oku,
                                _ms_olcekle, _gun_hazirla, aday_sinyaller_uret,
                                GUN_MS, SAAT_MS)

MIN_RANGE_ALT = 1.0
MIN_RANGE_UST = 2.0
HACIM_PENCERE = 20
ASIA_BIT      = 4.0
OOS_ORAN      = 0.20


def _confirm_gunler(sembol, bas, bit):
    """
    Klines'tan (hızlı) her gün için kırılım-devam CONFIRM bayrağı:
    genlik %1-2 + Asia-HIGH hacim-destekli kırılım. Döner: set(gun_idx).
    NO-LOOKAHEAD hacim tabanı (önceki 20 gün).
    """
    import numpy as np
    k = _klines_oku(sembol, bas, bit)
    if k is None or not len(k):
        return set()
    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    OT_MIN, OT_MAX = 1_400_000_000_000, 2_000_000_000_000
    k = k[(k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)]
    k["gun"]  = (k["open_time"] // GUN_MS).astype("int64")
    k["saat"] = (k["open_time"] % GUN_MS) / SAAT_MS
    confirm = set()
    hacim_taban = deque(maxlen=HACIM_PENCERE)
    for g in sorted(k["gun"].unique()):
        gk = k[k["gun"] == g]
        asia = gk[gk["saat"] < ASIA_BIT]
        post = gk[gk["saat"] >= ASIA_BIT].sort_values("open_time")
        if asia.empty or post.empty:
            continue
        a_high = float(asia["high"].max()); a_low = float(asia["low"].min())
        gun_dk_hacim = float(post["volume"].mean())
        if a_low > 0 and (MIN_RANGE_ALT <= (a_high - a_low) / a_low * 100.0 < MIN_RANGE_UST):
            hi = post["high"].to_numpy()
            taban = float(np.median(hacim_taban)) if len(hacim_taban) >= 5 else None
            if (hi >= a_high).any() and taban is not None:
                ki = int(np.argmax(hi >= a_high))
                kir_hacim = float(post["volume"].to_numpy()[ki:ki + 60].mean())
                if kir_hacim > taban:
                    confirm.add(int(g))
        hacim_taban.append(gun_dk_hacim)
    return confirm


def _metrikler(netler):
    n = len(netler)
    if not n:
        return None
    kaz = [x for x in netler if x > 0]; kyp = [x for x in netler if x <= 0]
    gk = sum(kaz); gz = abs(sum(kyp))
    pf = round(gk / gz, 2) if gz > 0 else float("inf")
    eq = pk = dd = 0.0
    for x in netler:
        eq += x; pk = max(pk, eq); dd = max(dd, pk - eq)
    return {"n": n, "wr": round(100.0 * len(kaz) / n, 1), "pf": pf,
            "beklenti": round(sum(netler) / n, 3), "maxdd": round(dd, 2),
            "toplam": round(sum(netler), 1)}


def _oos(netler_ile_gun):
    """netler_ile_gun = [(gun, net)] → (in-sample netler, oos netler)."""
    s = sorted(netler_ile_gun, key=lambda x: x[0])
    kes = int(len(s) * (1 - OOS_ORAN))
    return [x[1] for x in s[:kes]], [x[1] for x in s[kes:]]


def _senaryo_metrik(kayitlar):
    """kayitlar = [(gun, net)] → metrik + OOS beklenti."""
    netler = [x[1] for x in kayitlar]
    m = _metrikler(netler)
    if not m:
        return None
    _, oos = _oos(kayitlar)
    om = _metrikler(oos)
    m["oos_beklenti"] = om["beklenti"] if om else None
    return m


def _analiz(sembol, bas, bit):
    """Şampiyon fade adaylarını üret + confirm bayrağı → 3 senaryonun (gun,net) listeleri."""
    print(f"   [{sembol}] confirm günleri (klines) hesaplanıyor…", flush=True)
    confirm = _confirm_gunler(sembol, bas, bit)
    print(f"   [{sembol}] {len(confirm)} kırılım-devam confirm günü", flush=True)

    print(f"   [{sembol}] şampiyon adayları (klines+aggTrades) — ay ay…", flush=True)
    klines = _klines_oku(sembol, bas, bit)
    yollar = _aggt_ay_yollari(sembol, bas, bit)
    if klines is None or not yollar:
        print(f"   [{sembol}] ⚠ parquet yok — atlandı", flush=True)
        return [], set()
    gunler = _gun_hazirla(klines, yollar, _metrics_oku(sembol, bas, bit))
    adaylar = aday_sinyaller_uret(gunler)
    # Her adayı sembol+gün ile etiketle (havuzda kaynağı kaybolmasın)
    for c in adaylar:
        c["_sembol"] = sembol
        c["_gun"] = int(c["ts"] // GUN_MS)
    print(f"   [{sembol}] ✓ {len(adaylar)} aday sinyal · {len(confirm)} confirm günü", flush=True)
    return adaylar, {(sembol, g) for g in confirm}


def _senaryolar(havuz, confirm_set, sampiyon_bloklar):
    """
    ŞAMPİYONUN GERÇEK seçim bloğuyla (kesfet'in bulduğu kombinasyon) A/B/C üret.
    A = şampiyon trade'leri (_filtre ile — proxy değil, gerçek blok mantığı).
    B = A − (kırılım-devam günü ÜST fade: mod=fade+SHORT).  C = B + devam kolu.
    """
    from oar_kesif import _filtre
    sampiyon_trades = _filtre(havuz, sampiyon_bloklar)   # şampiyonun gerçek işlem kümesi
    A, B, C = [], [], []
    for c in sampiyon_trades:
        anahtar = (c.get("_sembol"), c.get("_gun"))
        A.append((c["ts"], c["pct"]))   # ts = kronolojik sıra (equity için)
        ust_fade = (c.get("mod") == "fade" and c.get("yon") == "SHORT")
        if not (anahtar in confirm_set and ust_fade):
            B.append((c["ts"], c["pct"])); C.append((c["ts"], c["pct"]))
    # C ek kolu: confirm günü trend-LONG devam (hipotezle doğrulanmış kırılım-devam)
    for c in havuz:
        anahtar = (c.get("_sembol"), c.get("_gun"))
        if c.get("mod") == "trend" and c.get("yon") == "LONG" and anahtar in confirm_set:
            C.append((c["ts"], c["pct"]))
    return A, B, C


def _equity_sim(kayitlar, baslangic=1000.0, kaldirac=5.0):
    """
    $baslangic ile KRONOLOJİK compound equity simülasyonu (kaldıraçlı).
    Her işlem: equity *= (1 + net%/100 · kaldıraç). Tek işlemde ≤−(100/kaldıraç)%
    → hesap SIFIRLANIR (likidasyon). Döner: son bakiye, likide oldu mu + tarih,
    tepe/dip, maxDD%.
    """
    from datetime import datetime, timezone
    s = sorted(kayitlar, key=lambda x: x[0])
    eq = pik = baslangic
    max_dd = 0.0; min_eq = baslangic; liq_ts = None
    likidasyon_esigi = -100.0 / kaldirac    # 5x → −%20 tek işlemde sıfırlar
    for ts, net in s:
        if net <= likidasyon_esigi:          # bu tek işlem hesabı siler
            eq = 0.0; liq_ts = ts; break
        eq *= (1 + (net / 100.0) * kaldirac)
        if eq <= 0:
            eq = 0.0; liq_ts = ts; break
        pik = max(pik, eq)
        dd = (pik - eq) / pik * 100.0
        max_dd = max(max_dd, dd); min_eq = min(min_eq, eq)
    liq_tarih = (datetime.fromtimestamp(int(liq_ts) / 1000, timezone.utc).strftime("%Y-%m-%d")
                 if liq_ts else None)
    return {"son": round(eq, 2), "likide": liq_ts is not None, "likide_tarih": liq_tarih,
            "maxdd": round(max_dd, 1), "min_eq": round(min_eq, 2), "n": len(s)}


def _rapor(sampiyon, A, B, C):
    mA = _senaryo_metrik(A); mB = _senaryo_metrik(B); mC = _senaryo_metrik(C)
    bek = sampiyon.get("holdout_beklenti") or sampiyon.get("beklenti") or {}
    L = ["═══ ŞAMPİYON + KIRILIM-DEVAM CONFIRM BİRLEŞİK BACKTEST ═══",
         f"ŞAMPİYON (kesfet en sağlam): [{'+'.join(sampiyon.get('bloklar', []))}]",
         f"  OOS: puan {sampiyon.get('oos_puan')} WR%{sampiyon.get('oos_wr')} n{sampiyon.get('oos_trade')}"
         f" · HOLDOUT: puan {sampiyon.get('holdout_puan')} WR%{sampiyon.get('holdout_wr')} n{sampiyon.get('holdout_trade')}"
         f" · {'✅ SAĞLAM' if sampiyon.get('saglam') else '⚠ holdout zayıf'}",
         ""]
    def satir(ad, m):
        if not m:
            return f"  {ad}: yetersiz veri"
        return (f"  {ad}: n{m['n']} · WR%{m['wr']} · PF {m['pf']} · beklenti {m['beklenti']:+.3f}% "
                f"· maxDD {m['maxdd']}% · toplam {m['toplam']:+.0f}% · OOS beklenti {m['oos_beklenti']}")
    L.append(satir("A) ŞAMPİYON (gerçek blok kümesi)     ", mA))
    L.append(satir("B) A + CONFIRM filtre (üst fade AT)   ", mB))
    L.append(satir("C) B + kırılım-devam kolu (trend-LONG)", mC))
    if mA and mB:
        d_pf = (mB["pf"] - mA["pf"]) if (mA["pf"] != float("inf") and mB["pf"] != float("inf")) else None
        d_dd = mA["maxdd"] - mB["maxdd"]
        d_bek = mB["beklenti"] - mA["beklenti"]
        iyi = (d_bek >= 0 and d_dd >= 0 and (mB["oos_beklenti"] or 0) >= 0)
        L.append("")
        L.append(f"→ CONFIRM ETKİSİ (B−A): beklenti {d_bek:+.3f} · maxDD {d_dd:+.2f} "
                 f"· PF Δ {round(d_pf,3) if d_pf is not None else '—'}")
        L.append("✅ CONFIRM ŞAMPİYONU İYİLEŞTİRİYOR (ekle)" if iyi
                 else "❌ CONFIRM net iyileştirmiyor — şampiyona DOKUNMA")

    # ── $1000 COMPOUND EQUITY (1x/3x/5x) + LİKİDASYON — kullanıcı isteği ──
    L.append("\n─── $1000 COMPOUND EQUITY (2019 ilk gün → 2025 son gün, kronolojik) ───")
    def eq_satir(ad, kayitlar):
        parcalar = []
        for kald in (1, 3, 5):
            e = _equity_sim(kayitlar, 1000.0, float(kald))
            if e["likide"]:
                parcalar.append(f"{kald}x: 💀 SIFIRLANDI ({e['likide_tarih']})")
            else:
                kat = e["son"] / 1000.0
                parcalar.append(f"{kald}x: ${e['son']:,.0f} ({kat:.1f}x, dip ${e['min_eq']:,.0f}, maxDD%{e['maxdd']})")
        return f"  {ad}:\n     " + "  |  ".join(parcalar)
    L.append(eq_satir("A) FİLTRESİZ şampiyon", A))
    L.append(eq_satir("B) FİLTRELİ şampiyon  ", B))
    L.append("  NOT: 5x'te tek işlemde ≤−%20 → hesap SIFIRLANIR (likidasyon). "
             "'dip' = eğrinin gördüğü en düşük bakiye. Compound = her işlem öncekinin üstüne.")

    L.append("\nNOT: A = şampiyonun GERÇEK blok kümesi (kesfet+_filtre, proxy değil). "
             "Yalnız B, A'dan İYİ (beklenti↑ VE maxDD↓ VE OOS≥0) ise confirm eklenir.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to",   dest="bit", default="2025-06")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--sampiyon-blok", default="",
                    help="DOKUNULMAZ şampiyonun bloklarını SABİTLE (ör. 'mod_fade,htf_vpfr,range_rejimi'). "
                         "Verilirse kesfet YENİDEN ARAMAZ — confirm tam bu şampiyona uygulanır. Boşsa keşfeder.")
    args = ap.parse_args()

    # 1) Havuz: tüm sembollerin adayları + confirm günleri (sembol,gün)
    havuz, confirm_set = [], set()
    for sym in [s.strip().upper() for s in args.symbol.split(",") if s.strip()]:
        print(f"[ŞampiyonConfirm] {sym} {args.bas}..{args.bit}…", flush=True)
        adaylar, conf = _analiz(sym, args.bas, args.bit)
        havuz += adaylar; confirm_set |= conf
    if not havuz:
        print("⚠ aday yok — parquet/aggTrades eksik olabilir."); return

    # 2) ŞAMPİYON: sabitlendiyse ONU kullan (dokunulmaz şampiyon), yoksa kesfet keşfetsin
    if args.sampiyon_blok.strip():
        bloklar = [b.strip() for b in args.sampiyon_blok.split(",") if b.strip()]
        print(f"[ŞampiyonConfirm] ŞAMPİYON SABİT (dokunulmaz): [{'+'.join(bloklar)}] "
              f"— kesfet çağrılmadı, confirm tam bu şampiyona uygulanıyor.", flush=True)
        sampiyon = {"bloklar": bloklar, "oos_puan": "—", "oos_wr": "—", "oos_trade": "—",
                    "holdout_puan": "—", "holdout_wr": "—", "holdout_trade": "—", "saglam": None}
    else:
        print(f"[ŞampiyonConfirm] kesfet: en sağlam blok kombinasyonu aranıyor "
              f"({len(havuz)} aday)… (--sampiyon-blok ile sabitleyebilirsin)", flush=True)
        from oar_kesif import kesfet
        kesif = kesfet(havuz)
        enler = kesif.get("en_iyiler") or []
        sampiyon = next((a for a in enler if a.get("saglam")), enler[0] if enler else None)
        if not sampiyon:
            print("⚠ kesfet şampiyon bulamadı (yeterli sinyalli kombinasyon yok)."); return
    print(f"[ŞampiyonConfirm] şampiyon: [{'+'.join(sampiyon['bloklar'])}] "
          f"OOS puan {sampiyon.get('oos_puan')} · {'SAĞLAM' if sampiyon.get('saglam') else 'zayıf'}",
          flush=True)

    # 3) Şampiyonun GERÇEK blok kümesiyle A/B/C
    A, B, C = _senaryolar(havuz, confirm_set, sampiyon["bloklar"])
    rapor = _rapor(sampiyon, A, B, C)
    print("\n" + rapor)

    if args.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            asyncio.run(bildir("Şampiyon+Confirm Backtest", "backtest",
                               f"Şampiyon [{'+'.join(sampiyon['bloklar'])}] + kırılım-devam confirm testi",
                               detay=rapor))
            print("\n[Telegram] thread 4129'a gönderildi ✓", flush=True)
        except Exception as e:
            print(f"\n[Telegram] gönderilemedi: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
