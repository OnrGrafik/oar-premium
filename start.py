#!/usr/bin/env python3
"""
Crypto AI Agent (Gemini) - Kurulum & Başlatma
"""
import os, sys, subprocess, threading, time, webbrowser, shutil
from pathlib import Path

def run(cmd):
    subprocess.run(cmd, shell=True, check=True)

def find_all_data_folders():
    """Masaüstü ve İndirilenler'deki TÜM eski versiyonların data klasörlerini bul"""
    home = Path(os.path.expanduser("~"))
    current = Path(__file__).parent.resolve()
    found = []
    for base in [home / "Desktop", home / "Downloads", home / "Masaüstü", home / "İndirilenler"]:
        if not base.exists():
            continue
        for folder in base.iterdir():
            if not folder.is_dir():
                continue
            if "crypto-agent" not in folder.name.lower() and "oar" not in folder.name.lower():
                continue
            for marker in folder.rglob("data"):
                if marker.is_dir() and marker.parent != current and marker not in found:
                    # içinde json var mı?
                    if any(marker.glob("*.json")):
                        found.append(marker)
    return found

def merge_conversations(old_data_dirs, current_data):
    """Tüm eski conversations.json dosyalarını mevcutla BİRLEŞTİR (kayıpsız)"""
    import json as _json
    cur_file = current_data / "conversations.json"
    merged = {"exchanges": [], "next_id": 1}
    if cur_file.exists():
        try:
            merged = _json.loads(cur_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    seen = {(e.get("user",""), e.get("timestamp","")) for e in merged["exchanges"]}
    added = 0
    for d in old_data_dirs:
        f = d / "conversations.json"
        if not f.exists():
            continue
        try:
            old = _json.loads(f.read_text(encoding="utf-8"))
            for e in old.get("exchanges", []):
                key = (e.get("user",""), e.get("timestamp",""))
                if key not in seen:
                    merged["exchanges"].append(e)
                    seen.add(key)
                    added += 1
        except Exception:
            continue
    # zamana göre sırala, id'leri yenile
    merged["exchanges"].sort(key=lambda e: e.get("timestamp",""))
    for i, e in enumerate(merged["exchanges"], 1):
        e["id"] = i
    merged["next_id"] = len(merged["exchanges"]) + 1
    cur_file.write_text(_json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return added, len(merged["exchanges"])

def merge_knowledge(old_data_dirs, current_data):
    """Tüm eski knowledge.json dosyalarını birleştir"""
    import json as _json
    cur_file = current_data / "knowledge.json"
    merged = {"documents": [], "notes": [], "next_id": 1}
    if cur_file.exists():
        try:
            merged = _json.loads(cur_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    seen_notes = {n.get("content","") for n in merged["notes"]}
    seen_docs  = {(d.get("title",""), d.get("chunk_idx",0)) for d in merged["documents"]}
    added = 0
    for d in old_data_dirs:
        f = d / "knowledge.json"
        if not f.exists():
            continue
        try:
            old = _json.loads(f.read_text(encoding="utf-8"))
            for n in old.get("notes", []):
                if n.get("content","") not in seen_notes:
                    merged["notes"].append(n); seen_notes.add(n.get("content","")); added += 1
            for doc in old.get("documents", []):
                key = (doc.get("title",""), doc.get("chunk_idx",0))
                if key not in seen_docs:
                    merged["documents"].append(doc); seen_docs.add(key); added += 1
        except Exception:
            continue
    nid = 1
    for e in merged["documents"] + merged["notes"]:
        e["id"] = nid; nid += 1
    merged["next_id"] = nid
    cur_file.write_text(_json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return added

def main():
    print("=" * 56)
    print("  🚀 CRYPTO AI AGENT — Google Gemini")
    print("=" * 56)

    if sys.version_info < (3, 9):
        print("❌ Python 3.9+ gerekli!"); sys.exit(1)
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor}")

    print("\n📦 Kütüphaneler kuruluyor...")
    run(f"{sys.executable} -m pip install -r requirements.txt -q")
    print("✅ Kütüphaneler hazır")

    # API Key
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key and os.path.exists(".env"):
        for line in open(".env"):
            if line.startswith("GEMINI_API_KEY="):
                api_key = line.split("=",1)[1].strip().strip('"').strip("'")
                os.environ["GEMINI_API_KEY"] = api_key

    IS_RENDER = os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID")
    if not api_key:
        if IS_RENDER:
            print("❌ GEMINI_API_KEY Render Environment Variables'a eklenmeli!")
            sys.exit(1)
        print("\n" + "─"*56)
        print("  ⚠️  GOOGLE GEMINI API KEY GEREKLİ")
        print("  → https://aistudio.google.com/apikey")
        print("─"*56)
        api_key = input("  API Key: ").strip()
        if not api_key:
            print("❌ API Key girilmedi."); sys.exit(1)
        os.environ["GEMINI_API_KEY"] = api_key
        with open(".env","w") as f:
            f.write(f'GEMINI_API_KEY="{api_key}"\n')
        print("  ✅ .env dosyasına kaydedildi")
    else:
        print(f"✅ Gemini API Key: {api_key[:12]}...")

    # ── Opsiyonel Groq yedek API ──
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key and os.path.exists(".env"):
        for line in open(".env"):
            if line.startswith("GROQ_API_KEY="):
                groq_key = line.split("=",1)[1].strip().strip('"').strip("'")
                os.environ["GROQ_API_KEY"] = groq_key
    if groq_key:
        print(f"✅ Groq Yedek API: {groq_key[:10]}... (Gemini yoğunken devreye girer)")
    elif not IS_RENDER:
        print("\n💡 İsteğe bağlı: Gemini yoğunken yedek olarak Groq kullanılabilir (ücretsiz)")
        print("   → https://console.groq.com/keys adresinden key alın")
        groq_key = input("   Groq API Key (boş bırakabilirsiniz): ").strip()
        if groq_key:
            os.environ["GROQ_API_KEY"] = groq_key
            with open(".env","a") as f:
                f.write(f'GROQ_API_KEY="{groq_key}"\n')
            print("   ✅ Groq yedek kaydedildi")

    # ── HAFIZA BİRLEŞTİRME (tüm eski versiyonlardan kayıpsız) ──
    # Render persistent disk varsa onu kullan
    _render_disk = Path("/var/data")
    current_data = _render_disk if _render_disk.exists() else Path("data")
    current_data.mkdir(exist_ok=True)
    os.environ["DATA_DIR"] = str(current_data)
    old_dirs = find_all_data_folders()
    if old_dirs:
        print(f"\n📂 {len(old_dirs)} eski hafıza klasörü bulundu")
        conv_added, conv_total = merge_conversations(old_dirs, current_data)
        kb_added = merge_knowledge(old_dirs, current_data)
        if conv_added or kb_added:
            print(f"   ✅ Birleştirildi: +{conv_added} konuşma, +{kb_added} not/doküman")
        print(f"   📊 Toplam hafıza: {conv_total} konuşma")
        # signals/alerts gibi diğer dosyaları da kopyala (yoksa)
        for d in old_dirs:
            for item in d.iterdir():
                if item.name in ("conversations.json", "knowledge.json"):
                    continue
                dest = current_data / item.name
                if not dest.exists():
                    try:
                        if item.is_dir():
                            shutil.copytree(item, dest)
                        else:
                            shutil.copy2(item, dest)
                    except Exception:
                        pass
    else:
        import json
        cf = current_data / "conversations.json"
        if cf.exists():
            n = len(json.loads(cf.read_text(encoding="utf-8")).get("exchanges", []))
            print(f"✅ Hafıza: {n} konuşma kayıtlı")

    print("\n" + "="*56)
    print("  ✅ HAZIR!")
    print("  🌐 Adres : http://localhost:8000")
    print("  ⏹  Durmak: CTRL+C")
    print("="*56 + "\n")

    if not IS_RENDER:
        threading.Thread(target=lambda: [time.sleep(2), webbrowser.open("http://localhost:8000")], daemon=True).start()
    port = os.environ.get("PORT", "8000")
    reload_flag = "" if IS_RENDER else "--reload"
    run(f"{sys.executable} -m uvicorn main:app --host 0.0.0.0 --port {port} {reload_flag}")

if __name__ == "__main__":
    main()
