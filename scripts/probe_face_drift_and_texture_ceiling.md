# Проба: «плавающее лицо» в хвосте клипа + потолок микротекстуры в видео

Дата пробы: 2026-11-03. Стойка: ComfyUI 0.43.0, RTX 4070 Ti SUPER 16 ГБ, H3 (fl2va/ref2va, int8-convrot).
GPU во время пробы не трогали (занят рендером Tessera).

Метод: чтение первоисточников (репозитории, HF-модели, arXiv), локальная проверка установленных паков и
моделей, кросс-проверка нашими же измерениями (face MAE, поры, мерцание).

Легенда: **[П]** подтверждено источником · **[О]** наша оценка/перенос · **[?]** неизвестно, нужно замерить.

---

## ЧАСТЬ 1. Плавающее лицо (identity drift в хвосте клипа)

### 1.1. Главная находка: у H3 есть два РАЗНЫХ чекпоинта, и только один «приземляется» на кадр

**[П]** Главное открытие, найденное в карточке модели
[`joeygambino/MiniMax-H3-comfy-native-fl2va`](https://huggingface.co/joeygambino/MiniMax-H3-comfy-native-fl2va),
а также в [`joeygambino/MiniMax-H3-Multishot-Workflow`](https://huggingface.co/joeygambino/MiniMax-H3-Multishot-Workflow):

> «**fl2va lands on a supplied frame and ref2va only nudges toward one.** Measured against the same frame:
> **fl2va — 26.35 dB**, **ref2va + keyframe, turbo 6 steps — 16.15 dB**, **ref2va + keyframe, stock 20 steps — 16.81 dB**.
> The stock-20-step control rules out the sampler. The checkpoints genuinely differ, by about 10 dB.
> `ref2va` treats a keyframe as a suggestion; `fl2va` treats it as the task.»

То есть разница ~10 dB — это не сэмплер и не число шагов, а сама архитектура/обучение чекпоинта.
`ref2va` = Ref2VA (омни-референс: ≤9 картинок, ≤3 видео, ≤3 аудио), `fl2va` = First/Last-Frame VA
(ноль/одна/две картинки). **[П]** Подтверждено документацией MiniMax:
[`H3-Base-FL2VA` vs `H3-Base-Ref2VA`](https://huggingface.co/MiniMaxAI/MiniMax-H3) — FL2VA «supports zero, one, or two input images»,
Ref2VA — мультимодальные референс-строки.

**Практический вывод для нашего стенда [О]:** наша текущая связка (ref2va + MiniMaxH3AddGuide) — это ровно
тот случай, где ключевой кадр работает как «подсказка», а не как «прибивка». Отсюда и хвостовой дрейф:
мы подпираем identity-LoRA + guide-кадром, но модель физически не «прибита» к кадру.

**[П] У нас уже лежит нужный чекпоинт:**
`models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors` (20 ГБ) — тот самый int8-convrot,
тот же формат, что используем сейчас. Проверено локально (`ls` по models/diffusion_models).
Т.е. переключение ref2va → fl2va на нашей карточке = ноль скачиваний.

**Важные ограничения fl2va [П]:** «`fl2va` ignores `voice_ref`, `self_anchor_voice` and the identity bank entirely».
То есть fl2va — это чистый кадровый якорь, без референс-строк. Для нашего кейса (лицо + тело одного
человека, без разных голосов) это приемлемо, а для сцен с несколькими персонажами/голосами — нет.

**Побочная находка [П]:** «fl2va and ref2va are **byte-identical in size** at every quantisation. "Lighter" only ever
meant fewer tokens per sampling step, because there are no reference rows riding along.» — то есть
переключение не стоит ничего по VRAM, а даже выигрывает в скорости (меньше токенов на шаг).

### 1.2. Наш измеренный факт vs их измеренный факт

**[О]** Наши цифры: face MAE на кадре 130 = 33.6 против первого кадра; после identity-LoRA → 21.0; после
guide-кадра на кадре 87 → **5.0**. Их измерение: ref2va+keyframe ≈ 16 dB против fl2va ≈ 26 dB.
Оба числа согласуются: сильный guide-кадр в ref2va дал нам -84% MAE (33.6 → 5.0), т.е. эквивалент
примерно +10 дБ прироста точности попадания в кадр — ровно величина, которую показывает их разница
чекпоинтов. **[О]** Это независимое подтверждение того, что оставшийся хвостовой дрейф у нас — это именно
«ref2va не прибивается к кадру», а не ошибка настройки LoRA.

### 1.3. Ре-инъекция guide-кадра: какой кадр, какой вес

**[П] Механика AddGuide** (прочитан локально в `comfy_extras/nodes_minimax_h3.py`, класс `MiniMaxH3AddGuide`):
`frame_idx` — индекс кадра якоря, **отрицательные значения считаются от конца видео**
(`resolved_frame_index = frame_count + frame_idx`). Гайд-клип кропается до валидной длины H3
(5, 22, 39… = 17k+5); батчи < 5 кадров берут только первый кадр. Несколько узлов можно chain'ить
для нескольких якорей.

**[П] Что работает в экосистеме:**
- `head_swap` LoRA от Alissonerdx ([`docs/head-swap.md`](https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI/blob/main/docs/head-swap.md))
  требует ровно этой схемы: **референс-лицо → нативный image-reference канал**, **полный исходный видео-клип → AddGuide при `frame_idx = 0`**. Промпт-шаблон прямо запрещает цели: «Prevent identity drift, facial morphing, hairstyle changes, head-size fluctuations, geometry instability, texture crawling, temporal flickering».
- `LMS` LoRA ( sharpness/detail) — тоже работает через AddGuide при `frame_idx = 0`, второй проход поверх готового клипа.

**[П] Гайд-кадр в середине клипа: ограничение, найденное в Tessera LAWS.md** (Spot-anchor law, runs 183/186/189):
> «only frame 0 relays detail forward on its own; a mid-clip 1-frame splice is a ±2 frame blip — VAE packing averages it away
> and mid-guide detail does not propagate.»

**Это прямо про наш хвостовой дрейф [О]:** одиночный guide-кадр в середине клипа (наш кадр 87) НЕ распространяет
identity дальше ±2 кадров. Он чинит локальный участок вокруг себя, но не «держит» лицо до конца клипа.
Отсюда и наше наблюдение: MAE упал до 5.0 в окрестности guide-кадра, но хвост (130) продолжает плыть.

**[П] Как это лечится (тот же источник, v4 detail relay):**
1. `TesseraAnchor` (Guide Anchor) — сплайс детального still'а в fine-band гайда, `spread` разносит деталь в обе стороны;
   `spread` — чистый линейный taper от кадра якоря (r73), дефолт **4**.
2. `guide_inherit` на Tessera Refine — каждый следующий temporal chunk наследует **уже отрендеренный** латент
   как истину. Измерено: **+60% детали по всему клипу, ПЛОСКО от f73 до f110** («the decay that defined every
   corruption-based approach is gone»), «the detail ceiling is now set by a single still from any image pipeline,
   and the video model propagates it».

**[О] Прямой ответ на нашу задачу:** identity/деталь надо протягивать **рекурсивно по чанкам**, а не одним
серединным кадром. Guide-ре-инъекция «в середину» — правильная идея, но её надо делать **на каждом чанке**
с наследованием от предыдущего (механизм `guide_inherit`), а не один раз.

**[П] Параметры для нашего случая (Tessera validated recipe v3):**
```
border_mode=overlap  seam_regen=32  seam_direction=forward  seed_mode=global
context_border=64    ref_crop=per_tile  image_ref_crop=per_tile  guide_noise=0
guide_release=0.8    frame_match=dc  motion_adapt=on  motion_release_floor=0.0
fine_below_sigma=0.75  sampler_fine=euler
sigmas = 1.0, 0.7, 0.4, 0.0   (1 turbo head + 2 euler tail)
clip подключён, prompt = промпт ref-ноды
```
**[П] Когда используем anchor — `motion_release_floor = 0.3`** («House setting WHEN ANCHORING»), плюс:
> «RELAY-RELEASE LAW: the anchor relay IS the model re-rendering the anchor texture in its released tail steps.
> with motion_adapt clamping release to 0.00 the relay cannot run and the splice degrades to a visible crossfade
> (head adj-corr 0.90 default vs 0.96-0.97 with release freedom; the same mechanism measured 0.67 at 4x).»

**[П] SPLICE-SCALE LAW:** сплайс anchor'а работает только на **2x**. «At 4x+ leave the anchor input UNPLUGGED:
the no-splice control showed a perfectly smooth head (adj corr 1.00 from f1) at the same head detail as the anchored run's
steady state — the Qwen still's value at big hops flows through the ReferenceToVideo attention ref instead.»

### 1.4. Last-frame chaining (чанки с перекрытием)

**[П] Реализация, которую можно взять целиком:** [`ComfyUI-H3-Multishot` / LaDruid MiniMax-H3-Multishot-Workflow v2.7.0](https://huggingface.co/LaDruid/MiniMax-H3-Multishot-Workflow).

Механизм `first_frame` chain:
> «Each shot's **last frame** is handed to the next shot as its **first frame**, through `fl2va`'s trained continuation task.
> That is the model doing what it was trained to do, not a hand-rolled trick. The duplicated boundary frame is trimmed,
> and the seam audio gets a 40 ms equal-power weld.»

Механизм `context_pin` (глубже):
> «The previous shot's **last 22 frames ride into the next shot as raw latents** — bit-identical, with no VAE round trip —
> placed at interior keyframe coordinates... The model regenerates that overlap as its own head; the regenerated 0.92 s is
> trimmed on decode, so what survives is the original tail followed by continuous new material.»

**[П] Прямо релевантные нам замеры того же пака:**
- `seed_per_shot` — **оставить ON**: «per-shot seeds hold the face; a single seed shared across all shots drifted both face and voice.» **[О]** Для нас: наш текущий single-seed клип — риск; надо per-shot/per-chunk seed.
- `chain_gain_control=flatten` — «Texture ratchets about **1.3x per join** otherwise, so late shots come out visibly over-sharpened.»
- **Texture ratchet +13% fine texture per join** at 736x1280 with anti-drift on; «Under 4 windows (30-40 s) it is slight; at 7 windows it is visible sharpening.»
- `color_level` — уровни к shot 1's settled tail (фиксированный референс; «matching neighbour-to-neighbour re-accumulates drift»).
- **Правила промпта:** ~2 тихих секунды в начале каждого чанка (AIRLOCK), первые ~1 сек каждого chained shot — «discarded replay», LAND SETTLED в конце, «Repeat verbatim» описание персонажа в каждом шоте.

**[П] Ключевой механизм удержания identity без референсов:**
> «1. The frame relay. Every shot after the first begins from an actual rendered picture of the character. Faces and wardrobe
> propagate as *pixels*, not as a re-imagining from text. 2. Byte-identical text. The prompt writer repeats each character's
> appearance block **verbatim** in every shot. The frame pins the specific instance; the repeated text pins the category...
> This was verified on a 40-second two-character scene with **zero reference images supplied**.»

**[О] Вывод:** last-frame chaining через **fl2va** (last frame → next first frame) — это самый дешёвый и самый
прямой способ убрать хвостовой дрейф: identity переносится пикселями, а не пересказом. Наш стенд уже имеет
fl2va-чекпоинт.

**[П] Ограничение multishot-подхода:** «the chain's texture ratchet is not fully solved for long takes», и
«Every booster except block cache changes what people look like» — cache-бустеры (TeaCache/Spectrum/EasyCache)
дают -14..-29% скорости, но «can distort people». **[О]** У нас GPU занят, скорость не критична — не включать.

### 1.5. Per-frame face-swap (ReActor) — почему у нас пластик

**[П]** Рекомендации по ReActor ([atlascloud.ai](https://www.atlascloud.ai/blog/tips/comfyui-face-swap), [runcomfy](https://www.runcomfy.com/comfyui-nodes/comfyui-reactor-node/re-actor-face-swap)):
`face_restore_model=GPEN-BFR-512` (альтернатива CodeFormer), `face_restore_visibility=0.7-0.8`
(«Full strength looks airbrushed»), `codeformer_weight=0.5-0.75`.

**[П]** Ограничения самого метода ([ReActor discussion #215](https://github.com/Gourieff/ComfyUI-ReActor/discussions/215)):
> «This technology working good ONLY on faces which look straight forward... If face looking to the side (90° turn) — it's not work at all.»

**[П]** Документация Temporal Face Detailer (SDXL video) формулирует проблему точно:
> «If you've ever run Impact Pack's `FaceDetailer` on every frame of a video, you already know why this node exists.
> Each frame gets a fresh img2img pass with fresh noise, the face sharpens up nicely — and then morphs, shimmers, or
> straight-up changes identity a few frames later.»

**[П]** Reddit (r/comfyui, «Most Optimal Face Refining Workflow for H3»):
> «using per-frame face detailers (like cropping, detailing, and pasting back) can lead to flickering in video, as each frame is
> adjusted individually. If you choose this method, it's important to keep the denoise level low and evaluate it while in motion
> rather than relying on still images.»

**[О]** Вывод: ReActor у нас даёт пластик закономерно — `face_restore_visibility` 0.7-1.0 + GPEN/CodeFormer перерисовывают
кожу и убивают текстуру, а per-frame пасте = flicker. **Per-frame face-swap НЕ должен быть основным инструментом
борьбы с хвостовым дрейфом.** Его правильная роль — только emergency-починка 1-2 конкретных кадров в самом хвосте.

### 1.6. ComfyUI-H3-FaceRefine (Carasibana, ★534) — что это и решает ли он хвостовой дрейф

**[П]** Репозиторий: [`Carasibana/ComfyUI-H3-FaceRefine`](https://github.com/Carasibana/ComfyUI-H3-FaceRefine) (MIT, создан 2026-08-12, ★534).
Установлен локально: `custom_nodes/ComfyUI-H3-FaceRefine/`.

**Заявленная проблема (цитата из README):**
> «MiniMax H3 renders faces poorly when the head occupies a small fraction of the frame. **This is a property of
> head-size-in-frame, not of output resolution**, so it persists at 720p and above. These nodes detect the face on every frame,
> crop to it so it fills a canvas, let H3 re-generate it, and composite the result back into the original video.
> Modelled on Impact Pack's FaceDetailer, adapted from stills to video.»

**[П] Ключевое ограничение, о котором честно пишет сам автор:**
> «These two are what to watch for temporal behaviour: **the box has to sit still on a moving subject, or the refined face boils.**»

**[О] Ответ на вопрос «решает ли он именно хвостовой дрейф»:**
**Нет, не решает.** Это инструмент для *мелких* лиц (head-size-in-frame), а не для identity-drift в хвосте.
У нас наоборот: лицо в кадре достаточно крупное (мы генерим 704-736×1056-1120, потом апскейлим), проблема не в размере лица,
а в том, что модель к концу клипа меняет черты. FaceRefine это не адресует по построению: он перегенерирует
лицо из выбранного кадра, а не «прибивает» identity.

**[П] Что у FaceRefine реально полезно для нас (параметры, прочитанные локально из README + `nodes.py`):**

| узел | параметр | дефолт | что делает |
|---|---|---|---|
| H3 Per-Frame Denoise | `denoise_multiplier_small_face` | `1.0` | множитель denoise там, где лицо мелкое |
| | `denoise_multiplier_large_face` | `0.35` | множитель там, где лицо крупное (сохраняет детали) |
| | `face_px_small` / `face_px_large` | `30` / `120` | высота лица в source px, границы кривой |
| | `gamma` | `1.0` | форма кривой |
| | `smooth_frames` | `9` | сглаживание кривой по времени (скачки denoise = texture pop) |
| H3 Face Stitch Back | `paste_region` | `face_only` | композит только бокса лица (не весь кроп) |
| | `mask_dilation` | `16` | расширить бокс перед размытием |
| | `feather` | `6` | размытие маски в source px; **~24 с rect-маской, 4-8 с SAM** |
| | `colour_match` | `1.0` | матчить mean/std кропа к региону |
| | `blend` | `1.0` | глобальная непрозрачность рефайна |
| | `undetected_frames` | `fade_out` | на пропусках — затухание композита |

**[П] КРИТИЧНО: denoise в H3 — это НЕ denoise из SDXL.** Формула: `sigma = shift*t/(1+(shift-1)*t)`, при shift 12:

| denoise | effective sigma (shift 12) |
|---|---|
| 0.02 | 0.197 |
| 0.05 | 0.387 |
| 0.15 | ~0.68 |
| 0.25 | 0.800 |

> «At the default shift of 12, `0.25`, an ordinary FaceDetailer value, lands at an effective sigma of **0.800** and rewrites the frame.»

**[П]** Базовый denoise в примерах пака — **0.40**, и `denoise_multiplier_large_face=0.35` его масштабирует вниз.
**[О]** Если бы мы взяли FaceRefine с нашими привычными 0.25-0.35 — получили бы effective sigma 0.8+ и перезапись
лица целиком. Это и есть источник «пластика», если FaceRefine применять с неверным denoise.

**[П]** Ещё: `steps` и `denoise` в H3 независимы (`BasicScheduler` строит полный диапазон и оставляет низшие sigmas).
**Не использовать `SplitSigmas`** («On a 4-step schedule at shift 12 even the last split point is already sigma 0.800»).
**[П]** Turbo LoRA от Kijai/lightx2v: 8-step v1.0, strength 1.0, BasicScheduler = 8 steps (число шагов обязано соответствовать turbo-LoRA).
**[П]** Стоимость: `canvas² × frames`; на длинном видео canvas может не влезть в VRAM → стриминг весов из RAM = замедление на порядок.

**[П]** Frame count снапится до сетки H3 (5, 22, 39… 175, 226, 362).

**[П]** Ещё важно из README: трекер «не прыгает на другого человека» (версия 1.1.2), при потере субъекта кадры
остаются unresolved, композит затухает, `lost:` в отчёте.

**[П]** Модель детектора: `buffalo_l` (ArcFace) через InsightFace SCRFD; `identity_threshold` 0.28 для InsightFace,
~0.80 для clip_vision, 0.5 для CCIP; `identity_model` ∈ {insightface, clip_vision, ccip}.

**[О] Вердикт по FaceRefine:** полезен нам как **инструмент починки мелких лиц** (если в кадре лицо < ~120 px по высоте),
но **не как средство от хвостового дрейфа**. Если применим — только с denoise ≤0.10 (effective sigma ≤0.55) на крупных лицах,
`feather` 4-8 с SAM-маской или ~24 с rect, `blend` 0.8-1.0, `smooth_frames` ≥9.

### 1.7. Identity-LoRA / IP-Adapter / InstantID / PuLID для видео — работает ли вообще

**[П] IP-Adapter / InstantID / PuLID для H3 — НЕ поддерживаются.**
Проверено локально: в `custom_nodes/ComfyUI_IPAdapter_plus/` нет ни одного упоминания `minimax`/`H3`
(grep по всем .py — 0 совпадений). `ComfyUI_InstantID` установлен, но это SD1.5/SDXL-пайплайн.

**[П] Почему (архитектурно, из [Stand-In, arXiv:2508.07901](https://arxiv.org/html/2508.07901v4)):**
Stand-In вводит conditional image branch + **restricted self-attention** + **conditional position mapping**
(3D RoPE, temporal index −1 для reference-токенов, disjoint spatial subspace) + LoRA rank 128 **только на QKV
проекции image-токенов** в каждом DiT-блоке. Все существующие image-identity методы (InstantID, PuLID) —
face-encoder + cross-attention; они тренировались под T2I и в H3-архитектуру не втыкаются.
Stand-In база — Wan2.1-14B-T2V.

**[П] Цифры Stand-In** (user study + ablation, FaceSim ↑ / Naturalness ↑):

| Method | Face Similarity | Video Quality |
|---|---|---|
| Hunyuan-Custom | 3.34 | 2.92 |
| VACE-14B | 3.00 | 3.07 |
| Phantom-14B | 2.37 | 2.92 |
| ConsistID | 2.25 | 2.46 |
| Kling | 2.21 | 3.09 |
| **Stand-In (ours)** | **4.10** | **4.08** |

Ablation (FaceSim / Naturalness): RSA→VSA = 0.422 / 3.855; CPM→SPM = 0.536 / 3.755; **Full = 0.724 / 3.922**.
Overhead: +153M trainable params (1% от базы), +2.3% runtime с KV-caching, +0.07% FLOPs.
**[О]** Мы не можем применить Stand-In без дообучения под H3 — готовых весов под H3 нет (только Wan2.1-14B).
Это **долгий** путь (2000 пар, 3000 шагов, H20). Как быстрый практический аналог — `head_swap` LoRA (ниже).

**[П] Что реально доступно и работает на H3 — LoRA от Alissonerdx**
([`Alissonerdx/Minimax-H3-ComfyUI`](https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI), 197k скачиваний/мес):
- `minimax_h3_head_swap_v1.0_r32` — «Experimental reference-image head replacement over an aligned source video», триггер `head_swap:`. Схема: референс-лицо → **native image-reference** канал; полный исходный видео-клип → **AddGuide при frame_idx=0**. LoRA strength 1.0.
  Автор честно пишет: «This is a research model, not a production face-swap system. Exact identity is not guaranteed, and difficult angles, occlusion, motion blur, small faces and long clips can cause drift.»
- `minimax_h3_character_swap_v1.1_ref2va_fro` — замена главного субъекта шота на персонажа с референс-картинки, следует за перформансом гайда, 80 MB SVD-экстракция. **[О]** Это по сути identity-LoRA для H3, и это прямой кандидат на нашу задачу.
- `minimax_h3_lms_v1.0_r64` — sharpness/detail, второй проход, AddGuide при frame_idx=0, strength 1.0, промпт: «Enhance this video with sharp, crisp details while preserving a natural photorealistic appearance.»
- `minimax_h3_live_wallpaper_*` — «often more faithful to facial features».

**[П]** У нас локально уже есть: `minimax_h3_lms_v1.0_r64.safetensors`, `Realism People - MinimaxH3 - r34l1sm` (наш текущий 0.65),
`Sharpness_R64.safetensors`, `MMH3_Facial_Realism_CloseUp.safetensors`, `Natural Face & Speech v2.0 - MinimaxH3` (0.4-0.8).
**`head_swap` и `character_swap` у нас НЕ скачаны** — кандидаты на загрузку.

**[О] Оценка:** `head_swap`/`character_swap` — самый реалистичный «identity-LoRA» путь для H3 в 2026. Он не «держит»
лицо в хвосте сам по себе, но комбинируется с fl2va-якорем: гайд = исходный клип (пиксельная правда) + референс-лицо
в image-канале (identity) + LoRA (механизм переноса).

### 1.8. Temporal-aware face restore внутри самой видеомодели

**[П]** Реально temporal-aware варианты:
1. **H3 FaceRefine** — per-frame crop + re-render + stitch с **пространственно-временным трекингом** и
   per-frame denoise (H3 Per-Frame Denoise). Это ближайшее к «temporal-aware face restore», что есть для H3.
2. **Tessera v4 (detail relay + guide_inherit)** — temporal-aware по построению: чанки наследуют отрендеренный
   латент предыдущего чанка как истину. Это не face-специфично, но это единственный найденный механизм,
   который даёт **плоскую** деталь/identity на длинных клипах (f73→f110 без просадки).
3. **H3 Inject Video Latent (img2img)** в FaceRefine — «the missing img2img path» для H3: кодирует реальные кадры
   в видео-стрим AV-латента, оставляя аудио. **[О]** Позволяет делать **in-video** рефайн лица с сохранением
   исходных пикселей как стартовой точки — потенциально лучше, чем регенерация с нуля.

**[П]** Отдельный класс: Temporal Face Detailer (SDXL) — «Each frame gets a fresh img2img pass with fresh noise... and then morphs».

### 1.9. Порядок и параметры (сводный рецепт для нашего стенда)

**[О] Общий принцип (вытекает из 1.1 + 1.4):** identity надо переносить **пикселями** (фрейм-релей), а не пересказом
(референс/LoRA). Поэтому:

**Уровень 0 (база, до всего):**
- Переключить чекпоинт ref2va → **fl2va** (файл уже на диске). Ожидание: +10 dB попадания в целевой кадр
  → хвостовой дрейф должен упасть кратно. **Это самая дешёвая и самая сильная из возможных мер.**
- Ограничить длину клипа: один H3-блок = 10-15 с. Не пытаться держать лицо 15+ с одним шотом.

**Уровень 1 (внутри генерации, если клип длинный):**
- Last-frame chaining: последний кадр чанка → первый кадр следующего, через fl2va (модель делает то, что обучена).
  Seam-аудио: 40 ms equal-power weld. Дублирующийся boundary-кадр тримится.
- Per-chunk seed (не общий на весь клип).
- Чанк ≤ ~30-40 с (≤4 windows) — иначе texture ratchet +13%/join становится видимым.
- Промпт: описание персонажа **дословно** в каждом чанке + ~2 тихих секунды в начале + LAND SETTLED в конце.

**Уровень 2 (post, для хвоста):**
- **Tessera Guide Anchor** (`spread=4`, `motion_release_floor=0.3`) + `guide_inherit` — сплайс нашего still'а
  (после Qwen-Image-Edit / FaceRefine) в fine-band гайда на **последнем** кадре клипа, чтобы деталь и identity
  доехали до хвоста. Anchor — 2x-масштаб (мы апскейлим 2x), не 4x.
  **Или** положить в гайд последний отрендеренный кадр + detail-still через AddGuide с `frame_idx = -1` (от конца).
- **Tessera Refine** с recipe v3 (`guide_release=0.8`, sigmas `1.0, 0.7, 0.4, 0.0`, `sampler_fine=euler`).
- **FaceRefine** — только если лицо < ~120 px; denoise ≤0.10, `feather` 4-8 (SAM) / ~24 (rect), `smooth_frames` ≥9.
- **ReActor** — только как точечная починка 1-2 кадров; `face_restore_visibility` 0.7, `GPEN-BFR-512`, `codeformer_weight` 0.5-0.75.

**[П] Почему НЕ в посте:** «The in-band guide is a spectrum clamp: the model matches its softness, **vetoing detail LoRAs**»
(Tessera LAWS.md). То есть если в посте гнать деталь через FaceDetailer/ReActor, а в гайде стоит мягкий кадр —
деталь LoRA будет отменена гайдом. **[О]** Значит: либо деталь в гайде (anchor + guide_inherit), либо деталь в посте — но не оба сразу.

### 1.10. Сводка по части 1

| приём | вердикт | доказательность |
|---|---|---|
| **fl2va вместо ref2va** (чекпоинт) | **главная находка, +10 dB попадания в кадр** | **[П]** измерено автором чекпоинта |
| last-frame chaining (fl2va) | работает, identity идёт пикселями | **[П]** документировано в Multishot |
| per-chunk seed | держит лицо (общий seed — плывёт) | **[П]** измерено в Multishot |
| дословный повтор описания персонажа | держит категорию, которую пины пиксельный фрейм | **[П]** проверено на 40-сек сцене |
| guide-кадр в середину | чинит локально, **не распространяется** (±2 кадра) | **[П]** Spot-anchor law, Tessera |
| guide-ре-инъекция по чанкам (guide_inherit) | единственное, что даёт плоскую деталь на длинных клипах | **[П]** +60% detail, f73→f110 flat |
| head_swap / character_swap LoRA | самый реалистичный identity-LoRA для H3 | **[П]** существует, [О] качество не измерено нами |
| IP-Adapter / InstantID / PuLID для H3 | **не поддерживаются** | **[П]** 0 совпадений в коде IPAdapter_plus |
| Stand-In | SOTA по цифрам, но нет весов под H3 | **[П]** FaceSim 0.724 vs 0.422/0.536 |
| FaceRefine | чинит мелкие лица, **не** хвостовой дрейф | **[П]** назначение пака + авторская оговорка про boil |
| ReActor per-frame | пластик + flicker, только аварийно | **[П]** доки ReActor + TemporalFaceDetailer |
| SelfLift TST (наш) | -3.5% HF delta, -1.2% пор | наше измерение |

---

## ЧАСТЬ 2. Потолок микротекстуры кожи в видео

### 2.1. Главный ответ: потолок задаётся still'ом, а видеомодель его переносит

**[П] Референс-измерение из Tessera LAWS.md (v4, detail relay):**
> «**The detail ceiling is now set by a single still from any image pipeline, and the video model propagates it.**»
> «Measured (same encoder conditions): **+60% detail over v3 across the whole clip, FLAT from f73 to f110**
> (the decay that defined every corruption-based approach is gone), frame-0 at the noise-corruption ceiling,
> f0->f1 step 5.3 vs 4.7 anchorless (the raw unaligned splice popped at 30), chunk boundary and grade at v3 levels.»

**[П]** Это ровно ответ на наш вопрос «что поднимает микротекстуру выше still-уровня»: **не upscale, а detail relay через
внутридиффузионный tiled-рефайнер с guide-анкером и наследованием по чанкам.**

**[П]** Официально H3 делает то же самое, но без исходников:
[`H3-Regenerate-2K`](https://huggingface.co/MiniMaxAI/MiniMax-H3):
> «For H3's 2K-resolution output, instead of using a conventional dedicated super-resolution module, we use the H3 base model
> to regenerate its own low-resolution result **in an in-context manner**... (2) the in-context format can reuse the original
> multimodal context when producing high-resolution output, allowing it to **recover information that conventional super-resolution
> methods would otherwise have to "guess", such as small text and fine details**.»
> «**Due to the complexity of the system, this module is not yet open-sourced.** We provide an API for validating the official results.»

**[О] Tessera — это открытая реимплементация того же принципа (in-context регенерация вместо SR), причём
open-source и с измеренными законами. Наш текущий Tessera-рендер — это ровно правильный инструмент для части 2.

### 2.2. Работает ли tiled/temporal refinement внутри одной диффузии — что именно измеряли другие

**[П] Да, и это единственный подход с опубликованными цифрами.** Все цифры — из
[`hashu786/ComfyUI-Tessera` docs/LAWS.md](https://github.com/hashu786/ComfyUI-Tessera/blob/main/docs/LAWS.md)
(локальная копия: `custom_nodes/comfyui-tessera/docs/LAWS.md`, 33 КБ, совпадает с GitHub).

**Заявленные автором (+67% tile-local detail) — как это получено:**

| измерение | значение |
|---|---|
| **+67% tile-local detail** vs frozen-border baseline, при near-baseline shimmer | v3 recipe |
| run-2-class full-frame detail | **129.6 vs 130** (baseline) |
| **end-of-clip persistence** | **103.6 vs 91** (baseline) |
| chunk boundary | dead (нет разрыва) |
| diff vs faithful baseline | thin-edge-only |

**[П] Законы, которые за этим стоят (все — на реальных рендерах, прогоны пронумерованы):**

1. **Якорь к истине, а не к соседям.** Frozen-border дизайн (v1/v2) давал накопление registration drift вдоль ряда:
   **+1.5, −2.1, −9.3, −15.5 px** на 4 тайлах. `border_mode=overlap` убирает это: каждый тайл деноизится целиком и
   привязан к своей conditioning (plate + кропы refs + кроп текста).
2. **Расписание: 1 head + 2 tail.** Один tail-шаг saturates (release 0.5→0.8 = <1% детали); два tail-шага дают
   «texture BORN then ORGANIZED (+67%)»; три шага перекармливают: **+7% энергии (читается как жёсткость) и +17% мерцания**.
   Ёмкость кривой — пик на двух шагах.
3. **Euler для tail, никогда не multistep.** «switching the identical schedule to euler gave **+56% detail AND cut shimmer by a third**».
4. **Больше head = меньше послушания.** Второй turbo-head шаг: **−21% detail**. Плюс safety-правило: второй шаг
   turbo-модели через mid-sigmas выдавал NaN в латент (fp16).
5. **Источник мерцания наследуется, не создаётся.** Рефайн поднимает frame-to-frame coherence источника
   **0.40 → 0.75** (потолок ~0.75), но **никогда выше**. «neither release, schedule, sampling-noise coherence, nor
   pre-stabilizing the plate moves the ceiling.»
   **[О] Прямо наш кейс:** наш still — 9.2/10, видео-кадр — 8.0/10. SeedVR2/RIFE поры не создают, видео съедает ~55% микротекстуры.
   Потолок ~0.75 coherence и «источник мерцания наследуется» — прямое объяснение, почему SeedVR2/RIFE не помогут:
   они работают ПОСЛЕ диффузии, а потолок задан ДО неё.
6. **frame_match — только transient.** Якорный DC-сдвиг на плату утаскивает refined-латент ~1.4 off-manifold,
   VAE декодирует это как mush: **коллапс детали 319 → 87** (проявлялся только в chunked full-frame прогонах).
   Фикс: вычитать time-mean офсета перед применением. После фикса: chunked-full вернулся к solo-классу (78–112% против 27–52% сломанного).
7. **Metric caveat (важно для нас):** «Laplacian energy ranks etchers above drawers — a LoRA that presses harder on edges
   outscores one that draws finer organic structure. **LoRA comparisons are settled by faces and jewelry at 1:1, not by the benchmark.**»
   **[О]** Наши измерения пор/MAE — в той же ловушке. Надо мерить и по «лицам и украшениям».

**[П] Про still→видео потолок в терминах Tessera:**
> «a 4x-upscaled plate has no top-octave energy **by construction**: soft faces read 2.7x floor on the plate vs 38-43x for hard frame borders»
> и «the model lifts coherence toward its ~0.75 ceiling (0.40 → 0.75 measured)».

**[О]** Это ключ к нашему Part 2: **текстура, которой нет в plate, не появится в рефайне — рефайн может лишь
перенести/усилить то, что есть в гайде/анкоре.** Поэтому anchor из still-а (Qwen-Image-Edit 2511, 9.2/10) —
единственный способ поднять потолок. Это совпадает с нашим измерением «видео съедает ~55% микротекстуры still-а».

### 2.3. Независимые замеры и бенчмарки со стороны (не Tessera)

**[П] `ai-upscaler-benchmark`** ([ismael-joffroy-chandoutis/ai-upscaler-benchmark](https://github.com/ismael-joffroy-chandoutis/ai-upscaler-benchmark),
июнь 2026, RTX 5090 как референс-машина) — методологически честный, явно разделяет измеренное и неподтверждённое:
- **SeedVR2 (сильнейший локальный видео-инструмент) имеет в СВОЕЙ модель-карточке явное предупреждение, что он
  переостраняет 720p AI-видео. «Your exact use-case is its documented failure mode.»** Для нас (720p-ish AI-видео) — прямое попадание.
- «GAN/CNN upscalers sharpen what is already there, crisping plastic skin and amplifying flicker. Diffusion upscalers regenerate detail,
  which fits synthetic input better, but **they can invent a new face and drift across frames**.» — ровно наш риск с SeedVR2 и FaceRefine.
- Методология: «There is no ground-truth 4K for AI-generated 720p, so standard full-reference metrics mostly do not apply, and the
  popular no-reference metrics (MUSIQ, NIQE, CLIP-IQA+) **reward the over-sharpening and hallucinated detail** that AI upscalers produce.»
  **[О]** Наша методика измерения (поры, face MAE, temporal delta) — правильнее этих метрик; менять не надо.
- Рекомендация для нашего случая: «Cinema 4K, quality-max → **SeedVR2-7B (low strength)**», «Short-drama volume → FlashVSR Tiny / SeedVR2-3B».
  **[О]** Мы на 16 ГБ, у нас 3B.

**[П] `SeedVR` (ByteDance-Seed, ICLR2026)** — [репозиторий](https://github.com/ByteDance-Seed/SeedVR):
> «most of the existing diffusion-based restoration models rely on patch-based sampling, i.e. dividing the input video into overlapping
> spatial-temporal patches and fusing these patches using a Gaussian kernel at each diffusion step.»

**[П]** Причина деградации микротекстуры в видео (SeedVR2-гайд, aifilms):
> «**VAE latent grid softness.** 3D causal VAEs compress spatial dimensions by 8×8, smoothing out high frequency details such as skin pores,
> distant foliage, eyelash definition, and woven fabric. **Temporal boiling in background textures.**»

**[О] Это независимое подтверждение нашего наблюдения:** 3D-VAE H3 сжимает 8×8 по пространству → поры кожи
и микротекстура сглаживаются на этапе латента, до любого пост-прохода. Потолок микротекстуры задан латентом, а не нашей обработкой.
H3-Regenerate-2K/Tessera работают именно потому, что перегенерируют в латенте, а не upsample'ят пиксели.

**[П] Важная оговорка по SeedVR2 из того же источника:** «SeedVR2's own model card warns it over-sharpens 720p AI-generated video»,
и SeedVR2 «can invent a new face and drift across frames» (диффузионные апскейлеры).

### 2.4. Есть ли модели с такой же плотностью текстуры, как у still — и как их запустить на 16 ГБ

**[П] Да, и официальный ответ MiniMax — in-context регенерация, а не SR:**
`H3-Base` генерит 768p; `H3-Regenerate-2K` перегенерирует в 2K in-context. Открыт только API, исходников модуля нет.

**[П] Открытые реализации того же принципа:**
1. **Tessera** — открыт, с измеренными законами, работает на H3, мы его уже интегрировали.
2. **`minimax_h3_lms_v1.0_r64`** (Alissonerdx) — sharpness/detail LoRA, второй проход поверх клипа через AddGuide при
   `frame_idx=0`, strength 1.0. **Уже лежит у нас локально.** Является частью «house recipe» Tessera
   («LMS detail LoRA (Alissonerdx) — sharpness/detail enhancement — part of the house recipe»).
   **[П] Ограничение LoRA в H3:** LoRA «vetoes» — «the in-band guide is a spectrum clamp: the model matches its softness, **vetoing detail LoRAs**».
   То есть LMS работает, если гайд мягкий (нужна свобода в tail) — при `guide_release` хвост разжимается.
3. **`minimax_h3_vfx_edit_v1.0_r128_ffp`** — «experimental first-frame propagation: edit the first frame and the edit follows the whole video».
   **[О]** Прямо наш кейс: деталь/правка из still'а распространяется на весь клип.

**[П] Модели, генерящие видео с текстурой уровня still (для справки, что есть на рынке):**
- **ViBe** ([arxiv:2603.23326](https://arxiv.org/html/2603.23326)) — «Ultra-High-Resolution Video Synthesis Born from Pure Images»;
  3D attention даёт квадратичную стоимость, end-to-end обучение в ультра-высоком разрешении слишком дорого.
- **CineScale** ([arxiv:2508.15774](https://arxiv.org/html/2508.15774v2)) — «Tuning-Free High-Resolution Video Generation»;
  «most video diffusion models are trained at 720p videos and are therefore effectively limited to generating videos at similar resolution».
- **UniVid** ([arxiv:2603.13739](https://arxiv.org/html/2603.13739)) — pyramid diffusion, текстура из image clues.
  **[П]** Общий тезис литературы: видеомодели системно ограничены ~720p по пространственному разрешению —
  ровно наше ограничение (H3: BASE_SHORT_EDGE=768).

**[О] Про «как запустить на 16 ГБ»:**
- H3 int8-convrot на 16 ГБ — уже работает (наш текущий пайплайн).
- Tessera спроектирован под одну consumer-карту: streamed VAE encode/decode, «the full-resolution pixel clip never exists in memory».
  Измеренная конфигурация: `spatial_tile=high_vram/low_vram/custom`, `temporal_tile=high_vram (124-кадровые окна) / low_vram (73) / custom`.
  **[П]** Из LAWS.md: «segment_frames 56 default => ~5.6GB window pixels at 3840x2176 and x1.55 encode overhead; lower if the encode OOMs».
  Production-validated прогон: «544p→4K 15s (617 dilated frames, 18 windows, 24 tiles × 3 snapped chunks, 3h total) — osc 2.44%/16.1%
  vs the same clip's 2x reference 2.32%/12.3%... peak encode-side pixels ~5.2 GB vs the 57.6 GB whole-clip copy that OOM'd the plain graph».
  **[О]** Т.е. 16 ГБ — решаемо через `Tessera Streamed Encode` + `spatial_tile=low_vram`/`custom` + `temporal_tile=low_vram`.
- Наш текущий план (2144×3232, тайл 640×768, 4×5=20 тайлов, border 64) — **вне** измеренной Tessera-конфигурации
  (у них 24 тайла × 3 чанка на 4K). **[О]** Стоит проверить `spatial_tile=custom` с тайлом 640×768, но `context_border=64` у нас
  совпадает с их дефолтом, `seam_regen=32` — тоже дефолт. Расхождение — в `temporal_tile` и в размере цели.

### 2.5. Сводка по части 2

| подход | поднимает потолок выше still? | цифры | доказательность |
|---|---|---|---|
| **Tessera detail relay (anchor + guide_inherit)** | **да — это и есть ответ** | +60% detail, flat f73→f110; v3 +67% tile-local | **[П]** LAWS.md |
| H3-Regenerate-2K (официальный) | да, но закрыт (только API) | без чисел | **[П]** модель-карточка |
| LMS sharpness LoRA | частично | — | **[П]** есть, измерений нет |
| vfx_edit_fpp (first-frame propagation) | потенциально да | — | **[О]** |
| SeedVR2 3B upscale | **нет** (наш текущий пайплайн) | — | **[П]** model card сам предостерегает от over-sharpen AI-видео |
| RIFE | нет | — | наше измерение |
| SelfLift TST | нет (−1.2% пор) | наши | наше |
| 3D-VAE 8×8 сглаживание | **это и есть потолок** | — | **[П]** SeedVR2-гайд |
| Кадровый FaceDetailer/ReActor | нет + flicker | — | **[П]** |
| Новая видеомодель «с текстурой still» | пока нет в open weights | — | **[П]** ViBe/CineScale |

---

## ЧАСТЬ 3. Рекомендуемый следующий шаг (для GPU-свободного окна)

**[О] Приоритеты (по соотношению эффект/стоимость):**

1. **Переключить чекпоинт ref2va → fl2va** (файл уже на диске, 0 скачиваний). Замерить face MAE на кадре 130
   тем же методом, что дало 33.6/21.0/5.0. Ожидание: кратное падение хвостового MAE. **Это первое, что надо проверить.**
2. **Разбить клип на чанки с last-frame chaining через fl2va** + per-chunk seed + дословный повтор описания персонажа.
   Замерить face MAE в хвосте каждого чанка.
3. **Добавить Tessera Guide Anchor** на последний кадр (anchor = наш still 9.2/10, `spread=4`, `motion_release_floor=0.3`)
   + `guide_inherit` — проверить, дотянет ли identity до хвоста.
4. **Скачать `minimax_h3_character_swap_v1.1_ref2va_fro` и `minimax_h3_head_swap_v1.0_r32`** (80 MB + r32) — тест
   как identity-LoRA для H3 (у нас их нет).
5. **LMS LoRA внутри Tessera** вместо/вместе с нашим `Realism People 0.65` — проверить, не конфликтует ли
   наш Realism LoRA с detail-задачей (по LAWS: деталь-LoRA конфликтует с мягким гайдом).

**[?] Что осталось неизвестным:**
- Насколько `head_swap`/`character_swap` реально удерживают лицо в хвосте H3 (нами не измерялось, документация
  автора прямо предупреждает о drift на long clips).
- Наш Tessera-рендер (2144×3232, 20 тайлов) ещё идёт — его реальные цифры детали/мерцания/identity неизвестны.
- Работает ли `character_swap` в связке с fl2va (в fl2va нет референс-строк — а `character_swap` это ref2va-LoRA).
  **[?]** Вероятно, нужен ref2va для character_swap, и тогда теряем выигрыш fl2va в 10 dB. Компромисс не измерен.

## Источники

**Первоисточники (репозитории / модели / статьи):**
1. https://huggingface.co/joeygambino/MiniMax-H3-comfy-native-fl2va — измерения fl2va vs ref2va (26.35 / 16.15 / 16.81 dB)
2. https://huggingface.co/joeygambino/MiniMax-H3-Multishot-Workflow — то же + chain mechanics
3. https://huggingface.co/LaDruid/MiniMax-H3-Multishot-Workflow — last-frame chaining, context_pin, seed_per_shot, texture ratchet
4. https://github.com/Carasibana/ComfyUI-H3-FaceRefine — ★534, назначение, ограничения
5. https://github.com/hashu786/ComfyUI-Tessera — tiled-рефайнер
6. https://github.com/hashu786/ComfyUI-Tessera/blob/main/docs/LAWS.md — измеренные законы (+67%, +60%, drift px, coherence 0.40→0.75, 319→87 collapse)
7. https://comfyui-wiki.com/en/news/2026-10-30-tessera-h3-upscaler — обзор Tessera, ghost-subject failure, seam_regen
8. https://huggingface.co/MiniMaxAI/MiniMax-H3 — H3-Base FL2VA vs Ref2VA, H3-Regenerate-2K, H3-Context-IR
9. https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI — LMS / head_swap / character_swap LoRA (+ docs/lms.md, docs/head-swap.md)
10. https://arxiv.org/html/2508.07901v4 — Stand-In (FaceSim 0.724, ablation, LoRA r128 на QKV image-токенов)
11. https://huggingface.co/BowenXue/Stand-In — Stand-In weights (Wan2.1-14B-T2V base)
12. https://github.com/ismael-joffroy-chandoutis/ai-upscaler-benchmark — методология и предупреждение model card SeedVR2
13. https://github.com/ByteDance-Seed/SeedVR — SeedVR2, patch-based temporal-spatial fusion
14. https://studio.aifilms.ai/blog/seedvr2-real-esrgan-open-source-video-upscaler — 3D-VAE 8×8 сглаживание, temporal boiling
15. https://arxiv.org/html/2603.23326 (ViBe), https://arxiv.org/html/2508.15774v2 (CineScale), https://arxiv.org/html/2603.13739 (UniVid) — предел видеомоделей
16. https://github.com/Gourieff/ComfyUI-ReActor/discussions/215 — ограничения face-swap (только frontal)
17. https://www.atlascloud.ai/blog/tips/comfyui-face-swap — параметры ReActor (GPEN-BFR-512, visibility 0.7-0.8)
18. https://comfy.icu/node/TemporalFaceDetailer — почему per-frame FaceDetailer даёт morph/flicker
19. https://www.reddit.com/r/comfyui/comments/1xjgkwm/ — per-frame face detailer flicker, H3 face refining
20. https://docs.comfy.org/tutorials/video/minimax/minimax-h3 — официальный туториал H3

**Локально проверено на стенде:**
- `custom_nodes/ComfyUI-H3-FaceRefine/` (README + `nodes.py`, класс `H3PerFrameDenoise`, `H3FaceStitch`)
- `custom_nodes/comfyui-tessera/docs/LAWS.md` + `tessera/nodes.py` (параметры `guide_release`, `guide_inherit`, `hero_min`, `seam_regen`)
- `comfy_extras/nodes_minimax_h3.py`, класс `MiniMaxH3AddGuide` (frame_idx, отрицательные = от конца)
- `models/diffusion_models/`: `minimax_h3_fl2va_pruned_int8_convrot.safetensors` (20 ГБ) и `minimax_h3_ref2va_pruned_int8_convrot.safetensors` (20 ГБ)
- `models/loras/`: `minimax_h3_lms_v1.0_r64.safetensors`, `Realism People - MinimaxH3 - r34l1sm`, `Sharpness_R64.safetensors`,
  `MMH3_Facial_Realism_CloseUp.safetensors`, `Natural Face & Speech v2.0 - MinimaxH3`
- `custom_nodes/ComfyUI_IPAdapter_plus/` — 0 совпадений по `minimax`/`H3` (H3 не поддержан)

**Ограничения пробы:** Reddit недоступен напрямую (403 network policy на old.reddit.com и .json), часть треда
про identity drift прочитана через поисковый кэш. GPU не трогали. Реальные цифры Tessera на нашем клипе ещё
не получены (рендер идёт). Наши измерения (face MAE 33.6/21.0/5.0, поры, SelfLift) взяты из контекста стенда,
не перепроверены в этой пробе.
