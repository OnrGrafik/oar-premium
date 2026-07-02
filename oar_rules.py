"""
OAR Kural Bankası — Agent Eğitim Modülü
═══════════════════════════════════════════════════════════════════
Kullanıcının agentlara öğrettiği kuralları saklar.
- Metin kuralları: /var/data/oar_rules/rules.json
- Görseller:       /var/data/oar_rules/images/
- Her kural agent promptuna otomatik eklenir

Kural tipleri:
  SETUP    — "Şu formasyonda bu fib'ten gir"
  FILTRE   — "Şu koşulda işlem alma"
  SWING    — "Swing taşıma kriteri"
  MAKRO    — "Makro bağlamda bu kurala bak"
  GENEL    — Genel not
"""
import os, json, base64
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR  = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or ("/var/data" if Path("/var/data").exists() else "data"))
RULES_DIR = DATA_DIR / "oar_rules"
IMG_DIR   = RULES_DIR / "images"
RULES_FILE= RULES_DIR / "rules.json"

RULES_DIR.mkdir(parents=True, exist_ok=True)
IMG_DIR.mkdir(parents=True, exist_ok=True)

TIPLER = ["SETUP","FILTRE","SWING","MAKRO","GENEL"]

def _now(): return datetime.now(timezone.utc).isoformat()
def _load():
    try: return json.loads(RULES_FILE.read_text()) if RULES_FILE.exists() else {"rules":[]}
    except: return {"rules":[]}
def _save(d): RULES_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2))

# ── KURAL EKLE ───────────────────────────────────────────
def kural_ekle(baslik: str, icerik: str, tip: str = "GENEL",
               etiketler: list = None, oncelik: int = 1,
               yazar: str = None, rol: str = None, durum: str = "ADAY") -> dict:
    """
    Yeni kural ekle.
    baslik:   Kısa başlık (örn. "2.272'den Short Koşulu")
    icerik:   Detaylı açıklama
    tip:      SETUP / FILTRE / SWING / MAKRO / GENEL
    etiketler: ["btc","fib","gex"] gibi
    oncelik:  1-5 (5 = en kritik)
    yazar/rol: RBAC — yazar/rol verildiyse (API yolu) yetki zorunlu.
               Verilmezse (dahili/seed bootstrap çağrısı) kontrol atlanır.
    durum:    "ADAY" (BEKLEMEDE — varsayılan) → walk-forward geçmeden Leader
              prompt'una (agent_baglami) DAHİL EDİLMEZ. Seed/onaylı kurallar
              "AKTIF" geçer. (Geriye uyum: durum'suz eski kurallar AKTIF sayılır.)
    Yetkisiz ekleme PermissionError fırlatır ve audit'e "reddedildi" yazar.
    """
    # RBAC — yalnız kimlikli (API) ekleme denetlenir.
    if yazar is not None or rol is not None:
        import governance
        if not governance.yetkili_mi(yazar, rol):
            governance.audit_yaz("kural_ekle", baslik, yazar, rol,
                                  sonuc="reddedildi", detay={"sebep": "yetkisiz"})
            raise PermissionError(f"Yetkisiz kural ekleme denemesi (yazar={yazar}, rol={rol})")

    db = _load()
    kural_id = f"rule_{len(db['rules'])+1:04d}"
    kural = {
        "id":       kural_id,
        "baslik":   baslik,
        "icerik":   icerik,
        "tip":      tip.upper() if tip.upper() in TIPLER else "GENEL",
        "etiketler": etiketler or [],
        "oncelik":  min(max(int(oncelik), 1), 5),
        "tarih":    _now(),
        "aktif":    True,
        "durum":    durum if durum in ("ADAY", "AKTIF") else "ADAY",
        "gorsel":   None,  # görsel varsa dosya adı
    }
    db["rules"].append(kural)
    _save(db)
    try:
        import governance
        governance.audit_yaz("kural_ekle", kural_id, yazar, rol, sonuc="ok",
                             detay={"baslik": baslik, "durum": kural["durum"]})
    except Exception:
        pass
    return kural

# ── GÖRSEL EKLE ──────────────────────────────────────────
def gorsel_ekle(kural_id: str, dosya_adi: str, veri: bytes) -> bool:
    """
    Kurala görsel ekle. veri: binary içerik (PNG/JPG/WebP).
    """
    db = _load()
    kurallar = db.get("rules", [])
    hedef = next((k for k in kurallar if k["id"] == kural_id), None)
    if not hedef:
        return False

    # Dosyayı kaydet
    uzanti = Path(dosya_adi).suffix.lower() or ".png"
    kayit_adi = f"{kural_id}{uzanti}"
    kayit_yol = IMG_DIR / kayit_adi
    kayit_yol.write_bytes(veri)

    hedef["gorsel"] = kayit_adi
    _save(db)
    return True

# ── KURAL SİL / GÜNCELLE ─────────────────────────────────
def kural_sil(kural_id: str) -> bool:
    db = _load()
    onceki = len(db["rules"])
    db["rules"] = [k for k in db["rules"] if k["id"] != kural_id]
    _save(db)
    return len(db["rules"]) < onceki

def kural_guncelle(kural_id: str, **kwargs) -> bool:
    db = _load()
    for k in db["rules"]:
        if k["id"] == kural_id:
            for key, val in kwargs.items():
                if key in k: k[key] = val
            _save(db)
            return True
    return False

# ── KURALLARI OKU ────────────────────────────────────────
def kurallari_getir(tip: str = None, aktif_only: bool = True) -> list:
    db = _load()
    kurallar = db.get("rules", [])
    if aktif_only:
        kurallar = [k for k in kurallar if k.get("aktif", True)]
    if tip:
        kurallar = [k for k in kurallar if k["tip"] == tip.upper()]
    # Önceliğe göre sırala
    return sorted(kurallar, key=lambda k: -k.get("oncelik", 1))

def kural_sayisi() -> dict:
    db = _load()
    tiplere_gore = {}
    for k in db.get("rules", []):
        t = k.get("tip", "GENEL")
        tiplere_gore[t] = tiplere_gore.get(t, 0) + 1
    return {"toplam": len(db.get("rules", [])), "tipler": tiplere_gore}

# ── AGENT PROMPT'U İÇİN BAĞLAM ───────────────────────────
def agent_baglami(max_kural: int = 10) -> str:
    """
    Leader Agent'ın prompt'una eklenecek kural özeti.
    En yüksek öncelikli kuralları döner.
    ⚠️ Yalnız durum=="AKTIF" kurallar girer; ADAY (backtest beklemede) kurallar
    SIZDIRILMAZ. (Geriye uyum: durum alanı olmayan eski kurallar AKTIF sayılır.)
    """
    kurallar = [k for k in kurallari_getir() if k.get("durum", "AKTIF") == "AKTIF"][:max_kural]
    if not kurallar:
        return ""

    satirlar = ["=== OAR KURAL BANKASI (Kullanıcı Tanımlı) ==="]
    for k in kurallar:
        gors = f" [görsel: {k['gorsel']}]" if k.get("gorsel") else ""
        satirlar.append(
            f"[{k['tip']} P{k['oncelik']}] {k['baslik']}{gors}:\n  {k['icerik']}"
        )
    return "\n".join(satirlar)

# ── BACKTEST KAPISI: ADAY → AKTIF ─────────────────────────
def aday_kurallar() -> list:
    """Henüz backtest geçmemiş (durum=ADAY) kurallar."""
    return [k for k in kurallari_getir(aktif_only=False)
            if k.get("durum", "AKTIF") == "ADAY"]


def kural_aktiflestir(kural_id: str, oos_puan: float = None,
                      rejim_uyumlu: bool = True, esik: float = 50.0,
                      yazar: str = None, rol: str = None) -> dict:
    """
    Bir ADAY kuralı AKTIF'e çevirir — YALNIZCA walk-forward OOS kapısını geçerse.

    Görev 4 (walk_forward) çıktısındaki OOS puanı + hypothesis_activation rejim
    uyumu BİRLİKTE aranır:
      - oos_puan >= esik  (OOS başarısı; in-sample DEĞİL)
      - rejim_uyumlu      (hypothesis_activation rejim-aktivasyon mantığıyla uyum)
    İkisi de sağlanırsa durum="AKTIF" olur ve agent_baglami'ye girer.

    Döner: {ok, durum, sebep}. Her sonuç audit'e yazılır.
    """
    db = _load()
    hedef = next((k for k in db["rules"] if k["id"] == kural_id), None)
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
        governance.audit_yaz("kural_aktiflestir", kural_id, yazar, rol, sonuc=sonuc,
                             detay={"oos_puan": oos_puan, "rejim_uyumlu": rejim_uyumlu})
    except Exception:
        pass
    return {"ok": gecti, "durum": hedef["durum"], "sebep": sebep}


# ── GÖRSEL BASE64 OKU (AI için) ───────────────────────────
def gorsel_base64(dosya_adi: str) -> str:
    """Görseli base64'e çevir — Gemini vision API için."""
    yol = IMG_DIR / dosya_adi
    if not yol.exists():
        return ""
    return base64.b64encode(yol.read_bytes()).decode()

# ── İSTATİSTİK ───────────────────────────────────────────
def istatistik() -> dict:
    db = _load()
    kurallar = db.get("rules", [])
    gorselli = sum(1 for k in kurallar if k.get("gorsel"))
    return {
        "toplam_kural":   len(kurallar),
        "aktif":          sum(1 for k in kurallar if k.get("aktif", True)),
        "gorselli":       gorselli,
        "tipler":         kural_sayisi()["tipler"],
        "en_yuksek_onc":  max((k.get("oncelik",1) for k in kurallar), default=0),
    }
