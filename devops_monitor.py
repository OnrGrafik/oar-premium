"""
DevOps Monitor — OAR Premium
═══════════════════════════════════════════════════════════
Render API + GitHub API ile sistem durumunu izler:
  • Son deploy'lar (durum, commit, zaman)
  • Environment değişkenleri (sadece isimler — değerler GİZLİ)
  • Son GitHub commit'leri

Token'lar Render environment'tan okunur (kod içinde YOK):
  RENDER_API_KEY    — Render Dashboard → Account → API Keys
  RENDER_SERVICE_ID — servis URL'inde srv-xxxx
  GITHUB_TOKEN      — GitHub → Developer settings → PAT (repo read)
  GITHUB_REPO       — "OnrGrafik/oar-premium" formatında

Token yoksa panel "bağlı değil" döner — hata vermez.
"""
import os, httpx
from datetime import datetime, timezone

def _cfg():
    return {
        "render_key": os.environ.get("RENDER_API_KEY", ""),
        "service_id": os.environ.get("RENDER_SERVICE_ID", ""),
        "github_token": os.environ.get("GITHUB_TOKEN", ""),
        "github_repo": os.environ.get("GITHUB_REPO", "OnrGrafik/oar-premium"),
    }

async def render_deploys(limit=5) -> dict:
    c = _cfg()
    if not c["render_key"] or not c["service_id"]:
        return {"bagli": False, "neden": "RENDER_API_KEY veya RENDER_SERVICE_ID eksik"}
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.get(
                f"https://api.render.com/v1/services/{c['service_id']}/deploys",
                headers={"Authorization": f"Bearer {c['render_key']}", "Accept": "application/json"},
                params={"limit": limit})
            if r.status_code != 200:
                return {"bagli": False, "neden": f"Render API {r.status_code}"}
            data = r.json()
            deploys = []
            for item in data:
                d = item.get("deploy", item)
                commit = d.get("commit", {}) or {}
                deploys.append({
                    "id": d.get("id", "")[:12],
                    "durum": d.get("status", "?"),
                    "commit_msg": (commit.get("message", "") or "")[:60],
                    "commit_id": (commit.get("id", "") or "")[:7],
                    "olusturma": d.get("createdAt", ""),
                    "bitis": d.get("finishedAt", ""),
                })
            return {"bagli": True, "deploys": deploys}
    except Exception as e:
        return {"bagli": False, "neden": str(e)[:80]}

async def render_env() -> dict:
    c = _cfg()
    if not c["render_key"] or not c["service_id"]:
        return {"bagli": False, "neden": "token eksik"}
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.get(
                f"https://api.render.com/v1/services/{c['service_id']}/env-vars",
                headers={"Authorization": f"Bearer {c['render_key']}", "Accept": "application/json"})
            if r.status_code != 200:
                return {"bagli": False, "neden": f"Render API {r.status_code}"}
            data = r.json()
            # SADECE İSİMLER — değerler gizli tutulur
            isimler = []
            for item in data:
                ev = item.get("envVar", item)
                key = ev.get("key", "")
                if key:
                    # Değeri maskele: sadece var/yok bilgisi
                    val = ev.get("value", "")
                    isimler.append({"key": key, "dolu": bool(val)})
            return {"bagli": True, "degiskenler": isimler}
    except Exception as e:
        return {"bagli": False, "neden": str(e)[:80]}

async def github_commits(limit=8) -> dict:
    c = _cfg()
    if not c["github_token"]:
        return {"bagli": False, "neden": "GITHUB_TOKEN eksik"}
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.get(
                f"https://api.github.com/repos/{c['github_repo']}/commits",
                headers={"Authorization": f"Bearer {c['github_token']}",
                         "Accept": "application/vnd.github+json"},
                params={"per_page": limit})
            if r.status_code != 200:
                return {"bagli": False, "neden": f"GitHub API {r.status_code}"}
            data = r.json()
            commits = []
            for item in data:
                commit = item.get("commit", {})
                author = commit.get("author", {})
                commits.append({
                    "sha": item.get("sha", "")[:7],
                    "mesaj": (commit.get("message", "") or "").split("\n")[0][:70],
                    "yazar": author.get("name", "?"),
                    "tarih": author.get("date", ""),
                })
            return {"bagli": True, "commits": commits}
    except Exception as e:
        return {"bagli": False, "neden": str(e)[:80]}

async def devops_ozet() -> dict:
    deploys = await render_deploys()
    env     = await render_env()
    commits = await github_commits()
    return {
        "tarih": datetime.now(timezone.utc).isoformat(),
        "render_deploys": deploys,
        "render_env": env,
        "github_commits": commits,
    }
