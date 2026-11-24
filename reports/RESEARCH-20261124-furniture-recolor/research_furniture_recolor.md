# Как эксперты делают перекраску обивки на фото и на чём

Три подхода, которые встречаются в практике.

## 1. Классический ретушёрский (Photoshop) — самый контролируемый
Выделить объект, Hue/Saturation со сдвигом Hue: «Because Hue/Saturation keeps the original light and shadow, the result holds its texture». Для сильной смены цвета — Solid Color в режиме наложения Color, замаскированный на объект: даёт больше контроля [7].
Ключевая идея та же, что и в моей детерминированной попытке: менять цвет, не трогая свет и тень.

## 2. Специализированные сервисы замены ткани
Morphic: «The swap preserves the original item's shape, creases, lighting, and shadow behavior while applying a new material» — анализ геометрии поверхности, направления света и теней, затем физически корректное наложение [6].
RoomLab/others: часть умеет и мебель, и материалы (пол, стена, обивка), часть — только мебель и не трогает отделку [8].

## 3. Генеративное редактирование — актуальный фронт
Лидерборд по правке изображений (ноябрь 2026): GPT Image 2.6 Sunburst (max) — 1263 Elo; MAI-Image-2.7 — 1215; GPT Image 2 (high) — 1195; Nano Banana 2.1 — 1175; Seedream 5.5 — 1134 [4].
Recraft: «There is no single best AI photo editor. Photoshop still wins pixel-level control and layered composites» [5].

## Что из этого важно именно для нашей задачи
Бенчмарк VeriEdit-Bench (октябрь 2026, 1740 кейсов, 11 редакторов, детерминированная оценка по 4 осям): «even the strongest model remains far from full credit» [1]. То есть даже лучшие модели 2026 года не дают чистой локальной правки.

Целевой цвет: Paint-Anything (ByteDance Seed, 19.11.2026) читает hex прямо из промпта; на FLUX.2-4B даёт +85.3% (генерация) и +28.3% (редактирование) к базовой модели по новому бенчмарку ACBench [3]. Но: «Public inference code and checkpoints needed to run these methods on ACBench were unavailable at evaluation time» [3] — весов нет, использовать нельзя.

ColorCtrl (training-free, ICLR 2026): разделяет albedo, цвет источника света и ambient, манипулируя attention в MM-DiT; «our method surpasses strong commercial models such as FLUX.1 Kontext Max and GPT-4o Image Generation in terms of consistency» [2]. Работает на SD3 и FLUX.1-dev — обе у нас есть.

## Что уже стоит на этой машине
M1 = RTX 4070 Ti SUPER, 16 ГБ. Установлено: qwen_image_2.1_int8_convrot (7.3 ГБ), qwen_image_2.1_vae_bf16, qwen3vl_8b_int8, qwen_image_inpaint_diffsynth_controlnet. В ComfyUI 0.45 есть ноды TextEncodeQwenImage21, VAEEncodeForInpaint, QwenImageDiffsynthControlnet. Есть готовые скрипты scripts/qwen21_masked_refine.py, qwen_edit_run.py, qwen_masked_fix.py.
Также FLUX.1 Fill (GGUF Q8) — мы его гоняли.

## Вывод
Для нашей задачи правильный путь — гибрид: Qwen-Image-2.1 int8 в режиме masked inpaint (уже установлен, 16 ГБ VRAM хватает) + детерминированная перекраска как гарантия точного цвета. Плюс классическая схема Solid Color/Color из [7] как финальная доводка края.

## Sources

[1] https://arxiv.org/abs/2610.04381
[2] https://arxiv.org/abs/2508.09131
[3] https://arxiv.org/abs/2609.20816
[4] https://artificialanalysis.ai/image/leaderboard/text-to-image
[5] https://www.recraft.ai/blog/best-ai-photo-editors
[6] https://morphic.com/resources/how-to/swap-fabric-texture-on-furniture-photos
[7] https://abrush.ai/en/blog/color-retouching
[8] https://roomlab.app/en/blog/best-ai-furniture-material-swap-tools
