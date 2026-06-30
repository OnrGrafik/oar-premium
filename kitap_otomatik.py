"""
Kitap Otomatik Yükleyici — OAR Premium
═══════════════════════════════════════════════════════════════════════════════
Startup'ta kaynak klasör(ler)deki PDF/TXT/MD kitapları otomatik tarar, parçalar
ve kitap veritabanına (FTS5) yükler. Böylece "Henüz kitap yüklenmemiş" yerine
kitaplar bilgisayar/uygulama açılır açılmaz görünür.

Kaynak klasörler (varsa taranır):
  • ./kitaplar_kaynak                     (proje içi — commit'lenebilir)
  • $DATA_DIR/kitaplar_kaynak             (Railway volume — kalıcı)
Zaten DB'de olan başlıklar atlanır (tekrar yüklenmez).
"""
import asyncio
from pathlib import Path


def _kaynak_klasorler():
    import os
    kl = [Path("kitaplar_kaynak")]
    # Env ile elle yol göster (örn: C:\Users\ONURKLNC\Desktop\Data\kitaplar)
    env_yol = os.environ.get("KITAP_KAYNAK_DIR", "").strip()
    if env_yol:
        kl.append(Path(env_yol))
    try:
        from data_ingest import hist_dir
        kl.append(hist_dir() / "kitaplar_kaynak")
    except Exception:
        pass
    return [k for k in kl if k.exists() and k.is_dir()]


def _mevcut_basliklar() -> set:
    try:
        from kitap_db import istatistik
        return {k["title"] for k in istatistik().get("kitaplar", [])}
    except Exception:
        return set()


def tara_ve_yukle() -> dict:
    """Senkron tarama+yükleme (startup thread'inde çağrılır). Döner: özet."""
    from kitap_hazirla import _pdf_metin, _txt_metin, _temizle, _parcala
    from kitap_db import init_db, import_chunks
    from datetime import datetime, timezone

    init_db()
    mevcut = _mevcut_basliklar()
    klasorler = _kaynak_klasorler()
    if not klasorler:
        return {"durum": "kaynak_klasor_yok", "yuklenen": 0,
                "ipucu": "Kitapları ./kitaplar_kaynak veya $DATA_DIR/kitaplar_kaynak içine koy."}

    yeni_kitap = 0
    toplam_parca = 0
    atlanan = 0
    for klasor in klasorler:
        # ÖZYİNELEMELİ tara (alt klasörler dahil); kategori = bulunduğu alt klasör adı
        for p in sorted(klasor.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in (".pdf", ".txt", ".md"):
                continue
            baslik = p.stem
            # kategori: dosyanın bulunduğu alt klasör adı (kök ise 'genel')
            kategori = p.parent.name if p.parent != klasor else "genel"
            if baslik in mevcut:
                atlanan += 1
                continue
            try:
                ham = _pdf_metin(p) if p.suffix.lower() == ".pdf" else _txt_metin(p)
                metin = _temizle(ham)
                if len(metin) < 100:
                    continue
                parcalar = _parcala(metin)
                docs = [{"title": baslik, "category": kategori, "chunk_idx": i,
                         "content": parca,
                         "added_at": datetime.now(timezone.utc).isoformat()}
                        for i, parca in enumerate(parcalar)]
                import_chunks(docs)
                mevcut.add(baslik)
                yeni_kitap += 1
                toplam_parca += len(parcalar)
                print(f"[Kitap] otomatik yüklendi: [{kategori}] {baslik} ({len(parcalar)} parça)")
            except Exception as e:
                print(f"[Kitap] {p.name} yüklenemedi: {str(e)[:60]}")
    return {"durum": "tamam", "yuklenen": yeni_kitap, "parca": toplam_parca,
            "atlanan_mevcut": atlanan, "klasorler": [str(k) for k in klasorler]}


async def otomatik_yukle():
    """Startup'ta çağrılır — bloklamamak için thread'de çalıştırır."""
    try:
        ozet = await asyncio.to_thread(tara_ve_yukle)
        print(f"[Kitap] otomatik yükleme: {ozet}")
    except Exception as e:
        print(f"[Kitap] otomatik yükleme hatası: {str(e)[:80]}")


if __name__ == "__main__":
    print(tara_ve_yukle())
