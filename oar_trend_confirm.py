"""
oar_trend_confirm.py — CANLI TREND PROXY vs ŞAMPİYON: eksik blok ne kadar zarar ettiriyor?
════════════════════════════════════════════════════════════════════════════════════════
KULLANICI PROBLEMİ (2026-07: TREND -%22, R:R ters): canlı TREND (oar_trend_paper) şampiyon
kirilim_devam_trend'in 3 bloğundan (poc_taraf + footprint_trapped + gun_bias_uyum) yalnız
gun_bias_uyum'u uyguluyor — poc_taraf/footprint_trapped tick-veri istiyordu, canlıda YOKtu.
Sonuç: canlı proxy daha GEVŞEK → düşük-kalite kırılımları alıyor → R:R ters → zarar.

BU ARAÇ (ANAYASA #8 GÜVENLİ — şampiyon KODUNA DOKUNMAZ, yalnız _filtre/serap OKUR):
5 senaryoyu AYNI trend aday havuzunda ölçer, hangi eksik bloğun ne kadar edge kapattığını
KANITLAR. poc_taraf artık canlıda hesaplanabilir (asia_poc_gercek — FADE zaten kullanıyor).
footprint_trapped (reclaim) canlıda kısmen hesaplanabilir (sweep+reclaim).

  CHAMPION : [poc_taraf, footprint_trapped, gun_bias_uyum]  — kanıtlı DSR 1.0 hedef (n897)
  LIVE_A   : [gun_bias_uyum]                                — canlı TREND'in ŞU AN aldığı
  A+poc    : [gun_bias_uyum, poc_taraf]                     — canlıya EKLENEBİLİR (gerçek POC)
  A+trap   : [gun_bias_uyum, footprint_trapped]            — canlıya kısmen eklenebilir
  A+both   : = CHAMPION                                     — ikisi de eklenirse

Her senaryo: PF/beklenti/WR/maxDD/OOS + $1000·5x equity + SERAP karnesi (DSR/CI/likid).
KARAR (rapor): canlıya eklenmeye DEĞER blok = A'dan PF↑ VE maxDD↓ VE R:R düzeliyor VE
serap-geçer (DSR≥0.95). Eklemek ANAYASA #8 → kullanıcı onayı + canlı wiring ayrı adım.
Çıktı: oar_trend_confirm_sonuc.json (repo kökü, git-senkron). aggTrades → ay ay ilerler.

Çalıştırma:
  python oar_trend_confirm.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
"""
import argparse
import json
from pathlib import Path

from oar_sampiyon_confirm import _analiz, _senaryo_metrik, _equity_sim

KOK = Path(__file__).resolve().parent
CIKTI = KOK / "oar_trend_confirm_sonuc.json"

# Şampiyon TREND blokları — KANIT: oar_sampiyon_portfoy.json (kirilim_devam_trend).
CHAMP = ["poc_taraf", "footprint_trapped", "gun_bias_uyum"]
SENARYOLAR = {
    "CHAMPION":  ["poc_taraf", "footprint_trapped", "gun_bias_uyum"],
    "LIVE_A":    ["gun_bias_uyum"],
    "A+poc":     ["gun_bias_uyum", "poc_taraf"],
    "A+trap":    ["gun_bias_uyum", "footprint_trapped"],
}
N_DENEME = 50   # bu araçta denenen senaryo sayısı mertebesinde (serap DSR düzeltmesi)


def _trend_havuz(havuz):
    """Yalnız TREND adayları — şampiyon trend bloğu bunlara uygulanır (fade'e değil)."""
    return [c for c in havuz if c.get("mod") == "trend"]


def _rr(netler):
    kaz = [n for n in netler if n > 0]
    kay = [n for n in netler if n <= 0]
    if not kaz or not kay:
        return None
    ok = sum(kaz) / len(kaz)
    oy = abs(sum(kay) / len(kay))
    return round(ok / oy, 2) if oy else None


def _senaryo_hesapla(havuz_trend, bloklar):
    from oar_kesif import _filtre
    trades = _filtre(havuz_trend, bloklar)
    if not trades:
        return None
    kayitlar = [(c["ts"], c["pct"]) for c in trades]
    m = _senaryo_metrik(kayitlar)
    if not m:
        return None
    netler = [c["pct"] for c in trades]
    eq = _equity_sim(sorted(kayitlar), kaldirac=5.0)
    kart = {
        "n": len(trades), "pf": m.get("pf"), "beklenti": m.get("beklenti"),
        "wr": m.get("wr"), "maxdd": m.get("maxdd"), "oos_beklenti": m.get("oos_beklenti"),
        "rr": _rr(netler),
        "equity_5x_son": eq.get("son"), "likide_5x": eq.get("likide"),
        "_kayitlar": kayitlar,
    }
    return kart


def _serap(kart):
    """Senaryo işlem serisine serap karnesi (DSR/CI/perm/likidasyon)."""
    try:
        from oar_serap_testi import serap_karnesi, _karar
        k = kart.pop("_kayitlar")
        karne = serap_karnesi("trend_senaryo", [x[1] for x in k], [x[0] for x in k], N_DENEME)
        dsr = (karne.get("deflated_sharpe") or {}).get("dsr")
        ci = (karne.get("bootstrap_beklenti_ci") or {}).get("alt")
        lik = ((karne.get("mc_equity") or {}).get("5x") or {}).get("likidasyon_orani")
        return {"dsr": dsr, "ci_alt": ci, "perm_p": karne.get("permutasyon_p"),
                "likid_5x": lik, "karar": _karar(karne, True)}
    except Exception as e:
        kart.pop("_kayitlar", None)
        return {"hata": str(e)[:80]}


def calistir(semboller, bas, bit):
    havuz = []
    for s in semboller:
        adaylar, _cf = _analiz(s, bas, bit)
        havuz.extend(adaylar)
    ht = _trend_havuz(havuz)
    print(f"[TrendConfirm] toplam {len(havuz)} aday · {len(ht)} TREND adayı", flush=True)
    out = {"aralik": f"{bas}..{bit}", "semboller": semboller,
           "sampiyon_bloklar": CHAMP, "senaryolar": {}}
    for ad, bloklar in SENARYOLAR.items():
        k = _senaryo_hesapla(ht, bloklar)
        if k:
            k["serap"] = _serap(k)
            k.pop("_kayitlar", None)
        out["senaryolar"][ad] = k
        if k:
            print(f"  {ad:9s} n{k['n']:4d} PF {k['pf']} beklenti {k['beklenti']} "
                  f"R:R {k['rr']} maxDD%{k['maxdd']} OOS {k['oos_beklenti']} "
                  f"| serap {k['serap'].get('karar','?')}", flush=True)
    out["karar"] = _karar_metni(out["senaryolar"])
    CIKTI.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[TrendConfirm] → {CIKTI.name}", flush=True)
    return out


def _karar_metni(sen):
    A = sen.get("LIVE_A")
    C = sen.get("CHAMPION")
    if not A or not C:
        return "⚠ yetersiz veri (havuz boş — parquet gerekli)"
    L = [f"CANLI PROXY (LIVE_A, gun_bias_uyum) PF {A['pf']} R:R {A['rr']} maxDD%{A['maxdd']} "
         f"↔ ŞAMPİYON (3 blok) PF {C['pf']} R:R {C['rr']} maxDD%{C['maxdd']}."]
    fark = (C['pf'] or 0) - (A['pf'] or 0)
    L.append(f"Blok eksikliği kaynaklı PF farkı ≈ {fark:.2f}. Kapatan blok:")
    for ad in ("A+poc", "A+trap"):
        k = sen.get(ad)
        if not k:
            continue
        gecer = (k.get("serap", {}).get("dsr") or 0) >= 0.95
        iyi = (k['pf'] or 0) > (A['pf'] or 0) and (k['maxdd'] or 99) <= (A['maxdd'] or 99)
        L.append(f"  • {ad}: PF {k['pf']} R:R {k['rr']} maxDD%{k['maxdd']} "
                 f"→ {'✅ A''dan iyi' if iyi else '≈ A'} · serap {'GEÇER' if gecer else 'GEÇMEZ'}")
    L.append("KARAR: A'dan PF↑ VE maxDD↓ VE serap-geçer blok → canlı TREND girişine EKLE "
             "(ANAYASA #8: kullanıcı onayı + wiring ayrı). Aksi → şampiyona dokunma; "
             "canlı proxy zayıflığı yapısaldır, tek-ay zararı normaldir.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--telegram", action="store_true")
    a = ap.parse_args()
    semboller = [s.strip() for s in a.symbol.split(",") if s.strip()]
    out = calistir(semboller, a.bas, a.bit)
    print("\n" + out.get("karar", ""))
    if a.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            asyncio.run(bildir("TrendConfirm", "backtest",
                               "Canlı TREND proxy vs şampiyon blok analizi", out.get("karar", "")))
        except Exception as e:
            print(f"telegram hatası: {str(e)[:60]}")


if __name__ == "__main__":
    main()
