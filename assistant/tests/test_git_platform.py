"""Tests for git_platform: URL parsing and create_merge_request (GitHub/GitLab) with mocked httpx."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from assistant.skills.git_platform import create_merge_request, search_github_repos


def _mock_httpx_client(status_code: int = 201, json_data: dict | None = None):
    response = MagicMock()
    response.status_code = status_code
    response.headers = {"content-type": "application/json"}
    response.json = MagicMock(return_value=json_data or {"html_url": "https://github.com/a/b/pull/1", "number": 1})
    response.text = ""

    async def post(*args, **kwargs):
        return response

    client = MagicMock()
    client.post = AsyncMock(side_effect=post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


@pytest.mark.asyncio
async def test_create_merge_request_github_url_success():
    """GitHub repo URL: uses GitHub API and returns PR url/number."""
    with patch("assistant.skills.git_platform.httpx.AsyncClient", side_effect=_mock_httpx_client(201, {"html_url": "https://github.com/o/r/pull/2", "number": 2})):
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
    response.json = MagicMock(return_value={"web_url": "https://gitlab.com/o/r/-/merge_requests/3", "iid": 3})
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
            "items": [{"full_name": "a/b", "html_url": "https://github.com/a/b", "description": "d", "clone_url": "https://github.com/a/b.git"}],
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
async def test_search_github_repos_missing_token():
    out = await search_github_repos("q", token=None)
    assert out["ok"] is False
    assert "token" in out.get("error", "").lower() or "GITHUB" in out.get("error", "")


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
