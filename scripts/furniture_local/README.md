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
| `workflow`: flux_fill | Flux Fill GGUF |
| `workflow`: sdxl + `sdxl_ckpt` | SDXL inpaint checkpoint |
| Смена обивки/цвета по умолчанию | `qwen_masked_inpaint` |
| `operation`: texture, hardware | Flux Fill GGUF |
| JSON в `comfyui_workflows/` (klein/face) | Только при явном `workflow: flux_klein_*` (не для мебели) |

Графы собираются в `workflow_builders.py` (API-формат), по аналогии с `scripts/qwen_masked_fix.py`.

## Рабочий рецепт (проверен на реальных фото)

Метрика ниже — B-R (blue minus red) в среднем по маске ткани: бежевая обивка ≈ −29, тёмно-синяя ≈ +40.

| Схема | Результат | Время |
|---|---|---|
| кроп 768×768 + **сплошная** маска | B-R **+41.7** — перекрасило | 30 с |
| кроп 768×768 + дробная маска | B-R −12.0 — осталось бежевым | 27 с |
| полный кадр 1264×848 + маска-полоса | B-R −16.9 — осталось бежевым | 300 с |

**Fill-модель перекрашивает только при сплошной большой маске.** Точные границы
обеспечивает не она, а paste-back по отдельной точной маске.

Порядок работы конвейера (`pipeline.py`):

1. `prepare_assets.py` — кроп 768×768 (нативно, без апскейла) + **точная** маска
   ткани по цвету (`upholstery_mask.py`: низкая насыщенность + тёплый тон +
   диапазон яркости, с отбрасыванием мелких компонент) + **сплошная** fill-маска.
2. Рендер каждого тайла: в ComfyUI уходит сплошная маска (для Flux Fill).
   SDXL-инпейнт получает точную маску — он перекрашивает внутри маски сам.
3. `paste_back.py` — сгенерированное вклеивается обратно по **точной** маске
   внутри кропа. Подушки, ножки, мрамор и стол остаются оригинальными.
4. `restore_crop` — тайлы возвращаются в полный кадр.
5. QA по полному кадру + триптих «вход | что делаем | выход» (`triptych.py`).

Если область шире 768 px (диван 955 px), `compute_tiles` режет её на
перекрывающиеся тайлы 768×768 — по одному кропу не покрыть.

```json
{
  "id": "sofa_navy",
  "reference_image": "sofa_beige_source.jpg",
  "prompt": "a modern L-shaped sectional sofa upholstered in deep navy midnight blue velvet, matte dense pile with visible nap",
  "mask": {"file": "sofa_precise_full.png"},
  "crop": {"target": 768, "bbox": [150, 445, 1105, 618], "overlap": 96},
  "seed": 11,
  "workflow": "flux_fill",
  "flux_denoise": 1.0, "flux_cfg": 3.5, "flux_steps": 20
}
```

### Эталон SDXL (рецепт из ТЗ FURN-SRC-20261122)

```json
"workflow": "sdxl",
"sdxl_ckpt": "juggernautXL_versionXInpaint.safetensors",
"sdxl_denoise": 0.85, "sdxl_cfg": 7.0, "sdxl_steps": 32,
"sdxl_sampler": "dpmpp_2m", "sdxl_scheduler": "karras", "sdxl_grow_mask_by": 16
```

### Пороги приёмки под тип задачи

Для смены обивки дефолтные пороги дают ложный FAIL: намеренная смена цвета
сдвигает Lab на 70–90 (дефолт `max_inside_lab_shift` = 18), а новая фактура
ткани даёт корреляцию градиентов ~0.15 (дефолт `min_gradient_correlation` = 0.55).
Поднимайте `max_inside_lab_shift`, опускайте `min_gradient_correlation`,
`max_outside_change_fraction` держите строгим — это настоящая проверка силуэта
(после paste-back вне маски меняется 0 пикселей).

## Машины

| Профиль | ComfyUI |
|---|---|
| `local` / `m1` | `http://127.0.0.1:8188` (RTX 4070 Ti SUPER 16 ГБ) |
| `m2` | `https://desktop-6ecjbt6.tailcaa609.ts.net/comfy` (RTX 3060 12 ГБ, tailscale serve) |

На M2 нет нод `InpaintConditionEncode` / `InpaintConditionApply` (в сборке
0.45.3 они называются `InpaintModelConditioning`), поэтому Flux Fill там
не перекрашивает — используйте SDXL-рецепт.

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