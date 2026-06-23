#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════
  OAR Premium — Kitap Hazırlayıcı (BİLGİSAYARINDA ÇALIŞIR)
═══════════════════════════════════════════════════════════════════
Bir klasördeki PDF / TXT / MD kitapları okur, parçalara böler ve
kitap_yukle.py'nin yükleyebileceği kitap_indeksi.json dosyasını üretir.

KULLANIM:
  pip install pypdf
  python kitap_hazirla.py <kitap_klasoru>
  python kitap_hazirla.py kitaplar            # ./kitaplar klasörü
  python kitap_hazirla.py kitaplar trading     # kategori = trading

Çıktı: kitap_indeksi.json  →  sonra:  python kitap_yukle.py kitap_indeksi.json
"""
import sys
import os
import json
import re
from datetime import datetime, timezone
from pathlib import Path

CHUNK_KELIME = 400   # parça başına ~kelime (knowledge.py ile uyumlu)
OVERLAP      = 80    # parçalar arası örtüşme


def _pdf_metin(yol: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        print("   ⚠ pypdf yok — 'pip install pypdf' çalıştır. PDF atlandı.")
        return ""
    try:
        reader = PdfReader(str(yol))
        return "\n".join((s.extract_text() or "") for s in reader.pages)
    except Exception as e:
        print(f"   ⚠ PDF okunamadı ({yol.name}): {str(e)[:60]}")
        return ""


def _txt_metin(yol: Path) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return yol.read_text(encoding=enc, errors="ignore")
        except Exception:
            continue
    return ""


def _temizle(metin: str) -> str:
    metin = re.sub(r"[ \t]+", " ", metin)
    metin = re.sub(r"\n{3,}", "\n\n", metin)
    return metin.strip()


def _parcala(metin: str) -> list:
    """Metni ~CHUNK_KELIME kelimelik örtüşmeli parçalara böl."""
    kelimeler = metin.split()
    parcalar = []
    i = 0
    adim = CHUNK_KELIME - OVERLAP
    while i < len(kelimeler):
        parca = " ".join(kelimeler[i:i + CHUNK_KELIME])
        if parca.strip():
            parcalar.append(parca)
        i += adim
    return parcalar


def main():
    if len(sys.argv) < 2:
        print("KULLANIM: python kitap_hazirla.py <kitap_klasoru> [kategori]")
        print("Örnek:    python kitap_hazirla.py kitaplar trading")
        sys.exit(1)

    klasor = Path(sys.argv[1])
    kategori = sys.argv[2] if len(sys.argv) > 2 else "kitap"

    if not klasor.exists() or not klasor.is_dir():
        print(f"❌ Klasör bulunamadı: {klasor.resolve()}")
        print("   PDF/TXT kitaplarını bir klasöre koy ve o klasörü ver.")
        sys.exit(1)

    dosyalar = [p for p in sorted(klasor.iterdir())
                if p.suffix.lower() in (".pdf", ".txt", ".md")]
    if not dosyalar:
        print(f"❌ {klasor} içinde PDF/TXT/MD yok.")
        sys.exit(1)

    print(f"📂 {klasor} — {len(dosyalar)} dosya bulundu\n")
    documents = []
    kitap_sayisi = 0

    for p in dosyalar:
        print(f"📖 {p.name} ...", end=" ", flush=True)
        if p.suffix.lower() == ".pdf":
            ham = _pdf_metin(p)
        else:
            ham = _txt_metin(p)
        metin = _temizle(ham)
        if len(metin) < 100:
            print("atlandı (metin yok / taranmış PDF)")
            continue
        baslik = p.stem
        parcalar = _parcala(metin)
        for idx, parca in enumerate(parcalar):
            documents.append({
                "title":     baslik,
                "category":  kategori,
                "chunk_idx": idx,
                "content":   parca,
                "added_at":  datetime.now(timezone.utc).isoformat(),
            })
        kitap_sayisi += 1
        print(f"✓ {len(parcalar)} parça")

    if not documents:
        print("\n❌ Hiç parça üretilemedi.")
        sys.exit(1)

    cikti = {
        "kitap_sayisi": kitap_sayisi,
        "olusturuldu":  datetime.now(timezone.utc).isoformat(),
        "documents":    documents,
    }
    hedef = Path("kitap_indeksi.json")
    hedef.write_text(json.dumps(cikti, ensure_ascii=False), encoding="utf-8")

    print(f"\n{'═'*55}")
    print(f"✅ {hedef} oluşturuldu")
    print(f"   {kitap_sayisi} kitap · {len(documents)} parça")
    print(f"\n   Şimdi yükle:")
    print(f"   python kitap_yukle.py kitap_indeksi.json")
    print(f"{'═'*55}")


if __name__ == "__main__":
    main()
