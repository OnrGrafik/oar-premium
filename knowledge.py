"""
Kalıcı Bilgi Bankası (RAG - Retrieval Augmented Generation)
Doküman yükleme, not kaydetme, akıllı arama
"""

import json
import re
import math
from pathlib import Path
from datetime import datetime, timezone

# ─── Veri Dizinleri ──────────────────────────────────────────────────────────
import os as _os_k
DATA_DIR  = Path(_os_k.environ.get("DATA_DIR", "data"))
KB_DIR    = DATA_DIR / "knowledge"
KB_FILE   = DATA_DIR / "knowledge.json"
DATA_DIR.mkdir(exist_ok=True)
KB_DIR.mkdir(exist_ok=True)

def load_kb() -> dict:
    try:
        if KB_FILE.exists():
            return json.loads(KB_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"documents": [], "notes": [], "next_id": 1}

def save_kb(kb: dict):
    KB_FILE.write_text(json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8")

# ─── Metin İşleme ────────────────────────────────────────────────────────────
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list:
    """Metni örtüşen parçalara böl"""
    text   = re.sub(r'\s+', ' ', text).strip()
    words  = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = ' '.join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
        i += chunk_size - overlap
    return chunks

def simple_tokenize(text: str) -> list:
    """Basit kelime tokenizer"""
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    return [w for w in text.split() if len(w) > 2]

def tfidf_score(query_tokens: list, doc_tokens: list, all_docs_tokens: list) -> float:
    """TF-IDF benzerlik skoru"""
    if not doc_tokens or not query_tokens:
        return 0.0
    doc_set   = set(doc_tokens)
    query_set = set(query_tokens)
    common    = query_set & doc_set
    if not common:
        return 0.0
    score = 0.0
    for term in common:
        tf  = doc_tokens.count(term) / len(doc_tokens)
        df  = sum(1 for d in all_docs_tokens if term in d)
        idf = math.log((len(all_docs_tokens) + 1) / (df + 1)) + 1
        score += tf * idf
    return round(score, 4)

# ─── Doküman Ekleme ──────────────────────────────────────────────────────────
def add_document(title: str, content: str, category: str = "genel",
                 source: str = "") -> dict:
    """Dokümanı bilgi bankasına ekle (parçalara böl)"""
    kb     = load_kb()
    chunks = chunk_text(content, chunk_size=400, overlap=80)

    added = []
    for i, chunk in enumerate(chunks):
        doc_id = kb["next_id"]
        kb["next_id"] += 1
        entry = {
            "id":         doc_id,
            "type":       "document",
            "title":      title,
            "category":   category,
            "source":     source,
            "chunk_idx":  i,
            "total_chunks": len(chunks),
            "content":    chunk,
            "tokens":     simple_tokenize(chunk),
            "added_at":   datetime.now(timezone.utc).isoformat(),
            "access_count": 0,
        }
        kb["documents"].append(entry)
        added.append(doc_id)

    save_kb(kb)
    return {
        "status":      "ok",
        "title":       title,
        "category":    category,
        "chunks":      len(chunks),
        "doc_ids":     added,
        "total_words": len(content.split()),
    }

def add_note(content: str, category: str = "genel", tags: list = None) -> dict:
    """Kısa not/bilgi ekle"""
    kb = load_kb()
    note_id = kb["next_id"]
    kb["next_id"] += 1

    note = {
        "id":        note_id,
        "type":      "note",
        "content":   content,
        "category":  category,
        "tags":      tags or [],
        "tokens":    simple_tokenize(content),
        "added_at":  datetime.now(timezone.utc).isoformat(),
        "access_count": 0,
    }
    kb["notes"].append(note)
    # Son 2000 not tut
    kb["notes"] = kb["notes"][-2000:]
    save_kb(kb)
    return {"status": "ok", "id": note_id, "category": category}

# ─── Akıllı Arama ────────────────────────────────────────────────────────────
def search_knowledge(query: str, top_k: int = 5, category: str = None) -> list:
    """Sorguya en uygun bilgileri bul (TF-IDF)"""
    kb          = load_kb()
    all_entries = kb["documents"] + kb["notes"]

    if not all_entries:
        return []

    if category:
        all_entries = [e for e in all_entries if e.get("category") == category]

    query_tokens    = simple_tokenize(query)
    all_doc_tokens  = [e.get("tokens", []) for e in all_entries]

    scored = []
    for entry in all_entries:
        score = tfidf_score(query_tokens, entry.get("tokens", []), all_doc_tokens)
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, entry in scored[:top_k]:
        entry["access_count"] = entry.get("access_count", 0) + 1
        snippet = entry["content"][:300] + ("..." if len(entry["content"]) > 300 else "")
        results.append({
            "id":       entry["id"],
            "type":     entry["type"],
            "title":    entry.get("title", "Not"),
            "category": entry.get("category", "genel"),
            "snippet":  snippet,
            "score":    score,
            "content":  entry["content"],
        })

    save_kb(kb)
    return results

def get_context_for_prompt(query: str, max_chars: int = 3000) -> str:
    """Sohbet için ilgili bilgi bankası içeriğini hazırla"""
    results = search_knowledge(query, top_k=6)
    if not results:
        return ""

    context = "\n\n━━━ BİLGİ BANKASI (Kalıcı Hafıza) ━━━\n"
    total   = 0
    for r in results:
        chunk = f"\n[{r['type'].upper()} | {r.get('title','Not')} | {r['category']}]\n{r['content']}\n"
        if total + len(chunk) > max_chars:
            break
        context += chunk
        total   += len(chunk)
    context += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    return context

# ─── Bilgi Bankası Yönetimi ──────────────────────────────────────────────────
def list_knowledge(category: str = None) -> dict:
    """Bilgi bankası içeriğini listele"""
    kb      = load_kb()
    docs    = kb["documents"]
    notes   = kb["notes"]

    if category:
        docs  = [d for d in docs  if d.get("category") == category]
        notes = [n for n in notes if n.get("category") == category]

    # Benzersiz doküman başlıkları
    seen_titles = {}
    for d in docs:
        t = d.get("title","?")
        if t not in seen_titles:
            seen_titles[t] = {
                "title":    t,
                "category": d.get("category","genel"),
                "chunks":   d.get("total_chunks", 1),
                "added_at": d.get("added_at",""),
                "source":   d.get("source",""),
            }

    categories = {}
    for e in docs + notes:
        c = e.get("category","genel")
        categories[c] = categories.get(c, 0) + 1

    return {
        "total_documents": len(seen_titles),
        "total_notes":     len(notes),
        "total_chunks":    len(docs),
        "categories":      categories,
        "documents":       list(seen_titles.values()),
        "recent_notes":    [{
            "id":       n["id"],
            "content":  n["content"][:100],
            "category": n.get("category","genel"),
            "added_at": n.get("added_at",""),
        } for n in notes[-10:]],
    }

def delete_document(title: str) -> dict:
    """Başlığa göre dokümanı sil"""
    kb   = load_kb()
    before = len(kb["documents"])
    kb["documents"] = [d for d in kb["documents"] if d.get("title") != title]
    removed = before - len(kb["documents"])
    save_kb(kb)
    return {"status": "ok", "removed_chunks": removed}

def delete_note(note_id: int) -> dict:
    """ID ile notu sil"""
    kb = load_kb()
    kb["notes"] = [n for n in kb["notes"] if n.get("id") != note_id]
    save_kb(kb)
    return {"status": "ok"}

def clear_category(category: str) -> dict:
    """Kategoriyi tamamen temizle"""
    kb = load_kb()
    kb["documents"] = [d for d in kb["documents"] if d.get("category") != category]
    kb["notes"]     = [n for n in kb["notes"]     if n.get("category") != category]
    save_kb(kb)
    return {"status": "ok", "cleared": category}

# ─── OTOMATİK KONUŞMA HAFIZASI ───────────────────────────────────────────────
CONV_FILE = DATA_DIR / "conversations.json"

def load_conversations() -> dict:
    try:
        if CONV_FILE.exists():
            return json.loads(CONV_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"exchanges": [], "next_id": 1}

def save_conversations(data: dict):
    CONV_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def log_exchange(user_message: str, ai_reply: str):
    """Her yazışmayı kalıcı olarak kaydet — ASLA silinmez"""
    conv = load_conversations()
    conv["exchanges"].append({
        "id":        conv["next_id"],
        "user":      user_message,
        "assistant": ai_reply,
        "tokens":    simple_tokenize(user_message + " " + ai_reply),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    conv["next_id"] += 1
    # SINIRSIZ — hiçbir şey silinmez
    save_conversations(conv)

def search_conversations(query: str, top_k: int = 4) -> list:
    """Geçmiş konuşmalarda ara"""
    conv = load_conversations()
    exchanges = conv.get("exchanges", [])
    if not exchanges:
        return []

    query_tokens   = simple_tokenize(query)
    all_tokens     = [e.get("tokens", []) for e in exchanges]

    scored = []
    for e in exchanges:
        score = tfidf_score(query_tokens, e.get("tokens", []), all_tokens)
        if score > 0:
            scored.append((score, e))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [{
        "user":      e["user"][:300],
        "assistant": e["assistant"][:400],
        "timestamp": e.get("timestamp", ""),
        "score":     s,
    } for s, e in scored[:top_k]]

def get_full_context(query: str, max_chars: int = 4000) -> str:
    """Bilgi bankası + geçmiş konuşmalar birleşik bağlam"""
    parts = []

    # 1. Bilgi bankası (dokümanlar + notlar)
    kb_results = search_knowledge(query, top_k=4)
    if kb_results:
        parts.append("── KALICI BİLGİLER ──")
        for r in kb_results:
            parts.append(f"[{r.get('title','Not')} | {r['category']}]\n{r['content'][:500]}")

    # 2. Geçmiş konuşmalar
    conv_results = search_conversations(query, top_k=3)
    if conv_results:
        parts.append("── GEÇMİŞ KONUŞMALARDAN ──")
        for c in conv_results:
            date = c["timestamp"][:10] if c["timestamp"] else "?"
            parts.append(f"[{date}] Kullanıcı: {c['user']}\nSen: {c['assistant']}")

    if not parts:
        return ""

    context = "\n\n━━━ HAFIZA (Otomatik — tüm geçmiş kayıtlı) ━━━\n"
    total = len(context)
    for p in parts:
        if total + len(p) > max_chars:
            break
        context += p + "\n\n"
        total += len(p)
    context += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    return context

def get_memory_stats() -> dict:
    conv = load_conversations()
    kb   = load_kb()
    return {
        "total_exchanges": len(conv.get("exchanges", [])),
        "total_documents": len(set(d.get("title") for d in kb.get("documents", []))),
        "total_notes":     len(kb.get("notes", [])),
        "first_exchange":  conv["exchanges"][0]["timestamp"][:10] if conv.get("exchanges") else None,
    }
