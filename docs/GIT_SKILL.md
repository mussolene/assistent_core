# Git + GitLab/GitHub skill

Встроенный скилл `git` позволяет агенту работать с репозиториями: клонировать, читать файлы по ревизии, коммитить, пушить и создавать Merge Request (GitLab) или Pull Request (GitHub).

## Действия

| Действие | Параметры | Описание |
|----------|-----------|----------|
| **clone** | `url`, `dir?` | Клонирует репозиторий в рабочую директорию. Требует `SANDBOX_NETWORK_ENABLED=true`. |
| **read** | `path`, `rev?`, `repo_dir?` | Читает файл из репо по ревизии (по умолчанию HEAD). |
| **list_repos** / **list_cloned** | — | Список склонированных репо в workspace (path, remote_url). |
| **search_repos** | `query`, `platform?` (github \| gitlab \| both) | Поиск репо на GitHub (и позже GitLab). Нужен GITHUB_TOKEN. |
| **status**, **diff**, **log** | `subcommand`, `args?` | Стандартные git-команды в контексте репо. |
| **commit** | `message`, `paths?`, `repo_dir?` | Добавляет файлы и создаёт коммит. |
| **push** | `remote?`, `branch`, `repo_dir?` | Пушит ветку. Требует сеть. |
| **create_mr** | `repo`, `source_branch`, `target_branch`, `title`, `description?` | Создаёт MR (GitLab) или PR (GitHub) через API. |

`repo` в **create_mr** — URL репозитория (`https://github.com/owner/repo` или `https://gitlab.com/owner/repo`) или строка `owner/repo`. Платформа определяется по URL; если передано `owner/repo`, при наличии обоих токенов сначала пробуется GitHub.

## Переменные окружения

- **GITHUB_TOKEN** — Personal Access Token для создания PR (GitHub). Права: `repo` или минимум создание PR.
- **GITLAB_TOKEN** или **GITLAB_PRIVATE_TOKEN** — токен для GitLab API (создание MR).
- **SANDBOX_NETWORK_ENABLED** — `true`, чтобы разрешить clone и push из песочницы (по умолчанию сеть в песочнице отключена).

Токены задаются в `.env`; в репозиторий не коммитить.

## Безопасность

- Клонирование и push выполняются только при включённой сети для песочницы.
- Ограничения песочницы (CPU/RAM, изоляция) применяются к запуску git.
- Для ограничения списка разрешённых репозиториев можно добавить allowlist в конфиг (см. `config/default.yaml` и скилл `git`).

## Пример сценария

1. Агент получает задачу: «скачай репо X, посмотри README, добавь в конец строку Y и создай MR в main».
2. `git.clone(url=X, dir=repo_x)`.
3. `filesystem.read(path=repo_x/README.md)` или `git.read(path=README.md, repo_dir=repo_x)`.
4. `filesystem.write(path=repo_x/README.md, content=...)`.
5. `git.commit(message="Add Y to README", repo_dir=repo_x)`.
6. `git.push(branch=feature/readme-y, repo_dir=repo_x)`.
7. `git.create_mr(repo=X, source_branch=feature/readme-y, target_branch=main, title="Add Y to README", description="...")`.

Системный промпт ассистента описывает эти действия, чтобы модель вызывала нужные вызовы инструментов.
