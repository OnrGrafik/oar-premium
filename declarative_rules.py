"""
declarative_rules.py — Declarative Kural Değerlendirme Köprüsü (YENİ — Görev 6)
═══════════════════════════════════════════════════════════════════════════════
AMAÇ: Mevcut serbest-metin kuralların (oar_rules.py) YANINDA, confidence_engine
karar motorunun ÜRETTİĞİ çıktıyı DETERMİNİSTİK değerlendirebileceği declarative
bir kural formu (entry / confirmation / stop) ekler.

⚠️ ANAYASA #6 — OVER-REACH YOK:
  - Bu modül canlı `confidence_karar()` yolunu DEĞİŞTİRMEZ; onun çıktısını (dict)
    GİRDİ olarak alır ve kuralları deterministik değerlendirir. Bir KÖPRÜdür.
  - Serbest-metin kural bankası (oar_rules) korunur; bu yapı onun yanında durur.
  - main.py canlı hattına otomatik bağlanmaz (regresyon yok); çağıran taraf
    `degerlendir_hepsi(confidence_karar_ciktisi)` ile bilinçli kullanır.

DETERMİNİZM: Aynı bağlam → aynı sonuç. Rastgelelik/zaman bağımlılığı yoktur.

GÖREV 5 UYUMU: Declarative kurallar da RBAC + audit ile eklenir ve durum="ADAY"
başlar; walk-forward (Görev 4) OOS kapısını geçmeden `aktif_kurallar()`'a girmez.

ŞEMA (JSON):
{
  "id": "dec_0001",
  "ad": "Asia ekstrem short",
  "yon": "SHORT",                       # LONG | SHORT
  "entry":        {"tum": [<kosul>...]},  # hepsi (AND) sağlanmalı
  "confirmation": {"tum": [<kosul>...]},  # hepsi (AND) sağlanmalı (opsiyonel)
  "stop": {"tip": "atr", "carpan": 1.5} | {"tip": "yuzde", "deger": 1.0},
  "durum": "ADAY"                        # ADAY | AKTIF (Görev 5 kapısı)
}
<kosul> = {"alan": "rejim.rejim", "op": ">=|<=|>|<|==|!=|in|not_in|contains", "deger": ...}
  alan: confidence_karar çıktısında nokta-yollu erişim
        (ör. "konfidans", "rejim.rejim", "agent_skorlar.orderflow.yon",
         "oy_dagilimi.LONG").
"""
import os
import json
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or ("/var/data" if Path("/var/data").exists() else "data"))
DEC_DIR = DATA_DIR / "declarative_rules"
DEC_FILE = DEC_DIR / "rules.json"
DEC_DIR.mkdir(parents=True, exist_ok=True)

GECERLI_YON = {"LONG", "SHORT"}
GECERLI_OP = {">=", "<=", ">", "<", "==", "!=", "in", "not_in", "contains"}
GECERLI_STOP_TIP = {"atr", "yuzde", "seviye"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    try:
        return json.loads(DEC_FILE.read_text()) if DEC_FILE.exists() else {"rules": []}
    except Exception:
        return {"rules": []}


def _save(d):
    DEC_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# Nokta-yollu alan erişimi + deterministik operatör değerlendirme
# ─────────────────────────────────────────────────────────────────────────────
def alan_oku(baglam: dict, yol: str):
    """'rejim.rejim' / 'agent_skorlar.orderflow.yon' → iç içe değer (yoksa None)."""
    cur = baglam
    for parca in yol.split("."):
        if isinstance(cur, dict) and parca in cur:
            cur = cur[parca]
        else:
            return None
    return cur


def _uygula(op: str, sol, deger) -> bool:
    if sol is None and op not in ("==", "!=", "not_in"):
        return False
    try:
        if op == "==":
            return sol == deger
        if op == "!=":
            return sol != deger
        if op == ">":
            return float(sol) > float(deger)
        if op == ">=":
            return float(sol) >= float(deger)
        if op == "<":
            return float(sol) < float(deger)
        if op == "<=":
            return float(sol) <= float(deger)
        if op == "in":
            return sol in deger
        if op == "not_in":
            return sol not in deger
        if op == "contains":
            return (deger in sol) if sol is not None else False
    except (TypeError, ValueError):
        return False
    return False


def _kosul_degerlendir(baglam: dict, kosul: dict) -> dict:
    sol = alan_oku(baglam, kosul.get("alan", ""))
    ok = _uygula(kosul.get("op"), sol, kosul.get("deger"))
    return {
        "alan": kosul.get("alan"),
        "op": kosul.get("op"),
        "beklenen": kosul.get("deger"),
        "gercek": sol,
        "ok": ok,
    }


def _grup_degerlendir(baglam: dict, grup: dict) -> tuple[bool, list]:
    """grup={'tum': [kosul...]} → hepsi (AND). Boş/None grup → True (koşulsuz)."""
    kosullar = (grup or {}).get("tum", [])
    sonuc = [_kosul_degerlendir(baglam, k) for k in kosullar]
    ok = all(s["ok"] for s in sonuc)
    return ok, sonuc


# ─────────────────────────────────────────────────────────────────────────────
# Şema doğrulama
# ─────────────────────────────────────────────────────────────────────────────
def dogrula_sema(kural: dict) -> tuple[bool, list]:
    """Declarative kural şemasını doğrula. (ok, hatalar)."""
    hatalar = []
    if not kural.get("ad"):
        hatalar.append("ad zorunlu")
    if kural.get("yon") not in GECERLI_YON:
        hatalar.append(f"yon LONG|SHORT olmalı (geldi: {kural.get('yon')})")

    for bolum in ("entry", "confirmation"):
        grup = kural.get(bolum)
        if grup is None:
            continue
        if not isinstance(grup, dict) or "tum" not in grup:
            hatalar.append(f"{bolum} {{'tum': [...]}} biçiminde olmalı")
            continue
        for i, k in enumerate(grup["tum"]):
            if not k.get("alan"):
                hatalar.append(f"{bolum}[{i}].alan zorunlu")
            if k.get("op") not in GECERLI_OP:
                hatalar.append(f"{bolum}[{i}].op geçersiz: {k.get('op')}")

    stop = kural.get("stop")
    if stop is not None:
        if not isinstance(stop, dict) or stop.get("tip") not in GECERLI_STOP_TIP:
            hatalar.append(f"stop.tip {sorted(GECERLI_STOP_TIP)} içinden olmalı")

    return (len(hatalar) == 0), hatalar


# ─────────────────────────────────────────────────────────────────────────────
# Değerlendirme (köprü çekirdeği)
# ─────────────────────────────────────────────────────────────────────────────
def degerlendir(kural: dict, baglam: dict) -> dict:
    """
    Tek declarative kuralı confidence_karar çıktısına (baglam) karşı değerlendirir.
    DETERMİNİSTİK. Döner:
      {tetik, yon_uyumu, entry_ok, confirmation_ok, stop, detay}
      tetik = entry_ok AND confirmation_ok  (saf koşul tetiklemesi)
    NOT: 'yon_uyumu' kuralın yönü ile motorun kararını karşılaştırır; tetik'i
    BLOKLAMAZ — çağıran taraf nasıl kullanacağına karar verir (over-reach yok).
    """
    entry_ok, entry_detay = _grup_degerlendir(baglam, kural.get("entry"))
    conf_ok, conf_detay = _grup_degerlendir(baglam, kural.get("confirmation"))
    tetik = entry_ok and conf_ok
    return {
        "kural_id": kural.get("id"),
        "ad": kural.get("ad"),
        "yon": kural.get("yon"),
        "tetik": tetik,
        "entry_ok": entry_ok,
        "confirmation_ok": conf_ok,
        "yon_uyumu": kural.get("yon") == baglam.get("karar"),
        "stop": kural.get("stop"),
        "detay": {"entry": entry_detay, "confirmation": conf_detay},
    }


def degerlendir_hepsi(baglam: dict, yalniz_aktif: bool = True) -> dict:
    """
    Tüm (AKTIF) declarative kuralları bağlama karşı değerlendirir.
    yalniz_aktif=True → durum=ADAY kurallar DEĞERLENDİRMEYE GİRMEZ (Görev 5 kapısı).
    """
    kurallar = aktif_kurallar() if yalniz_aktif else kurallari_getir()
    sonuc = [degerlendir(k, baglam) for k in kurallar]
    tetikleyen = [s for s in sonuc if s["tetik"]]
    return {
        "sembol": baglam.get("sembol"),
        "motor_karar": baglam.get("karar"),
        "tetikleyen_sayisi": len(tetikleyen),
        "tetikleyenler": tetikleyen,
        "tum_sonuclar": sonuc,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CRUD + Görev 5 yönetişimi (RBAC + audit + ADAY/AKTIF kapısı)
# ─────────────────────────────────────────────────────────────────────────────
def kural_ekle(kural: dict, yazar: str = None, rol: str = None,
               durum: str = "ADAY") -> dict:
    """
    Declarative kural ekle. Şema doğrulanır; geçmezse ValueError.
    RBAC: yazar/rol verildiyse yetki zorunlu (Görev 5). durum varsayılan ADAY.
    """
    ok, hatalar = dogrula_sema(kural)
    if not ok:
        raise ValueError("Şema hatası: " + "; ".join(hatalar))

    if yazar is not None or rol is not None:
        import governance
        if not governance.yetkili_mi(yazar, rol):
            try:
                governance.audit_yaz("declarative_ekle", kural.get("ad", "?"),
                                     yazar, rol, sonuc="reddedildi")
            except Exception:
                pass
            raise PermissionError(f"Yetkisiz declarative kural ekleme (yazar={yazar}, rol={rol})")

    db = _load()
    kid = f"dec_{len(db['rules'])+1:04d}"
    kayit = dict(kural)
    kayit["id"] = kid
    kayit["durum"] = durum if durum in ("ADAY", "AKTIF") else "ADAY"
    kayit["tarih"] = _now()
    db["rules"].append(kayit)
    _save(db)
    try:
        import governance
        governance.audit_yaz("declarative_ekle", kid, yazar, rol, sonuc="ok",
                             detay={"ad": kayit.get("ad"), "durum": kayit["durum"]})
    except Exception:
        pass
    return kayit


def kurallari_getir() -> list:
    return _load().get("rules", [])


def aktif_kurallar() -> list:
    """durum=="AKTIF" declarative kurallar (durum'suz eski kayıt → AKTIF sayılır)."""
    return [k for k in kurallari_getir() if k.get("durum", "AKTIF") == "AKTIF"]


def aday_kurallar() -> list:
    return [k for k in kurallari_getir() if k.get("durum", "AKTIF") == "ADAY"]


def kural_aktiflestir(kural_id: str, oos_puan: float = None,
                      rejim_uyumlu: bool = True, esik: float = 50.0,
                      yazar: str = None, rol: str = None) -> dict:
    """
    ADAY → AKTIF, yalnız walk-forward OOS kapısı + rejim uyumu birlikte geçerse
    (oar_rules.kural_aktiflestir ile aynı mantık). Sonuç audit'e yazılır.
    """
    db = _load()
    hedef = next((k for k in db["rules"] if k.get("id") == kural_id), None)
    if not hedef:
        return {"ok": False, "sebep": "kural bulunamadı"}
    gecti = (oos_puan is not None and oos_puan >= esik) and rejim_uyumlu
    if gecti:
        hedef["durum"] = "AKTIF"
        _save(db)
        sonuc, sebep = "ok", f"OOS {oos_puan} ≥ {esik} + rejim uyumlu → AKTIF"
    else:
        sebep = (f"OOS {oos_puan} < {esik}" if (oos_puan is None or oos_puan < esik)
                 else "rejim uyumsuz") + " → ADAY kalır"
        sonuc = "reddedildi"
    try:
        import governance
        governance.audit_yaz("declarative_aktiflestir", kural_id, yazar, rol,
                             sonuc=sonuc, detay={"oos_puan": oos_puan,
                                                 "rejim_uyumlu": rejim_uyumlu})
    except Exception:
        pass
    return {"ok": gecti, "durum": hedef["durum"], "sebep": sebep}


if __name__ == "__main__":
    ornek = {
        "ad": "Trend-down + orderflow short teyidi", "yon": "SHORT",
        "entry": {"tum": [
            {"alan": "rejim.rejim", "op": "in", "deger": ["TREND_DOWN", "HIGH_VOL"]},
            {"alan": "konfidans", "op": ">=", "deger": 60},
        ]},
        "confirmation": {"tum": [
            {"alan": "agent_skorlar.orderflow.yon", "op": "==", "deger": "SHORT"},
        ]},
        "stop": {"tip": "atr", "carpan": 1.5},
    }
    print("şema:", dogrula_sema(ornek))
    baglam = {"karar": "SHORT", "konfidans": 72, "rejim": {"rejim": "TREND_DOWN"},
              "agent_skorlar": {"orderflow": {"yon": "SHORT"}}}
    print(json.dumps(degerlendir(ornek, baglam), ensure_ascii=False, indent=2))
