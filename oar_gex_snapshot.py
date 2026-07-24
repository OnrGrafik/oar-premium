# ═══════════════════════════════════════════════════════════════════
# GEX ISI HARİTASI SNAPSHOT DEPOSU (Faz 3 — Δ anlık/gün-içi + gamma drift)
# ───────────────────────────────────────────────────────────────────
# Saf depolama: options_engine gamma grid'ini periyodik kaydeder → hücre
# başına Δ (şimdi − ~22dk önce / − bugünkü açılış) ve gün-içi gamma drift
# zaman serisi hesaplanır. Dosya-yedekli (restart-dayanıklı). options_engine'i
# IMPORT ETMEZ (döngü yok); loop içinde geç import eder.
# ═══════════════════════════════════════════════════════════════════
import json, os, time, asyncio
from datetime import datetime, timezone

_DIR = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
_TTL_SAAT = 26      # bu kadar saatten eski snapshot atılır
_MAX = 400          # en fazla snapshot (5dk×400 ≈ 33 saat)
_mem = {}           # currency -> list[snap]

def _yol(cur): return os.path.join(_DIR, f"gex_snapshot_{cur}.json")

def _yukle(cur):
    if cur in _mem: return _mem[cur]
    try:
        with open(_yol(cur)) as f: _mem[cur] = json.load(f)
    except Exception:
        _mem[cur] = []
    return _mem[cur]

def kaydet(cur, snap):
    """snap = {ts, cells:{'strike|expTs':gex}, net_gex, zero_gamma, spot}"""
    lst = _yukle(cur); lst.append(snap)
    kes = int(time.time()*1000) - _TTL_SAAT*3600*1000
    lst = [s for s in lst if s.get("ts", 0) >= kes][-_MAX:]
    _mem[cur] = lst
    try:
        with open(_yol(cur), "w") as f: json.dump(lst, f)
    except Exception:
        pass

def en_yakin(cur, dakika):
    """`dakika` dk öncesine en yakın snapshot (o andan yeni olanları alma)."""
    lst = _yukle(cur)
    if not lst: return None
    hedef = int(time.time()*1000) - dakika*60*1000
    aday = [s for s in lst if s.get("ts", 0) <= hedef + dakika*30*1000]
    if not aday: return None
    return min(aday, key=lambda s: abs(s.get("ts", 0) - hedef))

def gun_ilk(cur):
    """Bugünün (UTC) ilk snapshot'ı = gün-içi Δ referansı."""
    lst = _yukle(cur)
    if not lst: return None
    g0 = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()*1000)
    bugun = [s for s in lst if s.get("ts", 0) >= g0]
    return bugun[0] if bugun else None

def drift(cur):
    """Bugünün net_gex / zero_gamma / spot zaman serisi (gamma drift paneli)."""
    lst = _yukle(cur)
    g0 = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()*1000)
    return [{"ts": s["ts"], "net_gex": s.get("net_gex"), "zero_gamma": s.get("zero_gamma"), "spot": s.get("spot")}
            for s in lst if s.get("ts", 0) >= g0]

async def snapshot_loop(currencies=("BTC", "ETH"), aralik=300):
    """Her `aralik` sn gamma grid'ini kaydeder (Δ + drift için tarih birikir).
    options_engine.gex_heatmap zaten greek=gamma'da snapshot yazar → burada
    sadece onu tetikleriz. İzole: hata olursa döngü ölmez."""
    import options_engine as oe
    await asyncio.sleep(30)
    while True:
        for cur in currencies:
            try:
                await oe.gex_heatmap(cur, "gamma")   # içinde kaydet() çağrılır
            except Exception:
                pass
        await asyncio.sleep(aralik)
