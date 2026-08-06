"""
oar_trend_10ay.py — 10 RASTGELE AY $1000·5x: mevcut canlı TREND vs poc_taraf'lı vs şampiyon
════════════════════════════════════════════════════════════════════════════════════════
KULLANICI: "şampiyonu değiştirmeden önce rastgele 10 ayı trade ettirelim; $1000·5x ile 10
ayda kaç dolar yapıyor görelim." → poc_taraf'ı canlıya bağlamadan ÖNCE somut $ kanıtı.

3 senaryo AYNI 10 rastgele ayda, AYNI trend aday havuzunda (ANAYASA #8 GÜVENLİ — şampiyon
koduna DOKUNMAZ, oar_kesif._filtre okur):
  • MEVCUT  (LIVE_A)     : [gun_bias_uyum]              — canlı TREND'in ŞU AN aldığı
  • poc_taraf'LI (A+poc) : [gun_bias_uyum, poc_taraf]   — önerilen değişiklik
  • ŞAMPİYON             : [poc_taraf, footprint_trapped, gun_bias_uyum]  — kanıtlı hedef

$1000 başlangıç, 5x, compound (oar_sampiyon_confirm._equity_sim). Hem 10 ayın KRONOLOJİK
birleşik sonucu, hem ay-ay bağımsız ($1000→$X her ay) dağılım. Havuz .trend_havuz_cache.pkl'a
cache'lenir → ilk koşu aggTrades işler (ağır), tekrarlar anında (farklı --seed hızlı denenir).

⚠ backtest base-exit (tp_sl_breakout) $ kullanır; canlı TP_3R değil → mutlak $ birebir canlı
değil, KIYAS (senaryolar arası) geçerli. "Getiri fantezi, güvenilir olan relatif+likidasyon."

Çalıştırma:
  python oar_trend_10ay.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06
  python oar_trend_10ay.py --seed 42          # tekrarlanabilir aynı 10 ay
  python oar_trend_10ay.py --ay-sayisi 10 --taze   # cache'i yok say, yeniden kur
"""
import argparse
import pickle
import random
from datetime import datetime, timezone
from pathlib import Path

from oar_sampiyon_confirm import _analiz, _equity_sim

KOK = Path(__file__).resolve().parent
CACHE = KOK / ".trend_havuz_cache.pkl"

SENARYOLAR = {
    "MEVCUT (canlı)":  ["gun_bias_uyum"],
    "poc_taraf'lı":    ["gun_bias_uyum", "poc_taraf"],
    "ŞAMPİYON":        ["poc_taraf", "footprint_trapped", "gun_bias_uyum"],
}


def _ay_of(ts):
    return datetime.fromtimestamp(int(ts) / 1000, timezone.utc).strftime("%Y-%m")


def _havuz_kur(semboller, bas, bit, taze):
    if CACHE.exists() and not taze:
        try:
            d = pickle.loads(CACHE.read_bytes())
            if d.get("semboller") == semboller and d.get("aralik") == f"{bas}..{bit}":
                print(f"[10ay] havuz cache'ten yüklendi ({len(d['havuz'])} trend adayı)", flush=True)
                return d["havuz"]
        except Exception:
            pass
    havuz = []
    for s in semboller:
        adaylar, _cf = _analiz(s, bas, bit)
        havuz.extend([c for c in adaylar if c.get("mod") == "trend"])
    CACHE.write_bytes(pickle.dumps({"semboller": semboller, "aralik": f"{bas}..{bit}", "havuz": havuz}))
    print(f"[10ay] havuz kuruldu + cache'lendi ({len(havuz)} trend adayı)", flush=True)
    return havuz


def _senaryo_trades(havuz, bloklar):
    from oar_kesif import _filtre
    return _filtre(havuz, bloklar)


def calistir(semboller, bas, bit, ay_sayisi, seed, taze):
    havuz = _havuz_kur(semboller, bas, bit, taze)
    # Senaryo işlemlerini bir kez hesapla
    sen_trades = {ad: _senaryo_trades(havuz, bl) for ad, bl in SENARYOLAR.items()}
    # Aday ay havuzu = ŞAMPİYON'un işlem yaptığı aylar (adil: her ayda en az bir gerçek işlem)
    tum_aylar = sorted({_ay_of(c["ts"]) for c in sen_trades["ŞAMPİYON"]})
    if len(tum_aylar) < ay_sayisi:
        print(f"⚠ yalnız {len(tum_aylar)} ay var, {ay_sayisi} istendi → hepsi kullanılıyor")
        ay_sayisi = len(tum_aylar)
    if seed is None:
        seed = random.randrange(1_000_000)
    random.seed(seed)
    secilen = sorted(random.sample(tum_aylar, ay_sayisi))
    print(f"\n═══ {ay_sayisi} RASTGELE AY (seed={seed}) ═══")
    print("Aylar:", ", ".join(secilen))
    secilen_set = set(secilen)

    ozet = {}
    for ad, trades in sen_trades.items():
        pencere = [(c["ts"], c["pct"]) for c in trades if _ay_of(c["ts"]) in secilen_set]
        birlesik = _equity_sim(sorted(pencere), 1000.0, 5.0)
        # ay-ay bağımsız ($1000 her ay)
        aylik = {}
        for ay in secilen:
            ay_tr = sorted([(ts, p) for ts, p in pencere if _ay_of(ts) == ay])
            e = _equity_sim(ay_tr, 1000.0, 5.0) if ay_tr else {"son": 1000.0, "n": 0}
            aylik[ay] = {"son": e["son"], "n": e["n"]}
        ozet[ad] = {"birlesik": birlesik, "aylik": aylik, "n": len(pencere)}

    # ── Rapor ──
    print(f"\n{'SENARYO':<18}{'işlem':>7}{'10-ay birleşik $':>20}{'maxDD%':>9}{'likide':>8}")
    print("─" * 62)
    for ad, o in ozet.items():
        b = o["birlesik"]
        print(f"{ad:<18}{o['n']:>7}{('$'+format(b['son'],',.0f')):>20}"
              f"{b['maxdd']:>9}{('EVET' if b['likide'] else 'yok'):>8}")

    print(f"\n── AY-AY $1000→$X (bağımsız) ──")
    print(f"{'ay':<10}" + "".join(f"{ad[:14]:>16}" for ad in SENARYOLAR))
    for ay in secilen:
        satir = f"{ay:<10}"
        for ad in SENARYOLAR:
            a = ozet[ad]["aylik"][ay]
            hucre = "$" + format(a["son"], ",.0f") + "(" + str(a["n"]) + ")"
            satir += f"{hucre:>16}"
        print(satir)

    # ── Karar ──
    m = ozet["MEVCUT (canlı)"]["birlesik"]
    p = ozet["poc_taraf'lı"]["birlesik"]
    print(f"\n═══ SONUÇ ═══")
    print(f"MEVCUT canlı TREND: $1000 → ${m['son']:,.0f}  (maxDD %{m['maxdd']}, "
          f"likidasyon {'VAR' if m['likide'] else 'yok'})")
    print(f"poc_taraf'lı      : $1000 → ${p['son']:,.0f}  (maxDD %{p['maxdd']}, "
          f"likidasyon {'VAR' if p['likide'] else 'yok'})")
    if m["son"] > 0:
        kat = p["son"] / m["son"] if m["son"] else 0
        print(f"→ poc_taraf'lı MEVCUT'un {kat:.1f}katı." if kat >= 1 else
              f"→ poc_taraf'lı MEVCUT'tan DÜŞÜK ({kat:.2f}x).")
    print(f"(seed={seed} — aynı 10 ayı tekrarlamak için --seed {seed})")
    return ozet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--ay-sayisi", type=int, default=10)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--taze", action="store_true", help="cache'i yok say, havuzu yeniden kur")
    a = ap.parse_args()
    semboller = [s.strip() for s in a.symbol.split(",") if s.strip()]
    calistir(semboller, a.bas, a.bit, a.ay_sayisi, a.seed, a.taze)


if __name__ == "__main__":
    main()
