"""Git + GitLab/GitHub skill: clone, read, status, diff, log, commit, push, create_mr."""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from assistant.security.command_whitelist import CommandWhitelist
from assistant.security.sandbox import run_in_sandbox
from assistant.skills.base import BaseSkill
from assistant.skills.git_platform import (
    create_merge_request,
    search_github_repos,
    search_gitlab_repos,
)

logger = logging.getLogger(__name__)

GIT_ALLOWED = ["git"]


def list_cloned_repos_sync(workspace_dir: str) -> list[dict[str, str]]:
    """
    Синхронно сканирует workspace на директории с .git и возвращает список {path, remote_url}.
    Для использования в дашборде/API (без asyncio). Путь workspace_dir должен быть абсолютным и существовать.
    """
    if not workspace_dir or not os.path.isdir(workspace_dir):
        return []
    repos: list[dict[str, str]] = []
    try:
        for name in sorted(os.listdir(workspace_dir)):
            path = os.path.join(workspace_dir, name)
            if not os.path.isdir(path):
                continue
            if not os.path.exists(os.path.join(path, ".git")):
                continue
            remote_url = ""
            try:
                r = subprocess.run(
                    ["git", "-C", path, "remote", "get-url", "origin"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    cwd=workspace_dir,
                )
                if r.returncode == 0 and r.stdout:
                    remote_url = r.stdout.strip()
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass
            repos.append({"path": name, "remote_url": remote_url})
    except OSError:
        pass
    return repos


class GitSkill(BaseSkill):
    """
    Git operations in sandbox: clone, read file from repo, status/diff/log, commit, push.
    Create Merge Request (GitLab) / Pull Request (GitHub) via API.
    For clone/push network must be enabled (SANDBOX_NETWORK_ENABLED or network_enabled=True).
    For create_mr set GITHUB_TOKEN or GITLAB_TOKEN in env.
    """

    def __init__(
        self,
        workspace_dir: str = "/workspace",
        cpu_limit_seconds: int = 30,
        memory_limit_mb: int = 256,
        network_enabled: bool = False,
    ) -> None:
        self._whitelist = CommandWhitelist(GIT_ALLOWED)
        self._workspace = workspace_dir
        self._cpu = cpu_limit_seconds
        self._memory = memory_limit_mb
        self._network = network_enabled

    @property
    def name(self) -> str:
        return "git"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        action = (params.get("action") or params.get("subcommand") or "status").lower().strip()
        use_network = self._network and action in ("clone", "push")

        if action == "clone":
            return await self._clone(params, use_network)
        if action == "read":
            return await self._read(params)
        if action == "commit":
            return await self._commit(params)
        if action == "push":
            return await self._push(params, use_network)
        if action == "create_mr":
            return await self._create_mr(params)
        if action in ("list_repos", "list_cloned"):
            return await self._list_repos(params)
        if action == "search_repos":
            return await self._search_repos(params)
        # status, diff, log, show, etc.
        return await self._git_subcommand(params)

    async def _clone(self, params: dict[str, Any], network: bool) -> dict[str, Any]:
        url = (params.get("url") or params.get("repo") or "").strip()
        if not url:
            return {"ok": False, "error": "url or repo is required for clone"}
        dir_name = (params.get("dir") or params.get("target_dir") or "").strip()
        args = ["clone", "--", url]
        if dir_name:
            args.append(dir_name)
        ok, reason = self._whitelist.is_allowed("git " + " ".join(args))
        if not ok:
            return {"ok": False, "error": reason}
        code, stdout, stderr = await run_in_sandbox(
            ["git"] + args,
            cwd=self._workspace,
            cpu_limit_seconds=self._cpu,
            memory_limit_mb=self._memory,
            network=network,
        )
        if code != 0 and not network:
            return {
                "ok": False,
                "error": "clone requires network. Set SANDBOX_NETWORK_ENABLED=true or network_enabled for git skill.",
                "stdout": stdout,
                "stderr": stderr,
            }
        return {"ok": code == 0, "returncode": code, "stdout": stdout, "stderr": stderr}

    async def _read(self, params: dict[str, Any]) -> dict[str, Any]:
        path = (params.get("path") or params.get("file") or "").strip()
        rev = (params.get("rev") or params.get("ref") or "HEAD").strip()
        repo_dir = (params.get("repo_dir") or params.get("cwd") or "").strip()
        if not path:
            return {"ok": False, "error": "path or file is required for read"}
        cwd = self._workspace
        if repo_dir:
            cwd = os.path.join(self._workspace, repo_dir)
        # git show rev:path (path must not contain colons in a confusing way)
        args = ["show", f"{rev}:{path}"]
        ok, reason = self._whitelist.is_allowed("git " + " ".join(args))
        if not ok:
            return {"ok": False, "error": reason}
        code, stdout, stderr = await run_in_sandbox(
            ["git", "show", f"{rev}:{path}"],
            cwd=cwd,
            cpu_limit_seconds=self._cpu,
            memory_limit_mb=self._memory,
            network=False,
        )
        if code != 0:
            return {"ok": False, "error": stderr or stdout or "git show failed", "returncode": code}
        return {"ok": True, "content": stdout, "path": path, "rev": rev}

    async def _commit(self, params: dict[str, Any]) -> dict[str, Any]:
        message = (params.get("message") or params.get("msg") or "").strip()
        paths = params.get("paths") or params.get("files")
        if isinstance(paths, str):
            paths = [p.strip() for p in paths.split(",") if p.strip()]
        if not paths:
            paths = ["."]
        repo_dir = (params.get("repo_dir") or params.get("cwd") or "").strip()
        cwd = os.path.join(self._workspace, repo_dir) if repo_dir else self._workspace
        if not message:
            return {"ok": False, "error": "message is required for commit"}
        add_args = ["add"] + paths
        if not self._whitelist.is_allowed("git " + " ".join(add_args))[0]:
            return {"ok": False, "error": "git add not allowed"}
        code1, out1, err1 = await run_in_sandbox(
            ["git", "add"] + paths,
            cwd=cwd,
            cpu_limit_seconds=self._cpu,
            memory_limit_mb=self._memory,
            network=False,
        )
        if code1 != 0:
            return {"ok": False, "error": err1 or out1, "step": "add"}
        commit_args = ["commit", "-m", message]
        if not self._whitelist.is_allowed("git " + " ".join(commit_args))[0]:
            return {"ok": False, "error": "git commit not allowed"}
        code2, out2, err2 = await run_in_sandbox(
            ["git", "commit", "-m", message],
            cwd=cwd,
            cpu_limit_seconds=self._cpu,
            memory_limit_mb=self._memory,
            network=False,
        )
        if code2 != 0:
            if "nothing to commit" in (out2 + err2).lower():
                return {"ok": True, "message": "nothing to commit, working tree clean"}
            return {"ok": False, "error": err2 or out2, "step": "commit"}
        return {"ok": True, "stdout": out2, "message": message}

    async def _push(self, params: dict[str, Any], network: bool) -> dict[str, Any]:
        remote = (params.get("remote") or "origin").strip()
        branch = (params.get("branch") or params.get("branch_name") or "").strip()
        repo_dir = (params.get("repo_dir") or params.get("cwd") or "").strip()
        cwd = os.path.join(self._workspace, repo_dir) if repo_dir else self._workspace
        if not branch:
            return {"ok": False, "error": "branch is required for push"}
        args = ["push", remote, branch]
        ok, reason = self._whitelist.is_allowed("git " + " ".join(args))
        if not ok:
            return {"ok": False, "error": reason}
        code, stdout, stderr = await run_in_sandbox(
            ["git", "push", remote, branch],
            cwd=cwd,
            cpu_limit_seconds=self._cpu,
            memory_limit_mb=self._memory,
            network=network,
        )
        if code != 0 and not network:
            return {
                "ok": False,
                "error": "push requires network. Set SANDBOX_NETWORK_ENABLED=true.",
                "stderr": stderr,
            }
        return {"ok": code == 0, "returncode": code, "stdout": stdout, "stderr": stderr}

    async def _create_mr(self, params: dict[str, Any]) -> dict[str, Any]:
        repo = (params.get("repo") or params.get("repository") or "").strip()
        source_branch = (params.get("source_branch") or params.get("head") or "").strip()
        target_branch = (params.get("target_branch") or params.get("base") or "main").strip()
        title = (params.get("title") or "").strip()
        description = (params.get("description") or params.get("body") or "").strip()
        github_token = os.environ.get("GITHUB_TOKEN", "").strip()
        gitlab_token = (
            os.environ.get("GITLAB_TOKEN", "").strip()
            or os.environ.get("GITLAB_PRIVATE_TOKEN", "").strip()
        )
        result = await create_merge_request(
            repo=repo,
            source_branch=source_branch,
            target_branch=target_branch,
            title=title,
            description=description,
            github_token=github_token or None,
            gitlab_token=gitlab_token or None,
        )
        return result

    async def _list_repos(self, params: dict[str, Any]) -> dict[str, Any]:
        """Scan workspace for dirs with .git, return list of path + remote origin url."""
        if not os.path.isdir(self._workspace):
            return {"ok": True, "repos": []}
        repos: list[dict[str, str]] = []
        for name in sorted(os.listdir(self._workspace)):
            path = os.path.join(self._workspace, name)
            if not os.path.isdir(path):
                continue
            git_dir = os.path.join(path, ".git")
            if not os.path.exists(git_dir):
                continue
            # get remote url (whitelist checks subcommand only; path is our workspace)
            if not self._whitelist.is_allowed("git remote get-url origin")[0]:
                repos.append({"path": name, "remote_url": ""})
                continue
            code, stdout, stderr = await run_in_sandbox(
                ["git", "-C", path, "remote", "get-url", "origin"],
                cwd=self._workspace,
                cpu_limit_seconds=self._cpu,
                memory_limit_mb=self._memory,
                network=False,
            )
            remote_url = (stdout or "").strip() if code == 0 else ""
            repos.append({"path": name, "remote_url": remote_url})
        return {"ok": True, "repos": repos}

    async def _search_repos(self, params: dict[str, Any]) -> dict[str, Any]:
        """Search repos on GitHub (and later GitLab). platform=github|gitlab|both, query=..."""
        platform = (params.get("platform") or "github").strip().lower()
        query = (params.get("query") or params.get("q") or "").strip()
        if not query:
            return {"ok": False, "error": "query is required for search_repos"}
        if platform == "github":
            token = os.environ.get("GITHUB_TOKEN", "").strip() or None
            return await search_github_repos(query, token=token)
        if platform == "gitlab":
            token = (
                os.environ.get("GITLAB_TOKEN", "").strip()
                or os.environ.get("GITLAB_PRIVATE_TOKEN", "").strip()
                or None
            )
            return await search_gitlab_repos(query, token=token)
        if platform == "both":
            gh_token = os.environ.get("GITHUB_TOKEN", "").strip() or None
            gl_token = (
                os.environ.get("GITLAB_TOKEN", "").strip()
                or os.environ.get("GITLAB_PRIVATE_TOKEN", "").strip()
                or None
            )
            gh_out = (
                await search_github_repos(query, token=gh_token)
                if gh_token
                else {"ok": True, "items": [], "total_count": 0}
            )
            gl_out = (
                await search_gitlab_repos(query, token=gl_token)
                if gl_token
                else {"ok": True, "items": [], "total_count": 0}
            )
            items = (gh_out.get("items") or []) + (gl_out.get("items") or [])
            return {"ok": True, "items": items, "total_count": len(items)}
        return {"ok": False, "error": "platform must be github, gitlab, or both"}

    async def _git_subcommand(self, params: dict[str, Any]) -> dict[str, Any]:
        subcommand = params.get("subcommand") or params.get("action") or "status"
        args = params.get("args") or []
        if isinstance(args, str):
            args = args.split()
        repo_dir = (params.get("repo_dir") or params.get("cwd") or "").strip()
        cwd = os.path.join(self._workspace, repo_dir) if repo_dir else self._workspace
        raw = "git " + subcommand + " " + " ".join(str(a) for a in args)
        ok, reason = self._whitelist.is_allowed(raw)
        if not ok:
            return {"error": reason, "ok": False}
        cmd = ["git", subcommand] + list(args)
        code, stdout, stderr = await run_in_sandbox(
            cmd,
            cwd=cwd,
            cpu_limit_seconds=self._cpu,
            memory_limit_mb=self._memory,
            network=False,
        )
        return {
            "returncode": code,
            "stdout": stdout,
            "stderr": stderr,
            "ok": code == 0,
        }
