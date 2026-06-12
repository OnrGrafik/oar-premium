"""
Kalıcı Hafıza & Bilgi Bankası Modülü
- Kullanıcının öğrettiği her şeyi kalıcı saklar
- PDF/TXT/kitap içeriklerini indeksler
- Soru sorulduğunda ilgili bilgiyi bulur (semantik benzeri arama)
"""

import json
import re
from pathlib import Path
from datetime import datetime, timezone

# ─── Klasörler ────────────────────────────────────────────────────────────────
DATA_DIR     = Path(__import__("os").environ.get("DATA_DIR", "data"))
MEMORY_DIR   = DATA_DIR / "memory"
KNOWLEDGE_DIR= DATA_DIR / "knowledge"
DATA_DIR.mkdir(exist_ok=True)
MEMORY_DIR.mkdir(exist_ok=True)
KNOWLEDGE_DIR.mkdir(exist_ok=True)

MEMORIES_FILE   = MEMORY_DIR / "memories.json"
KNOWLEDGE_INDEX = KNOWLEDGE_DIR / "index.json"
NOTES_FILE      = MEMORY_DIR / "notes.json"

def _load(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ─── Basit Anahtar Kelime Çıkarma ────────────────────────────────────────────
def extract_keywords(text: str) -> list:
    """Metinden önemli anahtar kelimeleri çıkar"""
    # Kısa kelimeleri ve yaygın kelimeleri atla
    stop_words = {"ve","veya","bir","bu","şu","o","da","de","ki","için","ile","gibi",
                  "ama","fakat","ancak","çünkü","eğer","ise","ya","hem","ne","mi",
                  "the","a","an","is","are","was","were","be","been","have","has",
                  "do","does","did","will","would","could","should","may","might",
                  "i","you","he","she","it","we","they","and","or","but","in","on",
                  "at","to","for","of","with","by","from","as","into","through"}
    
    words = re.findall(r'\b\w{3,}\b', text.lower())
    keywords = [w for w in words if w not in stop_words]
    # Frekansa göre sırala, en önemli 20'yi al
    freq = {}
    for w in keywords:
        freq[w] = freq.get(w, 0) + 1
    return sorted(freq.keys(), key=lambda x: freq[x], reverse=True)[:20]

def text_similarity(query: str, text: str) -> float:
    """İki metin arasındaki basit benzerlik skoru"""
    q_words = set(extract_keywords(query))
    t_words = set(extract_keywords(text))
    if not q_words or not t_words:
        return 0.0
    intersection = q_words & t_words
    union = q_words | t_words
    return len(intersection) / len(union) if union else 0.0

# ─── Hafıza (Öğretilen Bilgiler) ────────────────────────────────────────────
def add_memory(content: str, category: str = "genel", tags: list = None) -> dict:
    """Yeni bir bilgi/hafıza ekle"""
    memories = _load(MEMORIES_FILE, [])
    
    memory = {
        "id":         len(memories) + 1,
        "content":    content,
        "category":   category,
        "tags":       tags or extract_keywords(content)[:8],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "access_count": 0,
    }
    memories.append(memory)
    # Son 2000 hafıza tut
    memories = memories[-2000:]
    _save(MEMORIES_FILE, memories)
    return {"status": "ok", "id": memory["id"], "total": len(memories)}

def search_memories(query: str, top_k: int = 5, category: str = None) -> list:
    """Sorguya en yakın hafızaları bul"""
    memories = _load(MEMORIES_FILE, [])
    if not memories:
        return []
    
    scored = []
    for m in memories:
        if category and m.get("category") != category:
            continue
        score = text_similarity(query, m["content"])
        # Tag eşleşmesi bonus
        q_words = set(extract_keywords(query))
        tag_matches = len(q_words & set(m.get("tags", [])))
        score += tag_matches * 0.1
        if score > 0:
            scored.append((score, m))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, m in scored[:top_k]:
        m["relevance_score"] = round(score, 3)
        m["access_count"] = m.get("access_count", 0) + 1
        results.append(m)
    
    # Erişim sayısını güncelle
    if results:
        ids = {r["id"] for r in results}
        for m in memories:
            if m["id"] in ids:
                m["access_count"] = m.get("access_count", 0) + 1
        _save(MEMORIES_FILE, memories)
    
    return results

def get_all_memories(category: str = None) -> list:
    memories = _load(MEMORIES_FILE, [])
    if category:
        return [m for m in memories if m.get("category") == category]
    return memories

def delete_memory(memory_id: int) -> dict:
    memories = _load(MEMORIES_FILE, [])
    before = len(memories)
    memories = [m for m in memories if m["id"] != memory_id]
    _save(MEMORIES_FILE, memories)
    return {"deleted": before - len(memories)}

def get_memory_stats() -> dict:
    memories = _load(MEMORIES_FILE, [])
    categories = {}
    for m in memories:
        cat = m.get("category", "genel")
        categories[cat] = categories.get(cat, 0) + 1
    return {
        "total": len(memories),
        "categories": categories,
        "most_accessed": sorted(memories, key=lambda x: x.get("access_count",0), reverse=True)[:3]
    }

# ─── Bilgi Bankası (Dosya İçerikleri) ───────────────────────────────────────
def index_document(filename: str, content: str, doc_type: str = "text") -> dict:
    """Belgeyi parçalara böl ve indeksle"""
    index = _load(KNOWLEDGE_INDEX, {"documents": [], "chunks": []})
    
    # Mevcut belgeyi güncelle (aynı isimde varsa sil)
    index["documents"] = [d for d in index["documents"] if d["filename"] != filename]
    index["chunks"] = [c for c in index["chunks"] if c["source"] != filename]
    
    # Belgeyi ~500 kelimelik parçalara böl
    words = content.split()
    chunk_size = 500
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk_text = " ".join(words[i:i+chunk_size])
        chunks.append({
            "id":       f"{filename}_{i//chunk_size}",
            "source":   filename,
            "chunk_idx": i // chunk_size,
            "content":  chunk_text,
            "keywords": extract_keywords(chunk_text),
        })
    
    doc_entry = {
        "filename":   filename,
        "doc_type":   doc_type,
        "char_count": len(content),
        "chunk_count": len(chunks),
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "summary":    content[:300] + "..." if len(content) > 300 else content,
    }
    
    index["documents"].append(doc_entry)
    index["chunks"].extend(chunks)
    
    # Fazla chunk temizle (max 5000)
    index["chunks"] = index["chunks"][-5000:]
    _save(KNOWLEDGE_INDEX, index)
    
    # Belgeyi data/knowledge klasörüne de kaydet
    doc_path = KNOWLEDGE_DIR / f"{filename}.txt"
    doc_path.write_text(content, encoding="utf-8")
    
    return {"status": "ok", "filename": filename,
            "chunks": len(chunks), "chars": len(content)}

def search_knowledge(query: str, top_k: int = 5) -> list:
    """Bilgi bankasında ara"""
    index = _load(KNOWLEDGE_INDEX, {"documents": [], "chunks": []})
    if not index["chunks"]:
        return []
    
    scored = []
    for chunk in index["chunks"]:
        score = text_similarity(query, chunk["content"])
        kw_matches = len(set(extract_keywords(query)) & set(chunk.get("keywords", [])))
        score += kw_matches * 0.08
        if score > 0.05:
            scored.append((score, chunk))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"score": round(s, 3), **c} for s, c in scored[:top_k]]

def get_knowledge_list() -> list:
    index = _load(KNOWLEDGE_INDEX, {"documents": [], "chunks": []})
    return index.get("documents", [])

def delete_document(filename: str) -> dict:
    index = _load(KNOWLEDGE_INDEX, {"documents": [], "chunks": []})
    before_docs = len(index["documents"])
    index["documents"] = [d for d in index["documents"] if d["filename"] != filename]
    index["chunks"]    = [c for c in index["chunks"]    if c["source"]   != filename]
    _save(KNOWLEDGE_INDEX, index)
    doc_path = KNOWLEDGE_DIR / f"{filename}.txt"
    if doc_path.exists():
        doc_path.unlink()
    return {"deleted_docs": before_docs - len(index["documents"])}

# ─── Notlar ──────────────────────────────────────────────────────────────────
def add_note(title: str, content: str, tags: list = None) -> dict:
    notes = _load(NOTES_FILE, [])
    note = {
        "id":         len(notes) + 1,
        "title":      title,
        "content":    content,
        "tags":       tags or [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    notes.append(note)
    _save(NOTES_FILE, notes)
    # Notu hafızaya da ekle
    add_memory(f"{title}: {content}", category="not", tags=tags)
    return {"status": "ok", "id": note["id"]}

def get_notes() -> list:
    return _load(NOTES_FILE, [])

# ─── Bağlam Oluşturucu ───────────────────────────────────────────────────────
def build_context_for_query(query: str) -> str:
    """Sorgu için ilgili hafıza ve bilgi bankasını derle"""
    context_parts = []
    
    # Hafızadan ara
    memories = search_memories(query, top_k=4)
    if memories:
        context_parts.append("━━━ HAFIZADAN İLGİLİ BİLGİLER ━━━")
        for m in memories:
            cat = m.get("category", "genel")
            context_parts.append(f"[{cat.upper()}] {m['content'][:400]}")
        context_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    # Bilgi bankasından ara
    knowledge = search_knowledge(query, top_k=3)
    if knowledge:
        context_parts.append("━━━ BİLGİ BANKASI ━━━")
        for k in knowledge:
            context_parts.append(f"[{k['source']}] {k['content'][:500]}")
        context_parts.append("━━━━━━━━━━━━━━━━━━━━━")
    
    return "\n".join(context_parts) if context_parts else ""

# ─── Otomatik Hafıza Tespiti ─────────────────────────────────────────────────
def detect_learn_intent(message: str) -> tuple:
    """Kullanıcı bir şey öğretmek istiyor mu?"""
    msg_lower = message.lower()
    
    learn_phrases = [
        "bunu öğren", "bunu hatırla", "bunu bil", "not al",
        "kaydet bunu", "aklında tut", "unutma",
        "strateji:", "kural:", "önemli:", "not:",
        "sana söyleyeyim", "bilmeni istiyorum",
        "learn this", "remember this", "note:",
    ]
    
    for phrase in learn_phrases:
        if phrase in msg_lower:
            # Kategori tespit et
            if any(k in msg_lower for k in ["kripto","btc","eth","trading","borsa","opsiyon","teknik"]):
                category = "kripto"
            elif any(k in msg_lower for k in ["kod","python","javascript","bot","script"]):
                category = "kod"
            elif any(k in msg_lower for k in ["strateji","kural","sistem","plan"]):
                category = "strateji"
            else:
                category = "genel"
            return True, category
    
    return False, "genel"
