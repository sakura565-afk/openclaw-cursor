# Ручные измерители (не pytest)

В `pyproject.toml` задано `testpaths = ["tests"]`, поэтому файлы в `scripts/` **не собираются** pytest, даже если имя начинается с `test_`. Скрипты ниже намеренно названы `measure_*.py`, чтобы не путать их с CI-тестами.

Оба скрипта требуют **живой ComfyUI** на `127.0.0.1:8188` (или `COMFYUI_PROD`), реальные кадры/модели и **не** запускаются в CI. Пути — только через окружение (`HERMES_SCRATCH_DIR`, `COMFYUI_PROD_DIR`, `HERMES_NATIVE_INPUTS`) или относительные пути в аргументах.

## `measure_bc_temporal_stability.py`

**Что измеряет:** мерцание слоя BC_SkinTexture при покадровой обработке видео.

- **A (fixed_seed):** один и тот же seed на каждый кадр — поле пор стабильно в экранных координатах (нет мерцания инжекта), но поры «плывут» относительно субъекта.
- **B (per_frame_seed):** новый seed на кадр — типичное мерцание, о котором пишут в community.

**Метрика:** `temporal_delta` — среднее |кадр[i+1] − кадр[i]| по инжектированному слою; сравнивается с дельтой входа и амплитудой инжекта.

```bash
export COMFYUI_PROD_DIR=/path/to/ComfyUI_windows_portable
export HERMES_SCRATCH_DIR=/path/to/scratch
python scripts/measure_bc_temporal_stability.py
```

Подробности — докстринг в начале файла.

## `measure_identity_lora.py`

**Что измеряет:** влияние обученной identity LoRA на Qwen-Image 2.1 на **validation**-кадре (не из обучающей выборки).

Три рендера с одинаковым seed и промптом:

| Вариант | LoRA |
|--------|------|
| A | без LoRA |
| B | LoRA, полная сила |
| C | LoRA, пониженная сила |

```bash
export COMFYUI_PROD_DIR=/path/to/ComfyUI_windows_portable
python scripts/measure_identity_lora.py \
  --lora katya_identity_qwen21.safetensors \
  --source /path/to/validation_frame.png \
  --trigger katya_person \
  --out-dir /path/to/output
```

Выходные PNG и сводка печатаются в `--out-dir`.

## Guard в CI

Тест `tests/test_no_pytest_files_outside_tests.py` падает, если в репозитории (вне `tests/`) появляется `test_*.py` или `*_test.py`, кроме явного белого списка в том же файле.
