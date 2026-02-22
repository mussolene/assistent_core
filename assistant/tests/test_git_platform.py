"""Tests for git_platform: URL parsing and create_merge_request (GitHub/GitLab) with mocked httpx."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from assistant.skills.git_platform import (
    _parse_repo_url,
    create_merge_request,
    search_github_repos,
    search_gitlab_repos,
)


def _mock_httpx_client(status_code: int = 201, json_data: dict | None = None):
    response = MagicMock()
    response.status_code = status_code
    response.headers = {"content-type": "application/json"}
    response.json = MagicMock(
        return_value=json_data or {"html_url": "https://github.com/a/b/pull/1", "number": 1}
    )
    response.text = ""

    async def post(*args, **kwargs):
        return response

    client = MagicMock()
    client.post = AsyncMock(side_effect=post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def test_parse_repo_url_https_github():
    assert _parse_repo_url("https://github.com/owner/repo") == ("github", "owner/repo")
    assert _parse_repo_url("https://github.com/owner/repo.git") == ("github", "owner/repo")


def test_parse_repo_url_https_gitlab():
    assert _parse_repo_url("https://gitlab.com/g/r") == ("gitlab", "g/r")


def test_parse_repo_url_git_at_github():
    assert _parse_repo_url("git@github.com:owner/repo.git") == ("github", "owner/repo")


def test_parse_repo_url_git_at_gitlab():
    assert _parse_repo_url("git@gitlab.com:g/r.git") == ("gitlab", "g/r")


def test_parse_repo_url_invalid():
    assert _parse_repo_url("not-a-url") is None
    assert _parse_repo_url("http://other.com/path") is None
    assert _parse_repo_url("") is None


@pytest.mark.asyncio
async def test_create_merge_request_parse_url_fails():
    out = await create_merge_request(
        repo="https://other.com/o/r",
        source_branch="f",
        target_branch="main",
        title="T",
        github_token="gh",
    )
    assert out["ok"] is False
    assert "parse" in out.get("error", "").lower() or "url" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_create_merge_request_github_url_success():
    """GitHub repo URL: uses GitHub API and returns PR url/number."""
    client = _mock_httpx_client(201, {"html_url": "https://github.com/o/r/pull/2", "number": 2})
    with patch(
        "assistant.skills.git_platform.httpx.AsyncClient",
        return_value=client,
    ):
        out = await create_merge_request(
            repo="https://github.com/o/r",
            source_branch="feature",
            target_branch="main",
            title="Title",
            description="Desc",
            github_token="gh_token",
            gitlab_token=None,
        )
    assert out["ok"] is True
    assert out.get("platform") == "github"
    assert "pull" in out.get("url", "")


@pytest.mark.asyncio
async def test_create_merge_request_gitlab_url_success():
    """GitLab repo URL: uses GitLab API and returns MR url/iid."""
    response = MagicMock()
    response.status_code = 201
    response.headers = {"content-type": "application/json"}
    response.json = MagicMock(
        return_value={"web_url": "https://gitlab.com/o/r/-/merge_requests/3", "iid": 3}
    )
    response.text = ""

    async def post(*args, **kwargs):
        return response

    client = MagicMock()
    client.post = AsyncMock(side_effect=post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("assistant.skills.git_platform.httpx.AsyncClient", return_value=client):
        out = await create_merge_request(
            repo="https://gitlab.com/o/r",
            source_branch="feature",
            target_branch="main",
            title="Title",
            github_token=None,
            gitlab_token="gl_token",
        )
    assert out["ok"] is True
    assert out.get("platform") == "gitlab"
    assert out.get("iid") == 3


@pytest.mark.asyncio
async def test_create_merge_request_invalid_url():
    """Invalid repo URL returns error."""
    out = await create_merge_request(
        repo="not-a-url",
        source_branch="f",
        target_branch="main",
        title="T",
        github_token="gh",
        gitlab_token=None,
    )
    # repo is not URL, so host_type is None; repo_path = "not-a-url", no "/" -> use_github False, use_gitlab True if token
    # With only github_token, use_gitlab is True (host_type None and gitlab_token is None -> use_gitlab = False). So we fall through to "Set GITHUB_TOKEN..."
    # Actually: use_github = (host_type is None and github_token and "/" in repo_path) = (True and True and False) = False. use_gitlab = (host_type is None and gitlab_token) = False. So no token path taken -> "Set GITHUB_TOKEN or GITLAB_TOKEN"
    assert out["ok"] is False
    assert "GITHUB_TOKEN" in out.get("error", "")


@pytest.mark.asyncio
async def test_create_merge_request_github_non_201_returns_error():
    """GitHub API returns non-201 -> ok False with error message."""
    client = _mock_httpx_client(400, {"message": "Validation Failed"})
    with patch("assistant.skills.git_platform.httpx.AsyncClient", return_value=client):
        out = await create_merge_request(
            repo="https://github.com/o/r",
            source_branch="f",
            target_branch="main",
            title="T",
            github_token="gh",
        )
    assert out["ok"] is False
    assert "Validation" in out.get("error", "") or "400" in out.get("error", "")


@pytest.mark.asyncio
async def test_create_merge_request_owner_repo_path_with_github_token():
    """Repo as 'owner/repo' (no URL) with github_token uses GitHub API."""
    client = _mock_httpx_client(201, {"html_url": "https://github.com/a/b/pull/1", "number": 1})
    with patch("assistant.skills.git_platform.httpx.AsyncClient", return_value=client):
        out = await create_merge_request(
            repo="owner/repo",
            source_branch="f",
            target_branch="main",
            title="T",
            github_token="token",
        )
    assert out["ok"] is True
    assert out.get("platform") == "github"


@pytest.mark.asyncio
async def test_create_merge_request_missing_params():
    """Missing source_branch/target_branch/title returns error."""
    out = await create_merge_request(
        repo="https://github.com/o/r",
        source_branch="",
        target_branch="main",
        title="T",
        github_token="gh",
    )
    assert out["ok"] is False
    assert "required" in out.get("error", "").lower() or "source_branch" in out.get("error", "")


@pytest.mark.asyncio
async def test_search_github_repos_success():
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response.json = MagicMock(
        return_value={
            "items": [
                {
                    "full_name": "a/b",
                    "html_url": "https://github.com/a/b",
                    "description": "d",
                    "clone_url": "https://github.com/a/b.git",
                }
            ],
            "total_count": 1,
        }
    )
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    with patch("assistant.skills.git_platform.httpx.AsyncClient", return_value=client):
        out = await search_github_repos("test", token="gh")
    assert out["ok"] is True
    assert len(out["items"]) == 1
    assert out["items"][0]["full_name"] == "a/b"
    assert out["total_count"] == 1


@pytest.mark.asyncio
async def test_search_github_repos_missing_query():
    out = await search_github_repos("", token="x")
    assert out["ok"] is False
    assert "query" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_create_merge_request_gitlab_non_200_returns_error():
    """GitLab API returns non-200/201 -> ok False with error message."""
    response = MagicMock()
    response.status_code = 403
    response.headers = {"content-type": "application/json"}
    response.json = MagicMock(return_value={"message": "Forbidden"})
    response.text = "Forbidden"
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    with patch("assistant.skills.git_platform.httpx.AsyncClient", return_value=client):
        out = await create_merge_request(
            repo="https://gitlab.com/o/r",
            source_branch="f",
            target_branch="main",
            title="T",
            gitlab_token="gl",
        )
    assert out["ok"] is False
    assert "403" in out.get("error", "") or "Forbidden" in out.get("error", "")


@pytest.mark.asyncio
async def test_create_merge_request_github_post_raises_returns_error():
    """GitHub client.post raises -> ok False with error message."""
    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("network error"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    with patch("assistant.skills.git_platform.httpx.AsyncClient", return_value=client):
        out = await create_merge_request(
            repo="https://github.com/o/r",
            source_branch="f",
            target_branch="main",
            title="T",
            github_token="gh",
        )
    assert out["ok"] is False
    assert "network" in out.get("error", "").lower() or "error" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_search_github_repos_non_200_returns_error():
    """GitHub search API returns non-200 -> ok False."""
    response = MagicMock()
    response.status_code = 422
    response.headers = {"content-type": "application/json"}
    response.json = MagicMock(return_value={"message": "Validation Failed"})
    response.text = "error"
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    with patch("assistant.skills.git_platform.httpx.AsyncClient", return_value=client):
        out = await search_github_repos("q", token="t")
    assert out["ok"] is False
    assert "422" in out.get("error", "") or "Validation" in out.get("error", "")


@pytest.mark.asyncio
async def test_search_github_repos_exception_returns_error():
    """GitHub search client.get raises -> ok False with error message."""
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    with patch("assistant.skills.git_platform.httpx.AsyncClient", return_value=client):
        out = await search_github_repos("q", token="t")
    assert out["ok"] is False
    assert "timeout" in out.get("error", "").lower() or "error" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_search_github_repos_missing_token():
    out = await search_github_repos("q", token=None)
    assert out["ok"] is False
    assert "token" in out.get("error", "").lower() or "GITHUB" in out.get("error", "")


@pytest.mark.asyncio
async def test_search_gitlab_repos_success():
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response.json = MagicMock(
        return_value=[
            {
                "path_with_namespace": "g/r",
                "web_url": "https://gitlab.com/g/r",
                "description": "d",
                "http_url_to_repo": "https://gitlab.com/g/r.git",
            },
        ]
    )
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    with patch("assistant.skills.git_platform.httpx.AsyncClient", return_value=client):
        out = await search_gitlab_repos("test", token="gl")
    assert out["ok"] is True
    assert len(out["items"]) == 1
    assert out["items"][0]["full_name"] == "g/r"
    assert "gitlab" in out["items"][0]["html_url"]


@pytest.mark.asyncio
async def test_search_gitlab_repos_non_200_returns_error():
    """GitLab search API returns non-200 -> ok False."""
    response = MagicMock()
    response.status_code = 403
    response.headers = {"content-type": "application/json"}
    response.json = MagicMock(return_value={"error": "Forbidden"})
    response.text = "Forbidden"
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    with patch("assistant.skills.git_platform.httpx.AsyncClient", return_value=client):
        out = await search_gitlab_repos("q", token="t")
    assert out["ok"] is False
    assert "403" in out.get("error", "") or "Forbidden" in out.get("error", "")


@pytest.mark.asyncio
async def test_search_gitlab_repos_exception_returns_error():
    """GitLab search client raises -> ok False with error message."""
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("network error"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    with patch("assistant.skills.git_platform.httpx.AsyncClient", return_value=client):
        out = await search_gitlab_repos("q", token="t")
    assert out["ok"] is False
    assert "network" in out.get("error", "").lower() or "error" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_search_gitlab_repos_missing_token():
    out = await search_gitlab_repos("q", token=None)
    assert out["ok"] is False
    assert "GITLAB" in out.get("error", "")


@pytest.mark.asyncio
async def test_create_merge_request_no_tokens():
    """No tokens returns error (no HTTP call)."""
    out = await create_merge_request(
        repo="https://github.com/o/r",
        source_branch="f",
        target_branch="main",
        title="T",
        github_token=None,
        gitlab_token=None,
    )
    assert out["ok"] is False
    assert "GITHUB_TOKEN" in out.get("error", "")


# --- Iteration 9.2: list user repos (GitHub/GitLab) ---


@pytest.mark.asyncio
async def test_list_github_user_repos_missing_token():
    from assistant.skills.git_platform import list_github_user_repos

    out = await list_github_user_repos(token=None)
    assert out["ok"] is False
    assert "GITHUB_TOKEN" in out.get("error", "")


@pytest.mark.asyncio
async def test_list_github_user_repos_success():
    from assistant.skills.git_platform import list_github_user_repos

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_client.return_value.get = AsyncMock(
            return_value=MagicMock(
                status_code=200,
                json=lambda: [
                    {"full_name": "u/r1", "html_url": "https://github.com/u/r1", "clone_url": "https://github.com/u/r1.git", "description": "d1"},
                ],
                headers={"content-type": "application/json"},
            )
        )
        out = await list_github_user_repos(token="gh_token", per_page=6, page=1)
    assert out["ok"] is True
    assert len(out.get("items", [])) == 1
    assert out["items"][0]["full_name"] == "u/r1"
    assert out["items"][0]["html_url"] == "https://github.com/u/r1"


@pytest.mark.asyncio
async def test_list_gitlab_user_repos_missing_token():
    from assistant.skills.git_platform import list_gitlab_user_repos

    out = await list_gitlab_user_repos(token=None)
    assert out["ok"] is False
    assert "GITLAB" in out.get("error", "")


@pytest.mark.asyncio
async def test_list_github_user_repos_non_200_returns_error():
    from assistant.skills.git_platform import list_github_user_repos

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_client.return_value.get = AsyncMock(
            return_value=MagicMock(
                status_code=401,
                json=lambda: {"message": "Bad credentials"},
                headers={"content-type": "application/json"},
            )
        )
        out = await list_github_user_repos(token="bad", per_page=6, page=1)
    assert out["ok"] is False
    assert "401" in out.get("error", "") or "credentials" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_list_gitlab_user_repos_success():
    from assistant.skills.git_platform import list_gitlab_user_repos

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_client.return_value.get = AsyncMock(
            return_value=MagicMock(
                status_code=200,
                json=lambda: [
                    {"path_with_namespace": "g/r1", "web_url": "https://gitlab.com/g/r1", "http_url_to_repo": "https://gitlab.com/g/r1.git", "description": ""},
                ],
                headers={"content-type": "application/json"},
            )
        )
        out = await list_gitlab_user_repos(token="gl_token", per_page=6, page=1)
    assert out["ok"] is True
    assert len(out.get("items", [])) == 1
    assert out["items"][0]["full_name"] == "g/r1"
    assert out["items"][0]["html_url"] == "https://gitlab.com/g/r1"
