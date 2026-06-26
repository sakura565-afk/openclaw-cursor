# SEO Title Generator

CLI-скрипт для генерации A/B вариантов `meta title` и `meta description` карточек товаров мебельных сайтов [amadey.ru](https://amadey.ru) и [divaninfo.ru](https://divaninfo.ru) через локальную [Ollama](https://ollama.com).

## Требования

- Python 3.11+
- Зависимости: `requests`, `tqdm` (см. корневой `requirements.txt`)
- Локально запущенная Ollama с моделью `mistral-nemo:latest`

## Установка Ollama и модели

### Linux / macOS

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve
```

В отдельном терминале:

```bash
ollama pull mistral-nemo:latest
ollama list
```

Проверка API:

```bash
curl http://localhost:11434/api/tags
```

### Windows

1. Скачайте установщик с [ollama.com/download](https://ollama.com/download).
2. Запустите Ollama из меню Пуск.
3. В PowerShell:

```powershell
ollama pull mistral-nemo:latest
curl http://localhost:11434/api/tags
```

## Формат входного CSV

Файл `data/sample_products.csv` — пример. Обязательные колонки:

| Колонка | Описание |
| -------- | -------- |
| `sku` | Артикул товара |
| `name` | Название |
| `category` | Категория (диваны, кровати, шкафы…) |
| `current_title` | Текущий meta title |
| `current_description` | Текущий meta description |
| `keywords` | Ключевые слова через запятую |

## Запуск

Из корня репозитория:

```bash
pip install requests tqdm
python scripts/seo_title_generator.py
```

С явными параметрами:

```bash
python scripts/seo_title_generator.py \
  --input data/sample_products.csv \
  --output data/seo_titles_custom.csv \
  --model mistral-nemo:latest \
  --batch-size 3 \
  --ollama-url http://localhost:11434
```

Параметры CLI:

| Параметр | По умолчанию | Описание |
| -------- | ------------ | -------- |
| `--input` | `data/sample_products.csv` | Входной CSV |
| `--output` | `data/seo_titles_YYYYMMDD_HHMMSS.csv` | Выходной CSV |
| `--model` | `mistral-nemo:latest` | Модель Ollama |
| `--batch-size` | `0` (все строки) | Сколько товаров обработать |
| `--ollama-url` | `http://localhost:11434` | URL Ollama API |
| `--timeout` | `180` | Таймаут запроса, сек |
| `--verbose` | выкл. | Подробные логи |

## Выходной CSV

Колонки: `sku`, `name`, `category`, `title_var_1`…`title_var_5`, `desc_var_1`…`desc_var_3`, `best_title_idx`, `best_desc_idx`, `scores`.

- **title** — до 60 символов
- **description** — до 160 символов
- **scores** — JSON: `{"title_scores": [...], "desc_scores": [...]}`, оценки CTR 1–10
- **best_*_idx** — номер лучшего варианта (1-based)

## Поведение при недоступной Ollama

Если `http://localhost:11434` не отвечает, скрипт:

1. Пишет warning в лог
2. Создаёт выходной CSV с пустыми вариантами
3. Завершается с кодом `0` (без падения)

Между запросами к Ollama — пауза **0.5 сек** (rate limit).

## Тесты

```bash
python -m unittest tests.test_seo_title_generator -v
```

Тесты мокают HTTP-вызовы к Ollama и не требуют запущенного сервера.

## Пример вывода в консоли

```
Generating SEO variants: 100%|██████████| 7/7 [00:45<00:00,  6.50s/product]

=== SEO Title Generator — summary ===
Products in input:     7
Successfully processed:7
Failed/skipped rows:   0
Average CTR score:     7.85
Output file:           data/seo_titles_20260626_143022.csv
```
