"""GitLab and GitHub API helpers: create Merge Request / Pull Request."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


def _parse_repo_url(url: str) -> tuple[str, str] | None:
    """Return (host_type, owner/repo or project_path). host_type is 'github' or 'gitlab'."""
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://", "git@")):
        return None
    if url.startswith("git@"):
        # git@github.com:owner/repo.git or git@gitlab.com:owner/repo.git
        m = re.match(r"git@([^:]+):([^/]+/[^/]+?)(?:\.git)?$", url)
        if m:
            host = m.group(1).lower()
            path = m.group(2)
            if "github" in host:
                return ("github", path)
            if "gitlab" in host:
                return ("gitlab", path)
        return None
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").strip("/").replace(".git", "")
    if not path:
        return None
    if "github" in host:
        return ("github", path)
    if "gitlab" in host:
        return ("gitlab", path)
    return None


async def create_merge_request(
    repo: str,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str = "",
    *,
    github_token: str | None = None,
    gitlab_token: str | None = None,
) -> dict[str, Any]:
    """
    Create a Merge Request (GitLab) or Pull Request (GitHub).
    repo: "owner/repo" or full clone URL.
    Returns dict with ok, url, number/id, error.
    """
    # Normalize repo and detect platform from URL if needed
    host_type: str | None = None
    if repo.startswith(("http", "git@")):
        parsed = _parse_repo_url(repo)
        if not parsed:
            return {"ok": False, "error": "Could not parse repo URL"}
        host_type, repo_path = parsed
    else:
        repo_path = repo.strip()
        if "/" in repo_path:
            repo_path = repo_path

    if not source_branch or not target_branch or not title:
        return {"ok": False, "error": "source_branch, target_branch and title are required"}

    # Prefer platform from URL; otherwise use GitHub if token present
    use_github = (host_type == "github") or (
        host_type is None and github_token and "/" in repo_path
    )
    use_gitlab = (host_type == "gitlab") or (host_type is None and gitlab_token)

    if use_github and github_token and "/" in repo_path:
        owner, repo_name = repo_path.split("/", 1)
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"https://api.github.com/repos/{owner}/{repo_name}/pulls",
                    headers={
                        "Authorization": f"Bearer {github_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json={
                        "title": title,
                        "head": source_branch,
                        "base": target_branch,
                        "body": description or "",
                    },
                    timeout=15.0,
                )
            if r.status_code == 201:
                data = r.json()
                return {
                    "ok": True,
                    "url": data.get("html_url", ""),
                    "number": data.get("number"),
                    "platform": "github",
                }
            err = (
                r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            )
            return {
                "ok": False,
                "error": err.get("message", r.text) or f"HTTP {r.status_code}",
            }
        except Exception as e:
            logger.exception("GitHub create PR failed: %s", e)
            return {"ok": False, "error": str(e)}

    if use_gitlab and gitlab_token:
        # GitLab: project id can be path (owner/repo) URL-encoded
        project_id = repo_path.replace("/", "%2F")
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests",
                    headers={"PRIVATE-TOKEN": gitlab_token},
                    json={
                        "source_branch": source_branch,
                        "target_branch": target_branch,
                        "title": title,
                        "description": description or "",
                    },
                    timeout=15.0,
                )
            if r.status_code in (200, 201):
                data = r.json()
                return {
                    "ok": True,
                    "url": data.get("web_url", ""),
                    "iid": data.get("iid"),
                    "platform": "gitlab",
                }
            err = (
                r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            )
            return {
                "ok": False,
                "error": err.get("message", err.get("error", r.text)) or f"HTTP {r.status_code}",
            }
        except Exception as e:
            logger.exception("GitLab create MR failed: %s", e)
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": "Set GITHUB_TOKEN or GITLAB_TOKEN for create_mr"}


async def search_github_repos(
    query: str,
    *,
    token: str | None = None,
    per_page: int = 30,
) -> dict[str, Any]:
    """Search GitHub repositories. GET /search/repositories. Returns ok, items ([{full_name, html_url, description}]), error."""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "query is required"}
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": "GITHUB_TOKEN is required for search"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": query, "per_page": min(per_page, 100)},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=15.0,
            )
        if r.status_code != 200:
            err = (
                r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            )
            return {
                "ok": False,
                "error": err.get("message", r.text) or f"HTTP {r.status_code}",
            }
        data = r.json()
        items = [
            {
                "full_name": it.get("full_name", ""),
                "html_url": it.get("html_url", ""),
                "description": it.get("description") or "",
                "clone_url": it.get("clone_url", ""),
            }
            for it in data.get("items", [])
        ]
        return {"ok": True, "items": items, "total_count": data.get("total_count", 0)}
    except Exception as e:
        logger.exception("GitHub search repos failed: %s", e)
        return {"ok": False, "error": str(e)}


async def search_gitlab_repos(
    query: str,
    *,
    token: str | None = None,
    per_page: int = 30,
) -> dict[str, Any]:
    """Search GitLab projects. GET /projects?search=... Returns ok, items ([{full_name, web_url, description, clone_url}]), error."""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "query is required"}
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": "GITLAB_TOKEN is required for search"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://gitlab.com/api/v4/projects",
                params={"search": query, "per_page": min(per_page, 100)},
                headers={"PRIVATE-TOKEN": token},
                timeout=15.0,
            )
        if r.status_code != 200:
            err = (
                r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            )
            return {
                "ok": False,
                "error": err.get("message", err.get("error", r.text)) or f"HTTP {r.status_code}",
            }
        data = r.json()
        if not isinstance(data, list):
            data = []
        items = [
            {
                "full_name": it.get("path_with_namespace", ""),
                "html_url": it.get("web_url", ""),
                "web_url": it.get("web_url", ""),
                "description": it.get("description") or "",
                "clone_url": it.get("http_url_to_repo", "") or it.get("ssh_url_to_repo", ""),
            }
            for it in data
        ]
        return {"ok": True, "items": items, "total_count": len(items)}
    except Exception as e:
        logger.exception("GitLab search repos failed: %s", e)
        return {"ok": False, "error": str(e)}


async def list_github_user_repos(
    *,
    token: str | None = None,
    per_page: int = 30,
    page: int = 1,
) -> dict[str, Any]:
    """List GitHub repos for the authenticated user. GET /user/repos. For /github command (9.2)."""
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": "GITHUB_TOKEN is required"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.github.com/user/repos",
                params={"per_page": min(per_page, 100), "page": max(1, page), "sort": "updated"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=15.0,
            )
        if r.status_code != 200:
            err = (
                r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            )
            return {
                "ok": False,
                "error": err.get("message", r.text) or f"HTTP {r.status_code}",
            }
        data = r.json()
        if not isinstance(data, list):
            data = []
        items = [
            {
                "full_name": it.get("full_name", ""),
                "html_url": it.get("html_url", ""),
                "description": it.get("description") or "",
                "clone_url": it.get("clone_url", ""),
            }
            for it in data
        ]
        return {"ok": True, "items": items, "total_count": len(items)}
    except Exception as e:
        logger.exception("GitHub list user repos failed: %s", e)
        return {"ok": False, "error": str(e)}


async def list_gitlab_user_repos(
    *,
    token: str | None = None,
    per_page: int = 30,
    page: int = 1,
) -> dict[str, Any]:
    """List GitLab projects for the authenticated user. GET /projects (membership). For /gitlab command (9.2)."""
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": "GITLAB_TOKEN is required"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://gitlab.com/api/v4/projects",
                params={
                    "membership": "true",
                    "per_page": min(per_page, 100),
                    "page": max(1, page),
                    "order_by": "updated_at",
                },
                headers={"PRIVATE-TOKEN": token},
                timeout=15.0,
            )
        if r.status_code != 200:
            try:
                err = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            except Exception:
                err = {}
            err = err if isinstance(err, dict) else {}
            return {
                "ok": False,
                "error": err.get("message", err.get("error", r.text)) or f"HTTP {r.status_code}",
            }
        data = r.json()
        if not isinstance(data, list):
            data = []
        items = [
            {
                "full_name": it.get("path_with_namespace", ""),
                "html_url": it.get("web_url", ""),
                "web_url": it.get("web_url", ""),
                "description": it.get("description") or "",
                "clone_url": it.get("http_url_to_repo", "") or it.get("ssh_url_to_repo", ""),
            }
            for it in data
        ]
        return {"ok": True, "items": items, "total_count": len(items)}
    except Exception as e:
        logger.exception("GitLab list user repos failed: %s", e)
        return {"ok": False, "error": str(e)}
