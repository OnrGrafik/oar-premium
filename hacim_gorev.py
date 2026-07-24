"""
hacim_gorev.py — GÖREV WORKER (write-only kuyruk açığını kapatır) + konsey görev üretimi
════════════════════════════════════════════════════════════════════════════════════
Kullanıcı isteği: "worker görevi alıp çalıştırsın." + "konsey anlamlı durum bulunca
backtest/researcher'a görev versin."

DÜRÜST MİMARİ (ANAYASA #3 — koddan doğrulandı):
  • Railway'de GEÇMİŞ parquet veri YOK → derin backtest sunucuda KOŞAMAZ. Derin backtest
    yalnız kullanıcının PC'sinde (oar_vardiya / oar_hipotez_motoru / oar_sampiyon_confirm).
  • Bu yüzden worker görevi "çalıştırmak" = TRİYAJ + git-senkron YEREL KUYRUĞA yönlendirme
    + TAMAMLANANA kadar TAKİP. Böylece leader_agent'in bugüne dek YAZILIP OKUNMAYAN
    (write-only) `agent_tasks.json` kuyruğu gerçekten TÜKETİLİR (mimari §3 açığı kapanır).

AKIŞ:
  1. Konsey gün sonu güçlü+kalıcı konsensüs bulursa → görev üretir (konsey_gorev_uret).
  2. Leader'ın eski `agent_tasks.json` bekleyen hipotezleri de bu kuyruğa AKTARILIR (drenaj).
  3. Worker her görevi git-senkron `hacim_gorev_kuyruk.json`'a yazar (repo-kökü → kullanıcı
     `git pull` ile PC'ye indirir; vardiya backtest eder).
  4. TAMAMLANMA sinyali: (a) PC `hacim_gorev_sonuc.json`'a sonucu yazıp push eder VEYA
     (b) eşleşen bir kanıtlı bulgu (kanitli_bulgular.json, mevcut git-senkron akış) belirir
     → worker görevi 'tamamlanan'a taşır. Lider bağlamı + /api/hacim-gorev bunu gösterir.

NOT: bu katman CANLI KARARA DOKUNMAZ. Üretilen görev = ADAY hipotez; ancak PC backtest'i +
serap testi (DSR≥0.95) geçerse kanıt olur (ANAYASA #8, 5p/5e kuralı).
"""
import os
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone

_DATA_DIR = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
                 or ("/var/data" if Path("/var/data").exists() else "data"))
_DATA_DIR.mkdir(parents=True, exist_ok=True)

GOREV_FILE   = _DATA_DIR / "hacim_gorev.json"                                  # worker iç durumu
YEREL_KUYRUK = Path(__file__).resolve().parent / "hacim_gorev_kuyruk.json"     # repo-kökü, git-senkron (PC'ye iner)
SONUC_FILE   = Path(__file__).resolve().parent / "hacim_gorev_sonuc.json"      # repo-kökü, git-senkron (PC yazar)

WORKER_ARALIK_S = 900        # 15 dk'da bir triyaj/takip turu
MUTABAKAT_ESIK  = 0.5        # gün konsensüs mutabakatı ≥ %50 → görev üretmeye değer
MIN_SNAPSHOT    = 12         # gün içinde en az 12 snapshot (yeterli örnek) varsa görev üret


def _now():
    return datetime.now(timezone.utc)

def _load(p, d):
    try:
        return json.loads(Path(p).read_text()) if Path(p).exists() else d
    except Exception:
        return d

def _save(p, d):
    try:
        Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2))
    except Exception:
        pass

def _durum():
    return _load(GOREV_FILE, {"bekleyen": [], "yerel_kuyrukta": [], "tamamlanan": []})

def _durum_yaz(d):
    _save(GOREV_FILE, d)


# ── Görev üretimi ────────────────────────────────────────────────────────────────
def _hipotez_var_mi(d, hipotez):
    for kova in ("bekleyen", "yerel_kuyrukta", "tamamlanan"):
        if any(g.get("hipotez") == hipotez for g in d.get(kova, [])):
            return True
    return False

def gorev_ekle(kaynak: str, sembol: str, hipotez: str, tetik: str = "", meta: dict = None) -> bool:
    """Yeni aday görev ekle (dedup). kaynak: 'hacim_konseyi' | 'leader_agent' | ..."""
    d = _durum()
    if _hipotez_var_mi(d, hipotez):
        return False
    d["bekleyen"].append({
        "id": f"gorev_{int(_now().timestamp())}_{len(d['bekleyen'])+1}",
        "tarih": _now().isoformat(),
        "kaynak": kaynak, "sembol": sembol,
        "hipotez": hipotez, "tetik": tetik,
        "meta": meta or {}, "durum": "bekliyor",
    })
    _durum_yaz(d)
    return True


def konsey_gorev_uret(gun_ozeti: dict) -> int:
    """
    Gün sonu konsey özetinden ADAY görev üret: bir sembolde gün boyu GÜÇLÜ + KALICI
    (yeterli örnekli) yön konsensüsü → 'bunu OAR fib×hacim bağlamında backtest et' görevi.
    Kanıt değil ADAY; PC backtest + serap testi karar verir.
    """
    n = 0
    for sembol, o in (gun_ozeti or {}).items():
        if o.get("snapshot_sayi", 0) < MIN_SNAPSHOT:
            continue
        yon = o.get("baskin_yon")
        mut = o.get("mutabakat_ort", 0.0)
        net = o.get("net_ort", 0.0)
        if yon in ("LONG", "SHORT") and mut >= MUTABAKAT_ESIK:
            gun = _now().strftime("%Y-%m-%d")
            hipotez = (f"{sembol} {gun}: gün boyu {yon} hacim konsensüsü "
                       f"(mutabakat %{mut*100:.0f}, net {net:+.2f}) — Asia fib×hacim "
                       f"bağlamında bu yön kârlı mı? backtest+serap test et.")
            tetik = f"hacim_konsey_gun_konsensus:{yon}:mut{mut:.2f}"
            if gorev_ekle("hacim_konseyi", sembol, hipotez, tetik,
                          meta={"gun_ozeti": o, "gun": gun}):
                n += 1
    return n


def _agent_tasks_drenaj() -> int:
    """
    leader_agent'in write-only `agent_tasks.json` bekleyen hipotezlerini bu worker
    kuyruğuna AKTAR (mimari §3 açığı) + leader'da 'tamamlanan'a taşı → bekleyen sayısı düşsün.
    """
    try:
        from leader_agent import TASKS_FILE
    except Exception:
        return 0
    tf = _load(TASKS_FILE, {"tasks": [], "tamamlanan": []})
    tasks = tf.get("tasks", [])
    if not tasks:
        return 0
    n = 0
    kalan = []
    for t in tasks:
        hip = t.get("hipotez") or t.get("bulgu") or ""
        if not hip:
            kalan.append(t)
            continue
        sembol = t.get("bot", "TÜM")
        if gorev_ekle("leader_agent", str(sembol), hip,
                      tetik=t.get("test_yontemi", ""), meta={"kaynak_task": t}):
            n += 1
            t["durum"] = "worker_kuyruguna_aktarildi"
            t["aktarim_tarih"] = _now().isoformat()
            tf.setdefault("tamamlanan", []).append(t)
        else:
            # zaten kuyruktaysa yine leader'dan düşür (tekrar drenajı önle)
            t["durum"] = "worker_kuyrugunda_mevcut"
            tf.setdefault("tamamlanan", []).append(t)
    tf["tasks"] = kalan
    _save(TASKS_FILE, tf)
    return n


def _yerel_kuyruga_yonlendir() -> int:
    """Bekleyen görevleri git-senkron yerel kuyruğa yaz (PC vardiya okur) → yerel_kuyrukta'ya taşı."""
    d = _durum()
    if not d["bekleyen"]:
        return 0
    yk = _load(YEREL_KUYRUK, {"aciklama":
              "Hacim Konseyi worker'ının PC'ye yönlendirdiği ADAY backtest görevleri. "
              "Kullanıcı `git pull` → vardiya/hipotez motoru backtest eder → sonucu "
              "hacim_gorev_sonuc.json'a yazıp push eder. Kanıt ancak serap testi (DSR≥0.95) ile.",
              "gorevler": []})
    mevcut_id = {g.get("id") for g in yk.get("gorevler", [])}
    n = 0
    for g in d["bekleyen"]:
        if g["id"] not in mevcut_id:
            yk["gorevler"].append(g)
            n += 1
        g["durum"] = "yerel_kuyrukta"
        g["yonlendirme_tarih"] = _now().isoformat()
        d["yerel_kuyrukta"].append(g)
    yk["gorevler"] = yk["gorevler"][-200:]     # son 200 görev
    d["bekleyen"] = []
    _save(YEREL_KUYRUK, yk)
    _durum_yaz(d)
    return n


def _tamamlanma_kontrol() -> int:
    """
    Yerel-kuyruktaki görevlerin TAMAMLANMASINI sapta: PC, backtest sonucunu görev id ile
    hacim_gorev_sonuc.json'a yazıp push ettiyse → 'tamamlanan'a taşı. (Yalnız kesin PC
    sonucu sayılır — sembol-benzerliğiyle kaba eşleşme yanlış-pozitif üretir, kullanılmaz.)
    """
    d = _durum()
    if not d["yerel_kuyrukta"]:
        return 0
    sonuc = _load(SONUC_FILE, {"sonuclar": []})
    sonuc_idler = {s.get("id"): s for s in sonuc.get("sonuclar", [])}
    kalan, tamam = [], 0
    for g in d["yerel_kuyrukta"]:
        if g["id"] in sonuc_idler:
            g["durum"] = "tamamlandi"
            g["tamamlanma_tarih"] = _now().isoformat()
            g["tamamlanma"] = {"tip": "pc_sonuc", "sonuc": sonuc_idler[g["id"]]}
            d["tamamlanan"].append(g)
            tamam += 1
        else:
            kalan.append(g)
    d["yerel_kuyrukta"] = kalan
    d["tamamlanan"] = d["tamamlanan"][-200:]
    _durum_yaz(d)
    return tamam


async def worker_dongu():
    """Görev worker'ı: drenaj → yerel kuyruğa yönlendir → tamamlanma takip. main.py startup."""
    await asyncio.sleep(90)   # startup + ilk snapshot geçsin
    while True:
        try:
            dr = _agent_tasks_drenaj()
            yn = _yerel_kuyruga_yonlendir()
            tm = _tamamlanma_kontrol()
            if dr or yn or tm:
                print(f"[hacim_gorev] drenaj {dr} · yönlendirme {yn} · tamamlanan {tm}", flush=True)
        except Exception as e:
            print(f"[hacim_gorev] worker hata: {e}", flush=True)
        await asyncio.sleep(WORKER_ARALIK_S)


def baglam_metni() -> str:
    """Lider bağlamı: görev kuyruğu durumu (bekleyen/yönlendirilen/tamamlanan)."""
    d = _durum()
    b, y, t = len(d["bekleyen"]), len(d["yerel_kuyrukta"]), len(d["tamamlanan"])
    L = [f"HACİM GÖREV WORKER: bekleyen {b} · PC-kuyruğunda {y} · tamamlanan {t}."]
    for g in d["yerel_kuyrukta"][-3:]:
        L.append(f"  → PC'de bekliyor: {g.get('hipotez','')[:110]}")
    for g in d["tamamlanan"][-2:]:
        L.append(f"  ✓ tamamlandı: {g.get('hipotez','')[:90]}")
    return "\n".join(L)


def durum() -> dict:
    """Endpoint/teşhis."""
    d = _durum()
    yk = _load(YEREL_KUYRUK, {"gorevler": []})
    return {"bekleyen": len(d["bekleyen"]), "yerel_kuyrukta": len(d["yerel_kuyrukta"]),
            "tamamlanan": len(d["tamamlanan"]), "yerel_kuyruk_toplam": len(yk.get("gorevler", [])),
            "son_yerel_kuyruk": yk.get("gorevler", [])[-5:], "baglam": baglam_metni()}


if __name__ == "__main__":
    async def _t():
        gorev_ekle("test", "BTCUSDT", "TEST hipotez: BTC gün boyu LONG konsensüsü backtest.", "test")
        print("drenaj:", _agent_tasks_drenaj())
        print("yönlendirme:", _yerel_kuyruga_yonlendir())
        print("tamamlanma:", _tamamlanma_kontrol())
        print(json.dumps(durum(), ensure_ascii=False, indent=2))
    asyncio.run(_t())
