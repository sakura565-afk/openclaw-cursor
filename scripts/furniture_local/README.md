# Локальный мебельный inpaint (ComfyUI)

Конвейер без облачных API: маска ткани → inpaint на **локальном** ComfyUI (M1/M2) → автоматическая приёмка с коллажом **«референс | результат»**.

## Зависимости

Из корневого `requirements.txt` уже есть **numpy** и **pillow**. Дополнительно на машине с ComfyUI:

- Ноды Qwen Image Edit (`UNETLoader`, `VAEEncodeForInpaint`, …) — как в `scripts/qwen_masked_fix.py`
- Опционально: **ComfyUI-GGUF** + Flux Fill, **BrushNet** на M2

## Переменные окружения (модели и URL)

| Переменная | Назначение |
|------------|------------|
| `FURNITURE_COMFY_URL` / `COMFYUI_PROD_URL` | Локальный ComfyUI (по умолчанию `http://127.0.0.1:8188`) |
| `FURNITURE_COMFY_M2_URL` | M2 через Hermes Funnel (HTTPS) |
| `FURNITURE_M1_QWEN_UNET`, `FURNITURE_M1_FLUX_FILL`, … | Имена файлов моделей на M1 |
| `FURNITURE_M2_*` | Аналогично для RTX 3060 |
| `QWEN_EDIT_MODEL`, `QWEN_EDIT_VAE`, … | Fallback как в qwen_masked_fix |

Жёсткие пути диска **не** зашиты — только имена checkpoint в папке `models/` ComfyUI.

## Формат `task.json`

```json
{
  "id": "sofa_blue_velvet",
  "reference_image": "path/to/sofa.jpg",
  "furniture_type": "sofa",
  "operation": "reupholstery",
  "comfy_host": "local",
  "prompt": "same sofa silhouette, blue velvet upholstery, realistic seams, studio photo",
  "mask": {
    "bbox": [120, 80, 520, 420],
    "feather": 12
  },
  "seed": 42,
  "workflow": "auto"
}
```

Источники маски (один из):

- `"mask": {"file": "mask.png"}` — готовая PNG
- `"mask": {"bbox": [x0,y0,x1,y1], "feather": 8}` — прямоугольник + размытие (PIL GaussianBlur)
- `"mask": {"target_rgb": [90, 70, 50], "color_tolerance": 24}` — порог по цвету (numpy; без numpy — медленный PIL fallback)

## Запуск

```bash
# Справка
python -m scripts.furniture_local.pipeline --help

# Полный прогон (нужен живой ComfyUI)
python -m scripts.furniture_local.pipeline task.json -o artifacts/furniture_local/sofa_run

# Только маска + выбор workflow
python -m scripts.furniture_local.pipeline task.json --dry-run

# Уже есть результат с ComfyUI — только QA и коллаж
python -m scripts.furniture_local.pipeline task.json --qa-only -o out/
```

В `task.json` для `--qa-only` / `--skip-comfy` добавьте `"result_image": "path/to/result.png"`.

## Выбор workflow (`pick_workflow`)

| Задача | Движок |
|--------|--------|
| Смена обивки/цвета по умолчанию | `qwen_masked_inpaint` |
| `operation`: texture, hardware или `workflow`: flux_fill | Flux Fill GGUF |
| M2 + `workflow`: juggernaut / color | SDXL inpaint checkpoint |
| JSON в `comfyui_workflows/` (klein/face) | Только при явном `workflow: flux_klein_*` (не для мебели) |

Графы собираются в `workflow_builders.py` (API-формат), по аналогии с `scripts/qwen_masked_fix.py`.

## Приёмка (QA)

Критерии владельца (пороги в `QAThresholds` / `task.qa_thresholds`):

- Изменения **вне** маски → FAIL (силуэт, ножки)
- Слишком мало изменений **внутри** маски → FAIL
- Корреляция лапласианов в маске → сохранение швов/фактуры
- Обязательный файл: `reference_result_collage.png`

Переиспользуется `laplacian` из `scripts/eval_judge/metrics.py`.

## Тесты (без сети и GPU)

```bash
python -m pytest tests/test_furniture_local_mask.py \
  tests/test_furniture_local_qa.py \
  tests/test_furniture_local_comfy_client.py -q
```