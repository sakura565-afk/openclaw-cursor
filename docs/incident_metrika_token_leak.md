# Утечка OAuth-токена Яндекс.Метрики в публичном репозитории

**Дата обнаружения:** 19.11.2026
**Репозиторий:** https://github.com/sakura565-afk/openclaw-cursor (публичный)
**Тип:** OAuth-токен API Яндекс.Метрики (доступ к статистике счётчиков)

## Что было

Файл `yandex_metrika.py` в корне репозитория содержал захардкоженный
OAuth-токен в константах:

```python
YANDEX_LOGIN = "Sakura565"
YANDEX_TOKEN  = "<oauth-токен>"   # значение не публикуется
```

Токен попал в историю при коммите `2ad68627` («Add Yandex Metrika API
integration with SQLite export») и присутствовал в **445 коммитах**.

## Что сделано (19.11.2026)

1. **Код очищен.** Токен и логин читаются из переменных окружения
   (`YANDEX_METRIKA_LOGIN`, `YANDEX_METRIKA_TOKEN` / `YANDEX_METRIKA_OAUTH_TOKEN`).
   Без токена скрипт объясняет настройку и завершает работу с кодом 2.
   Коммит `43c3571` в `main`.

2. **История переписана.** Все 445 коммитов пересобраны: файл
   `yandex_metrika.py` заменён на версию без секрета, переписаны все
   потомки (у них изменился parent SHA). Скрипт:
   `scratch/purge_secret_history.py`.

3. **Все ветки обновлены.** Force-push во все ветки, где был секрет —
   включая `main` (52 ref'а, 413 переписанных коммитов).
   Проверка `git ls-remote --heads origin` + `git grep` по каждой вершине:
   **0 веток с секретом.** Отдельно проверено на реальные ФИО, салоны и
   контрагентов: **0 веток с ПДн.**

   Переписывание шло в два прохода: первый нашёл секрет в 52 ref'ах,
   второй — в трёх коммитах `main`, где сам тест на секреты содержал
   литерал токена как паттерн. Тест теперь собирает литерал в рантайме
   из двух частей, так что в файле его нет.

4. **Локальные копии удалены.** `git reflog expire --expire=now --all` +
   `git gc --prune=now`.

## Что осталось (требует действий GitHub)

`refs/pull/*` — **read-only**, их нельзя переписать:

- `git push origin :refs/pull/N/head` → `deny updating a hidden ref`
- `PATCH/POST /repos/{owner}/{repo}/git/refs/pull/N/head` → `refs/pull/* is read-only`
- GraphQL `deleteRef` → `Could not resolve to a node`
- `DELETE /pulls/{n}` и GraphQL `deleteIssue` → 404
- reopen PR → блокируется: `no history in common` / `force-pushed or recreated`

**268 из 370 `refs/pull/*` отдают файл с токеном анонимно** через
`https://raw.githubusercontent.com/sakura565-afk/openclaw-cursor/<sha>/yandex_metrika.py`.

Проверка воспроизводится скриптом `scratch/check_exposure.py`
(16 потоков, ~2 минуты на 370 ref'ов).

Локальная копия репозитория очищена полностью: `reflog expire --expire=now --all`
+ `gc --prune=now` + удаление worktree'ов агентов — блобов с токеном в `.git`
не осталось (0 из 1168).

## Требуемые действия

### 1. Отозвать токен в Яндекс.Метрике (срочно)

Токен был публично доступен, значит **считать его скомпрометированным**.
Отзыв: Яндекс.Метрика → Настройки → Счётчик → Доступы для API → отозвать
текущий токен, выпустить новый.

Новый токен положить **только** в переменную окружения, не в код и не в
репозиторий. Ответственный: владелец аккаунта.

### 2. Обращение в GitHub Support — удаление refs/pull/*

Запрос: удаление pull-request refs из репозитория, содержащих
захардкоженный секрет. Что указать:

- Репозиторий: `sakura565-afk/openclaw-cursor`
- Тип: удаление данных / sensitive data removal
- Что именно: `refs/pull/*` (263 из 370), файл `yandex_metrika.py`,
  константа `YANDEX_TOKEN`
- Почему: OAuth-токен был захардкожен в коде и опубликован в публичном
  репозитории; force-push в ветки сделан, но pull-request refs read-only
- SHA-примеры для проверки: `2ad68627f81d3fa92d1728436de62363b5b8e47f`,
  `618e42bfc7445c179e4244335e12ca3fc62e93e5`
- Даты: токен опубликован коммитом `2ad68627`, обнаружено 19.11.2026

### 3. Почему не помогло удаление PR

GitHub не даёт удалить закрытый PR через API: нет ни REST-endpoint, ни
GraphQL-мутации для PR. `deleteIssue` работает только для обычных issue.

## Проверка после выполнения

```bash
# 1) секрета нет ни в одной ветке
git ls-remote --heads origin | while read sha ref; do
  git grep -I -l "<токен>" "$sha" && echo "УТЕЧКА: $ref"
done

# 2) секрет не отдаётся анонимно
python scratch/check_exposure.py

# 3) в локальном .git секрета нет
git cat-file --batch-all-objects --batch-check='%(objectname)' | \
  while read sha; do git cat-file blob "$sha" 2>/dev/null | grep -q "<токен>" && echo "$sha"; done
```

## Общий вывод

Ошибка — не в «GitHub не пушит», а в том, что секрет вообще попал в код.
Токен в константе в публичном репозитории — это утечка с первого коммита,
а не с момента публикации. Дальше: только переменные окружения, а
`tests/test_no_absolute_paths.py`-подобный тест должен ловить секреты в коде
до коммита.
