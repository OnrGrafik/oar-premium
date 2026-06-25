"""
DevOps Monitor — OAR Premium  (Railway)
═══════════════════════════════════════════════════════════
Railway runtime değişkenleri + GitHub API ile sistem durumunu izler:
  • Aktif deploy (commit, branch, durum, region, public domain)
  • Son GitHub commit'leri

Railway, container'a şu değişkenleri OTOMATİK enjekte eder (token GEREKMEZ):
  RAILWAY_SERVICE_NAME / RAILWAY_SERVICE_ID
  RAILWAY_ENVIRONMENT_NAME
  RAILWAY_DEPLOYMENT_ID
  RAILWAY_GIT_COMMIT_SHA / RAILWAY_GIT_BRANCH / RAILWAY_GIT_COMMIT_MESSAGE
  RAILWAY_PUBLIC_DOMAIN / RAILWAY_PRIVATE_DOMAIN
  RAILWAY_REPLICA_REGION

Opsiyonel (deploy GEÇMİŞİ için — Railway GraphQL API):
  RAILWAY_API_TOKEN   — Railway → Account/Project Settings → Tokens
  (RAILWAY_SERVICE_ID, RAILWAY_PROJECT_ID, RAILWAY_ENVIRONMENT_ID otomatik gelir)

GitHub:
  GITHUB_TOKEN  — GitHub → Developer settings → PAT (repo read)
  GITHUB_REPO   — "OnrGrafik/oar-premium" formatında

NOT: Eski RENDER_API_KEY ve RENDER_SERVICE_ID artık KULLANILMIYOR — silinebilir.
"""
import os, httpx
from datetime import datetime, timezone

RAILWAY_GRAPHQL = "https://backboard.railway.app/graphql/v2"


def _cfg():
    return {
        "railway_token": os.environ.get("RAILWAY_API_TOKEN", ""),
        "project_id":    os.environ.get("RAILWAY_PROJECT_ID", ""),
        "environment_id": os.environ.get("RAILWAY_ENVIRONMENT_ID", ""),
        "service_id":    os.environ.get("RAILWAY_SERVICE_ID", ""),
        "github_token":  os.environ.get("GITHUB_TOKEN", ""),
        "github_repo":   os.environ.get("GITHUB_REPO", "OnrGrafik/oar-premium"),
    }


def _railway_runtime() -> dict:
    """Token gerektirmeyen — Railway'in enjekte ettiği çalışma-zamanı değişkenleri."""
    g = os.environ
    return {
        "service":     g.get("RAILWAY_SERVICE_NAME") or g.get("RAILWAY_SERVICE_ID", ""),
        "environment": g.get("RAILWAY_ENVIRONMENT_NAME", ""),
        "deployment":  (g.get("RAILWAY_DEPLOYMENT_ID", "") or "")[:12],
        "commit_id":   (g.get("RAILWAY_GIT_COMMIT_SHA", "") or "")[:7],
        "branch":      g.get("RAILWAY_GIT_BRANCH", ""),
        "commit_msg":  (g.get("RAILWAY_GIT_COMMIT_MESSAGE", "") or "")[:60],
        "region":      g.get("RAILWAY_REPLICA_REGION", ""),
        "public_url":  g.get("RAILWAY_PUBLIC_DOMAIN", ""),
    }


async def railway_durum(limit: int = 5) -> dict:
    """
    Aktif deploy bilgisini Railway runtime değişkenlerinden döner (token gerekmez).
    RAILWAY_API_TOKEN varsa ek olarak deploy GEÇMİŞİNİ GraphQL ile çeker.
    """
    rt = _railway_runtime()
    railway_uzerinde = bool(rt["service"] or rt["deployment"] or rt["environment"])

    if not railway_uzerinde and not _cfg()["railway_token"]:
        return {"bagli": False,
                "neden": "Railway runtime değişkenleri yok (lokal çalışıyor olabilir)"}

    sonuc = {"bagli": True, "runtime": rt, "deploys": []}

    # Aktif deploy'u her zaman göster (runtime'dan)
    if rt["commit_id"] or rt["deployment"]:
        sonuc["deploys"].append({
            "id": rt["deployment"],
            "durum": "live",
            "commit_msg": rt["commit_msg"] or rt["branch"],
            "commit_id": rt["commit_id"],
            "olusturma": "",
            "bitis": "",
        })

    # Opsiyonel: deploy geçmişi (GraphQL — token gerekir)
    c = _cfg()
    if c["railway_token"] and c["service_id"] and c["environment_id"]:
        try:
            query = """
            query d($serviceId: String!, $environmentId: String!) {
              deployments(first: %d, input: {serviceId: $serviceId, environmentId: $environmentId}) {
                edges { node { id status createdAt } }
              }
            }""" % limit
            async with httpx.AsyncClient(timeout=15) as cl:
                r = await cl.post(
                    RAILWAY_GRAPHQL,
                    headers={"Authorization": f"Bearer {c['railway_token']}",
                             "Content-Type": "application/json"},
                    json={"query": query,
                          "variables": {"serviceId": c["service_id"],
                                        "environmentId": c["environment_id"]}})
                if r.status_code == 200:
                    edges = (((r.json() or {}).get("data") or {})
                             .get("deployments") or {}).get("edges") or []
                    gecmis = []
                    for e in edges:
                        n = e.get("node", {})
                        gecmis.append({
                            "id": (n.get("id", "") or "")[:12],
                            "durum": (n.get("status", "") or "?").lower(),
                            "commit_msg": "",
                            "commit_id": "",
                            "olusturma": n.get("createdAt", ""),
                            "bitis": "",
                        })
                    if gecmis:
                        sonuc["deploys"] = gecmis
        except Exception as e:
            sonuc["gecmis_hata"] = str(e)[:80]

    return sonuc


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
    deploys = await railway_durum()
    commits = await github_commits()
    return {
        "tarih": datetime.now(timezone.utc).isoformat(),
        "platform": "railway",
        "railway_durum": deploys,
        "github_commits": commits,
    }
