# llm_router — 4-слойный LLM-оркестратор

`src/llm_router/` — production-слой над наивными вызовами модели:
`classifier → cache → executor → judge`, плюс телеметрия и vision-ветка.

## Быстрый старт

```bash
# оффлайн-прогон на фикстурах, сеть не нужна
python -m src.llm_router.orchestrator --selftest

# агрегаты телеметрии (включая архивы)
python -m src.llm_router.orchestrator --summary

# один реальный запрос (нужен токен, см. «Авторизация»)
python -m src.llm_router.orchestrator --prompt "Посчитай hit rate по логам" --task analysis
```

```python
from src.llm_router import orchestrate

result = orchestrate("Сравни LRU и LFU для нашего профиля нагрузки", task="analysis")
print(result.tier, result.confidence, result.escalated, result.text)
```

## Слои

| Модуль | Отвечает за |
| --- | --- |
| `config.py` | `RouterConfig`, слияние `data/llm_router_config.json` + env `LLM_ROUTER_*`, поиск токена |
| `cache.py` | LRU + TTL, ключ `sha256(prompt + model + effort + image_hash)`, атомарная персистентность |
| `classifier.py` | 4 стратегии: `rule`, `embedding`, `llm`, `cascade`; результат — tier `fast`/`deep` |
| `executor.py` | ретраи (только 429/5xx), per-model таймауты, `parallel()`, `sequential_cascade()` |
| `judge.py` | confidence ответа, детект «не знаю»/«предположу», `needs_escalation()` |
| `telemetry.py` | append-only JSONL, ротация в `.jsonl.gz`, `summary()` |
| `vision.py` | прямой вызов `MiniMax-Vision-32B`, 451 как штатный фолбэк |
| `orchestrator.py` | фасад `orchestrate()` и `--selftest` |
| `transport.py` | HTTP на `urllib.request`, разбор ответа по блокам `type == "text"` |

## Текущий стек

* Основная модель — `MiniMax-M3.1-Flash`, провайдер `minimax-oauth`.
* Endpoint — `https://api.minimax.io/anthropic/v1/messages`, заголовок
  `anthropic-version: 2023-06-01`.
* Vision — **только прямым вызовом** `MiniMax-Vision-32B`: картинка лежит в
  `messages[0].content[0].source.data` (base64), `max_tokens = 6000` (при меньшем
  бюджете thinking съедает ответ и текстового блока в нём нет), парсинг — перебором
  блоков по `type == "text"`.
* HTTP 451 от vision-хелпера — не ошибка, а ветка: `is_vision_blocked()` распознаёт её,
  и запрос уходит в `vision_fallback()` (прямой MiniMax). Повторять заблокированный путь
  бессмысленно, поэтому ретраев там нет.

## Авторизация

Секретов в репозитории нет. Токен ищется по порядку:

1. env `MINIMAX_ACCESS_TOKEN`, `MINIMAX_API_KEY`, `LLM_ROUTER_ACCESS_TOKEN`;
2. hermes `auth.json` → `providers.minimax-oauth.access_token`
   (путь можно задать через `HERMES_AUTH_JSON`; по умолчанию проверяются
   `%LOCALAPPDATA%\hermes\auth.json` и POSIX-аналоги).

`data/llm_router_config.json` при загрузке проверяется на секретоподобные ключи
(`access_token`, `api_key`, ...) и JWT-строки — такой файл загрузить не получится.

## Артефакты

* `data/llm_cache.json` — опциональный персистентный кеш (`cache_persist: true`).
* `data/llm_router_telemetry.jsonl` — телеметрия; при превышении
  `telemetry_max_bytes` уезжает в `data/llm_router_telemetry/<ts>.jsonl.gz`.

Оба пути в `.gitignore`: это runtime-данные.

## Тесты

```bash
pytest tests/test_llm_router/ -v
```

Сеть в тестах запрещена: `tests/test_llm_router/conftest.py` подменяет
`urllib.request.urlopen` на падающую заглушку.
