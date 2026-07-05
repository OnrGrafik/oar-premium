"""
backtest_sonuclari.py — OAR Tüm Backtest/Hipotez Sonuç Defteri (paylaşılan, git-senkron)
═══════════════════════════════════════════════════════════════════════════════════════
AMAÇ (kullanıcı isteği): Test edilen HER hipotez/backtest sonucunu (kazanan VE elenen)
kalıcı bir deftere yaz. Böylece ajanlar:
  • Sonucu BELLİ olan şeyi tekrar tekrar backtest edip aynı hipotezi üretmesin (dedup),
  • Geçmiş sonuçları yeni backtest/hipotezlerine EK BİLGİ (prior) olarak kullansın.

kanitli_bulgular.py = yalnız KAZANANLAR (canlıya bağlanacak confirm adayları).
backtest_sonuclari.py = HER ŞEY (kazanan + elenen + marjinal) — kurumsal hafıza / bilgi bankası.

Depo REPO KÖKÜNDE JSON (git ile senkron): lokal motor yazar → commit/push → Railway okur.
Kayıt = {ad, wr, taban, lift, wilson_alt, oos_wr, n, karar, kaynak, aralik, tarih}.
karar ∈ {kazanan, marjinal, elendi}.  (yetersiz örneklem zaten deftere girmez.)
"""
import json
from pathlib import Path
from datetime import datetime, timezone

DEFTER = Path(__file__).resolve().parent / "backtest_sonuclari.json"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _yukle() -> dict:
    try:
        return json.loads(DEFTER.read_text()) if DEFTER.exists() else {"kayitlar": {}, "guncelleme": None}
    except Exception:
        return {"kayitlar": {}, "guncelleme": None}


def _kaydet(d: dict):
    try:
        DEFTER.write_text(json.dumps(d, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _karar(lift, wilson_alt, taban) -> str:
    if lift is None or taban is None:
        return "belirsiz"
    if lift >= 5.0 and wilson_alt is not None and wilson_alt > taban:
        return "kazanan"          # kanıtlı edge
    if lift >= 2.0:
        return "marjinal"         # zayıf sinyal, kanıt yetersiz
    return "elendi"               # edge yok / negatif


def kaydet_toplu(sonuclar: list, kaynak: str = "hipotez_motoru", aralik: str = "") -> dict:
    """
    arastir()'ın döndürdüğü 'tum' listesini deftere upsert eder (ad anahtar).
    Döner: {yeni, guncel, toplam} sayaçları.
    """
    d = _yukle()
    kayitlar = d["kayitlar"]
    yeni = guncel = 0
    for r in sonuclar:
        ad = r.get("ad")
        if not ad:
            continue
        karar = _karar(r.get("lift"), r.get("wilson_alt"), r.get("taban"))
        rec = {"wr": r.get("wr"), "taban": r.get("taban"), "lift": r.get("lift"),
               "wilson_alt": r.get("wilson_alt"), "oos_wr": r.get("oos_wr"),
               "n": r.get("n"), "karar": karar, "kaynak": kaynak,
               "aralik": aralik, "tarih": _now()}
        if ad in kayitlar:
            guncel += 1
        else:
            yeni += 1
        kayitlar[ad] = rec
    d["guncelleme"] = _now()
    _kaydet(d)
    return {"yeni": yeni, "guncel": guncel, "toplam": len(kayitlar)}


def test_edildi_mi(ad: str) -> dict | None:
    """Bu hipotez daha önce test edildi mi? Kaydını (sonucuyla) döner, yoksa None."""
    return _yukle()["kayitlar"].get(ad)


def elenenler() -> list:
    """Test edilip EDGE ÇIKMAYAN hipotezler — ajanlar bunları tekrar kovalamasın."""
    return [ad for ad, r in _yukle()["kayitlar"].items() if r.get("karar") == "elendi"]


def kazananlar() -> list:
    return [ad for ad, r in _yukle()["kayitlar"].items() if r.get("karar") == "kazanan"]


def ozet(n: int = 6) -> str:
    """Ajan bağlamı için defter özeti: sayımlar + en iyi birkaç sonuç."""
    d = _yukle()
    kayitlar = d["kayitlar"]
    if not kayitlar:
        return "Backtest defteri boş."
    say = {"kazanan": 0, "marjinal": 0, "elendi": 0, "belirsiz": 0}
    for r in kayitlar.values():
        say[r.get("karar", "belirsiz")] = say.get(r.get("karar", "belirsiz"), 0) + 1
    sirali = sorted(kayitlar.items(), key=lambda kv: (kv[1].get("lift") or -99), reverse=True)
    satir = [f"OAR BACKTEST DEFTERİ ({len(kayitlar)} test: "
             f"{say['kazanan']} kazanan · {say['marjinal']} marjinal · {say['elendi']} elendi). "
             f"Aynı sonucu tekrar test etme; ek bilgi olarak kullan. En iyiler:"]
    for ad, r in sirali[:n]:
        satir.append(f"  • [{r.get('karar')}] {ad}: LIFT {r.get('lift')} (WR%{r.get('wr')}, n{r.get('n')})")
    return "\n".join(satir)
