"""
OAR Swing Sistem — OAR Premium
═══════════════════════════════════════════════════════════════════════════════
SWING için fib NEREDEN çekilir? Çekirdek kural (kullanıcı dokümanı):

  • Bir değer alanı TERK edilir ve fiyat %15+ kırılım yapar (impuls).
  • AŞAĞI kırılımda: impuls dibinden sonra yapılan TEPE, ardından DİP → yeni range.
      Geçerlilik: bu DİP, impuls dibinin altına inip LL YAPMAZ.
  • YUKARI kırılımda: impuls tepesinden sonra yapılan DİP, ardından TEPE → yeni range.
      Geçerlilik: bu TEPE, impuls tepesini geçip yeni HH YAPMAZ.
  • Fib bu RANGE'den (tepe↔dip) çekilir. Her büyük hareket sonrası bu range geçerlidir.

Bu modül "fib'i nereden çekeceğiz" çekirdeğidir (saf, test edilebilir). Teyitler
(VPFR direnç, FP/CVD satıcılı, RSI, VWAP bandı, CB premium, GEX) ayrıca bağlanır.
Kullanıcı gün gün incelik öğrettikçe burası rafine edilir.
"""
import asyncio
import json
from datetime import datetime, timezone

# OAR fib oranları (daily ile aynı dil)
FIB_ORANLAR = [2.618, 2.272, 1.618, 1.377, 1.0, 0.5, 0.0, -0.377, -0.618, -1.272, -1.618]
ESIK_PCT = 15.0          # değer alanı terk impuls eşiği
PIVOT_SOL = 3
PIVOT_SAG = 3


# ─── Saf hesap: pivotlar + swing range + fib ─────────────────────────────────
def pivotlar(mumlar: list, sol: int = PIVOT_SOL, sag: int = PIVOT_SAG):
    """
    mumlar: [[ts,o,h,l,c], ...] kronolojik. Döner: (tepeler, dipler) — her biri
    (idx, fiyat) listesi. Tepe = [i-sol, i+sag] penceresinde en yüksek high;
    dip = en düşük low. (Klasik fraktal pivot.)
    """
    tepeler, dipler = [], []
    n = len(mumlar)
    for i in range(sol, n - sag):
        h = mumlar[i][2]
        l = mumlar[i][3]
        pencere = mumlar[i - sol:i + sag + 1]
        if h == max(m[2] for m in pencere):
            tepeler.append((i, h))
        if l == min(m[3] for m in pencere):
            dipler.append((i, l))
    return tepeler, dipler


def _dizi(tepeler, dipler):
    """Tepe/dipleri index'e göre alternatif sıraya diz: [(idx, fiyat, 'H'|'L'), ...]."""
    seq = [(i, p, "H") for i, p in tepeler] + [(i, p, "L") for i, p in dipler]
    seq.sort(key=lambda x: x[0])
    # aynı tipte ardışık olanları sadeleştir (H ise en yükseği, L ise en düşüğü tut)
    sade = []
    for e in seq:
        if sade and sade[-1][2] == e[2]:
            if (e[2] == "H" and e[1] > sade[-1][1]) or (e[2] == "L" and e[1] < sade[-1][1]):
                sade[-1] = e
        else:
            sade.append(e)
    return sade


def fib_swing(range_low: float, range_high: float) -> dict:
    """Range'den OAR fib seviyeleri (0=dip, 1=tepe)."""
    genislik = range_high - range_low
    return {oran: round(range_low + oran * genislik, 8) for oran in FIB_ORANLAR}


def swing_kurulum(mumlar: list, esik_pct: float = ESIK_PCT,
                  sol: int = PIVOT_SOL, sag: int = PIVOT_SAG) -> dict | None:
    """
    En güncel geçerli SWING range'i ve fib'i döndürür (yoksa None).
    Mantık: son ≥esik% impuls bacağını bul → sonraki tepe-dip (ya da dip-tepe)
    range'ini al → LL/HH geçerlilik kuralını uygula → fib çek.
    """
    seq = _dizi(*pivotlar(mumlar, sol, sag))
    if len(seq) < 4:
        return None
    # En sondan geriye doğru ≥esik% impuls bacağı ara (zıt tipli iki pivot arası)
    for k in range(len(seq) - 1, 0, -1):
        ai, ap, at = seq[k - 1]
        bi, bp, bt = seq[k]
        if at == bt:
            continue
        hareket = abs(bp - ap) / ap * 100 if ap else 0
        if hareket < esik_pct:
            continue
        # impuls bulundu: a→b. Sonraki iki pivot range'i oluşturur.
        if k + 2 >= len(seq):
            return None     # range henüz tamamlanmadı (tepe+dip yok)
        c = seq[k + 1]      # impuls sonrası 1. pivot
        d = seq[k + 2]      # impuls sonrası 2. pivot

        if bt == "L":       # AŞAĞI kırılım (impuls dibe indi): sonra TEPE(c=H) → DİP(d=L)
            if c[2] != "H" or d[2] != "L":
                return None
            tepe, dip = c[1], d[1]
            gecerli = dip >= bp          # DİP, impuls dibinin altına inmez (LL yok)
            yon = "SHORT"
        else:               # YUKARI kırılım (impuls tepeye çıktı): sonra DİP(c=L) → TEPE(d=H)
            if c[2] != "L" or d[2] != "H":
                return None
            dip, tepe = c[1], d[1]
            gecerli = tepe <= bp          # TEPE, impuls tepesini geçmez (HH yok)
            yon = "LONG"

        if tepe <= dip:
            return None
        return {
            "yon": yon,
            "kirilim": "DOWN" if bt == "L" else "UP",
            "impuls_pct": round(hareket, 2),
            "impuls_fiyat": round(bp, 8),
            "range_low": round(dip, 8),
            "range_high": round(tepe, 8),
            "gecerli": bool(gecerli),     # LL/HH kuralı geçti mi
            "fibs": fib_swing(dip, tepe),
            "impuls_idx": bi,
        }
    return None


# ─── Canlı tarama + bildirim ─────────────────────────────────────────────────
def _dosya():
    from data_ingest import hist_dir
    return hist_dir() / "oar_swing.json"


def _yukle():
    yol = _dosya()
    if yol.exists():
        try:
            with open(yol, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"son_bildirim": {}}


def _kaydet(d):
    yol = _dosya()
    yol.parent.mkdir(parents=True, exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


async def _mumlar(sembol, interval="4h", limit=200):
    from exchange_client import klines as _ec_klines
    rows = await _ec_klines(sembol, interval, limit, futures=False)
    # [ts,o,h,l,c] formatına indir
    return [[r[0], r[1], r[2], r[3], r[4]] for r in rows]


async def tara(sembol: str, interval: str = "4h") -> dict | None:
    """Bir sembolde güncel OAR Swing kurulumu (range + fib) döndürür."""
    try:
        mumlar = await _mumlar(sembol, interval)
    except Exception:
        return None
    if len(mumlar) < 30:
        return None
    k = swing_kurulum(mumlar)
    if k:
        k["sembol"] = sembol
        k["interval"] = interval
        k["fiyat"] = mumlar[-1][4]
    return k


def _bildirim_metni(k: dict) -> str:
    kisa = k["sembol"].replace("USDT", "")
    fibs = k["fibs"]
    onemli = {o: fibs[o] for o in (1.0, 0.5, 0.0, -0.618, 1.618) if o in fibs}
    fib_str = " · ".join(f"{o}:{v:,.2f}" for o, v in onemli.items())
    return (f"🟣 OAR SWING SİSTEM — {kisa} {k['yon']}\n"
            f"Kırılım: {k['kirilim']} %{k['impuls_pct']} (değer alanı terk)\n"
            f"Range: {k['range_low']:,.2f} ↔ {k['range_high']:,.2f}"
            f"{' ✅ geçerli' if k['gecerli'] else ' ⚠ LL/HH kuralı geçmedi'}\n"
            f"Fib: {fib_str}\n"
            f"Fiyat: {k.get('fiyat',0):,.2f} · TF {k.get('interval','4h')}")


async def _tg(metin):
    try:
        from oar_altcoin_sistem import TG_CHAT, TG_THREAD
        from main import _telegram_gonder
        await _telegram_gonder(metin, thread_id=TG_THREAD, chat_id=TG_CHAT)
    except Exception as e:
        print(f"[OAR-Swing] telegram hatası: {str(e)[:60]}")


async def dongu(semboller=("BTCUSDT", "ETHUSDT"), interval: int = 3600):
    """Saatlik OAR Swing taraması; YENİ/geçerli kurulumda 'OAR Swing Sistem' bildirimi."""
    await asyncio.sleep(150)
    while True:
        try:
            d = _yukle()
            for s in semboller:
                k = await tara(s)
                if not k or not k.get("gecerli"):
                    continue
                imza = f"{s}:{k['kirilim']}:{round(k['range_low'])}:{round(k['range_high'])}"
                if d["son_bildirim"].get(s) == imza:
                    continue                 # aynı range zaten bildirildi → spam yok
                d["son_bildirim"][s] = imza
                await _tg(_bildirim_metni(k))
                print(f"[OAR-Swing] bildirim: {imza}")
            _kaydet(d)
        except Exception as e:
            print(f"[OAR-Swing] döngü hatası: {str(e)[:80]}")
        await asyncio.sleep(interval)


def durum_ozet(semboller=("BTCUSDT", "ETHUSDT")) -> dict:
    """UI için son bildirilen swing imzaları (canlı tara endpoint ayrı)."""
    return _yukle()


if __name__ == "__main__":
    print(json.dumps(_yukle(), ensure_ascii=False, indent=2))
