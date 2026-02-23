# Release notes

Файлы в этой папке — **шаблон тела (description) для GitHub Release**.

## Как делается релиз (CI)

При **пуше тега** вида `v*` (например `v0.2.3`) workflow **Release** (`.github/workflows/release.yml`) автоматически:

1. Создаёт GitHub Release с именем **Assistant Core &lt;тег&gt;**.
2. Берёт тело релиза:
   - если есть файл `vX.Y.Z.md` в этой папке — используется его содержимое;
   - иначе — заголовок по умолчанию.
3. Добавляет в конец блок **«Изменения по коммитам»**, собранный из коммитов между предыдущим тегом и текущим (по [Conventional Commits](https://www.conventionalcommits.org/); см. **docs/CONVENTIONAL_COMMITS.md**).

Тег должен совпадать с версией в `pyproject.toml` на момент релиза.

## Ручной релиз (если нужно)

1. На GitHub: Releases → Create a new release, укажите тег (например `v0.1.0`).
2. Заголовок: `Assistant Core v0.1.0`.
3. В описание можно скопировать содержимое `v0.1.0.md` и при необходимости добавить changelog по коммитам.
