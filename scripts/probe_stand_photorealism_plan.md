# Фотореализм в видео: план по стенду (RTX 4070 Ti SUPER 16 ГБ)

**Дата проверки:** 2026-11-02. Все числа — с проверенных источников (HF API, код ComfyUI, Reddit, официальные README), не по памяти.
**Проверено на месте:** PROD 8188 + LAB 8189 живы, ComfyUI 0.43.0 в обоих, GPU 16376 MiB (занято 15863 — оба сервера в VRAM).

## 0. Исправления к постановке

| В постановке | На деле (проверено) |
|---|---|
| RAM 63.8 ГБ | **68.5 ГБ всего, 45.6 ГБ свободно** (psutil) |
| Модели в `C:\Users\user\...` | Модели в **`C:\AI\Models\comfyui`**; ComfyUI — на **E:** (`E:\ComfyUI_windows_portable_nvidia_cu126`), LAB — `E:\ComfyUI_LAB_037` |
| Свободно на C: 656 ГБ | Верно. Но **E: — 30 ГБ свободно (94% занято)** — это главное ограничение для установок |
| «H3 ref2v на 1080p» | **Нативно 1080p невозможно.** В `comfy_extras/nodes_minimax_h3.py`: `BASE_SHORT_EDGE = 768`, `MAX_PIXELS = 768*1344` (~1.03 MP). 1080p = обязательный апскейл-проход |
| «H3 first-frame 1056x704» | Верно по площади (в пределах 1.03 MP), но 1056×704 = 0.74 MP — это *меньше* полного холста 1344×768. Реальный максимум — 1344×768 |
| `wan2.2_ti2v_5b_index.json` = чекпойнт | Это **не** чекпойнт. Diffusers-индекс к 3-шардовой модели на 19.99 GB (`diffusion_pytorch_model-0000{1,2,3}-of-00003.safetensors`). Comfy-репак — отдельный файл 10 GB, **его нет** |

---

## (а) Таблица VRAM / RAM / диск

Разделение: **официально** (цитата из README модели) vs **измерено** (кто, на чём).

| Модель | VRAM (источник) | RAM | Диск | Файл |
|---|---|---|---|---|
| **Wan2.2 TI2V-5B** 720p/24fps | **официально ≥24 ГБ** (Wan2.2 README, 720p + offload-флаги). ComfyUI docs: «should fit well on 8GB vram with the ComfyUI native offloading» | 11+ ГБ (оффлоуд) | **10.00 ГБ** fp16 | `wan2.2_ti2v_5B_fp16.safetensors` |
| **Wan2.2 I2V-A14B** 720p | **официально ≥80 ГБ** (Wan README, 720p без оффлоуда). 480p/640px fp8: 24 ГБ. Пик 59.8 ГБ на 720p по скрипту Alibaba | 11+ ГБ; GGUF Q5_K_M на 16 ГБ VRAM → RAM забивается полностью (QuantStack discussion #12: 32 ГБ RAM заполнено) | 14.29 ГБ ×2 эксперта (fp8) | `wan2.2_i2v_{high,low}_noise_14B_fp8_scaled.safetensors` |
| **MiniMax H3 ref2v** 1080p | **нативно 1080p нет** — потолок 1344×768 (1.03 MP). 1080p = 2-стадийный латент-апскейл | H3 на 3060/12 ГБ: **43.6 ГБ пиковой RAM** (GenVidKit, 1344×768, 124 кадра) | 19.9 ГБ ×2 + латент-апскейлер 0.69 ГБ | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` |
| **LTX-2.5** | **официально 32 ГБ+ VRAM, ниже — не поддерживается** (ComfyUI-LTXVideo). На практике GGUF Q4_0 ~11.65 ГБ + Q4 энкодер | 45 ГБ (измерено на 4070/12 ГБ, 736×1280, 5 с) | 21.5 ГБ int8 / 11.65 ГБ GGUF | `ltx-2.5-22b-*.gguf` |
| **FLUX.1 Dev** (стартовый кадр) | 12 ГБ fp8 (11.90 ГБ) — влезает; bf16 23.8 ГБ — нет | ~16 ГБ | 11.90 ГБ fp8 | `flux1-dev-kontext_fp8_scaled.safetensors` |
| **FLUX.1 Kontext** (правка кадра) | 12 ГБ рекомендовано, 16 ГБ без ограничений; GGUF Q4_K_M 7.45 ГБ | ~16 ГБ | 11.90 ГБ fp8 / 7.45 ГБ GGUF | `flux1-dev-kontext_fp8_scaled.safetensors` |
| **SeedVR2** (видео-апскейл) | 3B: 12 ГБ; 7B fp8: 14 ГБ. 16 ГБ → fp8 + BlockSwap/VAE tiling | 16–24 ГБ | 3.39 ГБ (3B fp8) / 8.24 ГБ (7B fp8) | `seedvr2_3b_fp8_e4m3fn.safetensors` |
| **Real-ESRGAN** | **4 ГБ VRAM** (2 ГБ с tile splitting) | 4 ГБ | **0.067 ГБ** | `RealESRGAN_x4plus.safetensors` |

**Измерено на нашей GPU (RTX 4070 Ti SUPER 16 ГБ):**
- H3 I2V 5 с @ ~0.7 MP → **5–6 мин** (сообщественный отчёт на этой же карте, r/comfyui через minimax3.org)
- H3 10 с 736×1344 → **~20 мин** (note.com/aijikkilog, та же GPU)
- LTX-2.5 GGUF Q4_0 + **conv-VAE**, 5 с → **51–52 с**; 10 с → **107–115 с** (тот же автор, та же GPU)
- LTX-2.5 10 с на **обычном** VAE → 582 с. **Замена VAE на `ltx-2.5-video-vae-conv-bf16` даёт 5.4× ускорение.**
- Wan2.2 I2V-A14B GGUF Q5_K_M, 832×480×81 на 16 ГБ → **~25 мин** (QuantStack #12)

**Offload (CPU RAM streaming) — реально ли на 16 ГБ VRAM + 64 ГБ RAM:**
- **H3 33B (int8 19.9 ГБ):** да. FreeVideo уже стримит `int8-convrot` (22.93 ГБ) через `machine.json` → `"cache": "...\\int8-convrot\\cache"`, `prepared_format: int8_convrot`. Измерено на 12 ГБ: 11 649 MiB VRAM / 43.6 ГБ RAM. У нас 68.5 ГБ RAM — запас есть. **Прирост времени: ~1.5–2×** к native из-за PCIe-перекачки.
- **Wan A14B (2 эксперта по 14.29 ГБ fp8):** на 16 ГБ VRAM реально **только** через GGUF Q5_K_M (10.79 ГБ ×2) + CPU-offload энкодера. RAM: GGUF Q5_K_M на 16 ГБ VRAM **забивает 32 ГБ RAM полностью** → у нас 45.6 ГБ свободно, впритык. Прирост времени: с 19 мин (fp8) до 25+ мин (Q5_K_M).
- **Важно:** fp8-файлы A14B (14.29 ГБ ×2 = 28.6 ГБ на диске) не влезают в 16 ГБ VRAM одновременно — нужен либо GGUF, либо постоянный offload обоих экспертов.

**Диск:** C: 656 ГБ свободно, G: 9 ТБ свободно, **E: 30 ГБ (94% занято)**. Всё крупное — только на C: (модели) или G: (видео). E: для ComfyUI/нод не трогать.

---

## (б) Что доставить (файлы + ссылки + размеры + приоритет)

Размеры — из HF API `?blobs=true` (проверено 2026-11-02). Куда: `C:\AI\Models\comfyui\<папка>`.

### Приоритет 1 — обязательное (даёт фотореализм кожи)

| # | Файл | Размер | Папка | Репо |
|---|---|---|---|---|
| 1 | `minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors` | **0.69 ГБ** | `latent_upscale_models` | `LBH-123-AI/Minimax_h3_latent_Upscaler` (318k dl, 471 ★) |
| 2 | `RealESRGAN_x4plus.safetensors` | **0.067 ГБ** | `upscale_models` | `Comfy-Org/Real-ESRGAN` |
| 3 | `4x-ClearRealityV1.pth` (фотореалистичный) | ~0.07 ГБ | `upscale_models` | `Cseti/ComfyUI-Workflows` (датасет с воркфлоу) |
| 4 | `1xSkinContrast-HighAlternative-SuperUltraCompact.pth` (детали кожи) | ~0.01 ГБ | `upscale_models` | там же |
| 5 | `seedvr2_3b_fp8_e4m3fn.safetensors` | **3.39 ГБ** | `SEEDVR2` | `Comfy-Org/SeedVR2` |
| 6 | `clip_vision_h.safetensors` | **1.26 ГБ** | `clip_vision` | `Comfy-Org/Wan_2.2_ComfyUI_Repackaged` |

**Уже есть и НЕ надо качать:** `seedvr2_ema_3b.safetensors` (13.57 ГБ) и `seedvr2_3b_int8_convrot.safetensors` (3.46 ГБ) в `SEEDVR2/`, `4x-UltraSharp.pth` в `upscale_models/`, `codeformer-v0.1.0.pth` + `GPEN-BFR-512.onnx` в `facerestore_models/`, `inswapper_128.onnx` в `reactor/`.

### Приоритет 2 — разблокирует Wan-ветку (нужна база, её нет)

| # | Файл | Размер | Папка | Репо |
|---|---|---|---|---|
| 7 | `wan2.2_ti2v_5B_fp16.safetensors` | **10.00 ГБ** | `diffusion_models` | `Comfy-Org/Wan_2.2_ComfyUI_Repackaged` |
| 8 | `wan2.2_vae.safetensors` | **1.41 ГБ** | `vae` | там же |
| 9 | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | **6.74 ГБ** | `text_encoders` | там же |
| 10 | `wan2.2_i2v_lightx2v_4steps_lora_v1_{high,low}_noise.safetensors` | 1.23 ГБ ×2 | `loras` | там же |
| 11 | `wan2.2_i2v_{high,low}_noise_14B_fp8_scaled.safetensors` | **14.29 ГБ ×2** | `diffusion_models` | там же |
| 12 | `wan_2.1_vae.safetensors` | 0.25 ГБ | `vae` | там же (**уже есть**) |

> ⚠️ **Секторный LoRA уже есть, но бесполезен без базы:** `Wan2_2_I2V_A14B_4steps_lora_rank64_Seko_V1_{high,low}_noise` — это ровно те два файла, что нужны к п.11. Скачиваем вместе.

### Приоритет 3 — опционально (v2v, два человека, реальное видео→видео)

| # | Файл | Размер | Папка | Репо |
|---|---|---|---|---|
| 13 | `wan_animate_2_distill_int8_convrot.safetensors` | **16.65 ГБ** | `diffusion_models` | `Comfy-Org/Wan-Animate-2` (907k dl) |
| 14 | `lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors` | 0.74 ГБ | `loras` | там же |
| 15 | `flux1-dev-kontext_fp8_scaled.safetensors` | **11.90 ГБ** | `diffusion_models` | `Comfy-Org/flux1-kontext-dev` |

### Приоритет 4 — «что НЕ надо ставить» (см. раздел (д))

**Итого по приоритету 1: ~5.5 ГБ. По приоритету 2: ~48 ГБ. Приоритет 3: ~29 ГБ.**

⚠️ Порядок важен: **сначала** приоритет 1 (5.5 ГБ, даёт 80% фотореализма), **потом** чистка, **потом** Wan-база.

---

## (в) Рекомендация бэкенда: H3 ref2v + H3-латент-апскейлер + SeedVR2

**Победитель: H3 (Ref2VA для identity, FL2VA для i2v) → латент-апскейл H3 → SeedVR2 как финальный проход.**

### Почему не Wan TI2V-5B

| Критерий | H3 | Wan2.2 TI2V-5B |
|---|---|---|
| Разрешение | 1344×768 (1.03 MP) потолок | **1280×704 нативно 720p @ 24fps** ← выигрыш Wan |
| Кожа | Лучше в community (r/comfyui: «Minimax is presently recognized as the leading model for I2V») | 5B — «subpar performance» (r/StableDiffusion) |
| Identity из 1 фото | **Ref2VA — community standout** | i2v anchor, слабее |
| Аудио | Нативный стерео | Нет |
| Длина клипа | 5–15 с | 5 с |
| VRAM | 19.9 ГБ int8, стримится, работает на 16 ГБ | 10 ГБ fp16, **официально ≥24 ГБ**, в ComfyUI с offload «8 ГБ» |
| Время | ~20 мин на 10 с | ~9 мин на 4090 за 5 с 720p |

**Вердикт:** TI2V-5B выигрывает только по разрешению (720p нативно) и скорости. Но «фотореализм кожи» — это не про пиксели, а про **диапазон 32×32-токенов** и сохранение микро-текстуры. H3 даёт лучшую кожу; SeedVR2 потом дорисовывает разрешение. **Покупка 10 ГБ TI2V-5B = 10 ГБ ради 704p, которые всё равно придётся доапскейлить.** → Приоритет 2, не 1.

### Почему LTX-2.5 — не основной бэкенд

- **Официально: 32 ГБ+ VRAM, ниже не поддерживается** (ComfyUI-LTXVideo). На 16 ГБ только community GGUF.
- Слабое место ровно наше: r/StableDiffusion — «distorted hands and feet», «performs poorly in text2vid, but does better in img2vid, although it completely ignores the prompt». SpicyAPI: «Several upper-level clips were rough: head cropped out, colour casts, merged limbs».
- **Сильное:** 51 с на 5 с (в 3–10× быстрее H3), нативный звук, 10–20 с клипы, **conv-VAE = 5.4× ускорение**.
- **Роль: движковый слой** — черновики, тесты движения, wide shots, всё с аудио. Финальный close-up — H3.

### Почему «H3 ref2v + SeedVR2», а не «Wan TI2V + Real-ESRGAN»

**Real-ESRGAN на видео — неправильный инструмент:** обрабатывает кадры **независимо**, без временного окна → flicker, «each frame is upscaled independently». Это отличный инструмент для статики, но не для видео. SeedVR2 — one-step diffusion DiT с **полным 3D space-time attention** → де-фликкер встроен.

**Но SeedVR2 не решает проблему H3 «mushy faces» на дальних планах.** Причина (из `ComfyUI-H3-FaceDetail`): H3 video VAE жмёт 16× пространственно, DiT патчит латенты 2×2 → **каждый токен DiT покрывает 32×32 пикселя**. На 1024×576 модель рисует по сетке 32×18. Полный рост в кадре = лицо 35 px ≈ 1.5×1.5 токена → глаза/брови/рот рисуются как клякса. Рост разрешения почти не помогает: доля лица та же (~2 токена и на 1376×768).

**Правильный pipeline для фотореализма кожи (16 ГБ):**

```
1. Стартовый кадр:  FLUX.1 Dev / Qwen-Image 2.1 / Z-Image (уже стоят) → 1 MP фото
2. H3 Ref2VA (референс = исходное фото) → 1344×768, ~0.7–1.0 MP
   ↑ если i2v от одного кадра: H3 FL2VA
3. H3 Hires Fix (латент-апскейлер LBH, 0.69 ГБ) → 1920×1088, НО:
   - refine только от sigma 0.45 вниз по beta57-расписанию
   - scale 1.3–1.5× (не 2×) — иначе «lines or glass-like artifacts»
   - LoRA weight 1.0, снижать если «overly glossy»
   - старт с ПОЛНОГО 1 MP рендера, не с turbo-прохода (turbo меняет take)
4. SeedVR2 (3B fp8) — де-фликкер + микротекстура кожи
   - batch_size по формуле 4n+1: 1, 5, 9, 13, 17... (у нас 13 или 17)
   - temporal_overlap 2–4 (blend между батчами)
   - uniform_batch = True (иначе последний батч короче → артефакты)
   - resolution 1080, color correction = lab
   - BlockSwap + VAE tiling, если OOM
5. RIFE 2× — только если после SeedVR2 остаётся flicker
6. Face-lock (ReActor) — см. (в) ниже
```

**Оценка времени рендера на 16 ГБ (5 с клип):**
| Этап | Время |
|---|---|
| H3 Ref2VA 1 MP, 5 с | 5–6 мин (измерено на этой GPU) |
| H3 Hires Fix → 1080p | ~1–2 мин (ResCascade: «about a minute instead of about five») |
| SeedVR2 3B fp8, 5 с @1080p | ~3–6 мин (7B fp8: 0.2–2 fps → ~10–20 мин) |
| RIFE 2× | ~1–2 мин |
| **Итого 13 с** | **~11–16 мин** |

---

## (в2) NSFW: ограничения по моделям — ПРОВЕРЕНО

| Модель | Фильтр в весах? | Что реально | Ограничения лицензии |
|---|---|---|---|
| **FLUX.1 Dev** | **Нет активного safety-классификатора.** Нет refusals. Но: «the pre-training dataset was sanitized of explicit anatomy» → не анатомия, а **пробел в обучении** | Комьюнити лечит LoRA 0.8–1.2 (`Ballsworthy/Flux-Uncensored-V2`, `RomixERR/FLUX_NSFW_unlocked`, `enhanceaiteam/Flux-uncensored`) или merge (`FLUX-UNCENSORED-Merged`, `Chroma1-HD`, `SNOFS`) | **Non-Commercial License.** Коммерческое использование модели (в продукте/сервисе/для дообучения) запрещено; выходные изображения коммерчески использовать можно. Технически: «нельзя использовать модель в доходном продукте» |
| **Wan 2.2** | **Веса без фильтра.** «The open weights ship with no content filter. Hosted versions vary» | Не цензура, а **недообучение**: Wan «understands the concepts of nsfw and it will try to do it but it will end up a bizarre output as it was not fed with the data» | Apache 2.0 — **коммерческое использование разрешено** (кроме Civitai-лора под их лицензией). API-хостинги фильтруют: наш тест стандартного Wan 2.2 T2V отказал на всех adult-промптах |
| **Qwen-Image 2.1** | **Нет checker'а в пайплайне.** Фильтр живёт только в Alibaba Cloud / HF Demo | «Uncensored GGUF» = обычная квантизация тех же весов, метка ничего не добавляет. Heretic-энкодер (~9 ГБ) — если нужен снятый refusal-direction | **Qwen Research License: «FOR NON-COMMERCIAL PURPOSES ONLY»**, точнее — «research or evaluation purposes only». Платное/клиентское использование → отдельная лицензия с model-business@notice.qwencloud.com. Производные обязаны писать «Built with Qwen» |
| **MiniMax H3** | **Веса без фильтра** (фильтр в API/хостинге MiniMax, не в весах) | Открытые веса: полный контроль. NSFW-сообщество большое (168 LoRA у нас) | ⚠️ **Территориальное ограничение: лицензия H3 Community License исключает EU, UK, US, South Korea.** «Use, reproduce, distribute... outside the Applicable Territory is not authorized». Роялти-фри для исследований и коммерции (если >$20M выручки — письменное разрешение). Условие: коммерческий продукт должен показывать «MiniMax H3» в UI; хостерам — обязаны внедрить safeguards. Запрет: дистилляция в не-H3 модели |
| **LTX-2.5** | Без фильтра локально | `ChrisColeTech/LTX-2.5-uncensored-v1.1-FP8` — Eros10 + 4-step Turbo + **uncensored Gemma4 12B в одном файле** | LTX-2.x Community License: коммерческое использование при выручке >$10M требует платной лицензии |

**Ключевой вывод:** фильтров в весах **нет ни у одной** из четырёх моделей. Все «цензуры», что видят пользователи — это либо **хостинговые** API-фильтры (MiniMax, Alibaba Cloud, fal, Replicate), либо **пробел в обучающих данных** (особенно Wan и FLUX: анатомия не выучена → «bizarre output», а не отказ).

**Практический выбор по лицензии:** при любой коммерческой/клиентской работе **Wan 2.2 (Apache 2.0)** — единственный чистый вариант. H3 запрещён в US/EU/UK; Qwen-Image 2.1 — только research/evaluation; FLUX.1 Dev — non-commercial на саму модель.

---

## (в3) Face-lock + detail-пасс без temporal flicker

**Проблема:** ReActor обрабатывает каждый кадр **независимо** → «visible difference between frames (twitching)». inswapper_128 выдаёт лицо в 128×128.

### Правильный порядок (не тот, что в `fv_reactor_full.py`)

Текущий `C:\AI\scripts\fv_reactor_full.py` делает: FreeVideo → ReActor → VHS_VideoCombine. Это неправильно по трём причинам:
1. **ReActor не контролирует таймлайн.** Пропущенный кадр = провал в видео. Из `discuss.huggingface.co/t/1st-movie-clip/175306`: «If ReActor fails, use the original frame. Never let a failed ReActor frame disappear from the final timeline.»
2. `face_restore_visibility = 1.0` + `codeformer_weight = 0.5` → «eerily, overly smooth "fake plastic AI skin" look». Рекомендация — **0.6–0.7**.
3. RIFE добавлен не в том месте.

### Канонический pipeline

```
видео → разбить на нумерованные кадры (НЕ через VHS_LoadVideo)
     → ReActor на КАЖДЫЙ кадр как на отдельное изображение (батч, не поток)
     → сохранить кадры в отдельную папку
     → fallback: если ReActor не нашёл/не справился — копия ОРИГИНАЛЬНОГО кадра
     → проверить: количество кадров на входе == на выходе
     → пересобрать видео из полной папки кадров
     → RIFE 2× (только если flicker остался)
```

**Ключевые настройки ReActor (проверены по civitai-гайду 2026 и обсуждению ReActor #215):**

| Параметр | Значение | Почему |
|---|---|---|
| `face_restore_model` | **`GPEN-BFR-512`** (альтернатива CodeFormer) | «gives maximum quality with turned to the side faces». GPEN восстанавливает детали, которые inswapper 128px не даёт |
| `face_restore_visibility` | **0.7–0.8** | 1.0 = «airbrushed» |
| `codeformer_weight` | 0.5 | баланс |
| `facedetection` | `retinaface_resnet50` | стабильнее на поворотах |
| confidence threshold | **0.7–0.8** | меньше ложных детекций, но не пропуск реальных лиц |
| `detect_gender_*` | `no` | по умолчанию |

**Важно:** при `visibility = 1.0` получаем «fake plastic AI skin» — это **прямо наша задача** (фотореализм кожи). Поэтому `face_restore_visibility ≤ 0.8` + `codeformer_weight 0.5` обязательны.

### Деталь-пасс (detail pass) для кожи — без flicker

**Ключевой принцип: детализацию кожи делает ВНУТРИ видео-пайплайна (SeedVR2 с temporal attention), а не покадровый upscaler.**

| Слой | Инструмент | Почему не flicker |
|---|---|---|
| 1. Микротекстура кожи | **SeedVR2** (3B fp8) | Полное 3D space-time attention; batch 4n+1 + temporal_overlap |
| 2. Разрешение 1080p | **H3 Hires Fix** (латент-апскейлер) | Уточнение в латентном пространстве, без decode→encode round-trip; аудио первого прохода пинуется |
| 3. Локальная детализация лица | **H3 Face Atlas** (`ComfyUI-H3-FaceDetail`) | Каждое лицо в 512px-тайле на одном холсте → ~60 токенов на лицо вместо 2. Маска регена = 0 (пин), audio пинится. «With context they stayed as stable as pass 1» |
| 4. Позитивная кожа | ReActor с `visibility 0.7` + fallback на оригинал | Покадровый, но с GPEN-BFR-512 и порогом 0.75 |
| 5. Сглаживание остатка | **RIFE 2×** | Только после 1–4, только если flicker остался. RIFE удваивает файл и время |

**Настройки SeedVR2 (официальный README + seedvr2.net):**
- `batch_size` = **4n+1**: 13 или 17 для 5 с @ 24fps (120 кадров)
- `temporal_overlap` = **2–4**
- `uniform_batch` = **True** — иначе последний батч короче → артефакты
- `resolution` = 1080, `color correction` = lab
- `blocks_to_swap` + VAE tiling при OOM (сначала снижать batch, потом включать tiling)
- ⚠️ SeedVR2 «reconstructs more than the source can actually justify» — может подрисовать/изменить цвет глаз. Поэтому SeedVR2 **после** H3, а не вместо.

**H3 Face Atlas** (опционально, только для дальних планов): face-детектор `deepghs/anime_face_detection` v1.4 (ONNX, CPU) находит и живые лица; рендер второго прохода 72 с vs 171 с на 5090.

**Рекомендация по порядку:** SeedVR2 → (H3 Face Atlas для дальних планов) → ReActor-по-кадрам → RIFE.

---

## (г) План чистки (точные пути, без удаления по подстроке имени)

### Г.1 FreeVideo_output — 35 ГБ на G:

**Структура (проверено):**
```
G:\FreeVideo_output\
  FreeVideo\              31 ГБ  ← сырые артефакты рендеров (регенерируемые)
    .result-cache\         84 КБ
    2026-10-13\ ... 2026-11-02\   ← по датам
      <sha256>\             ← по job-uuid
        first.png, prompt.txt, media.json, video.json, generate.log
        video.artifacts\
          rgb.npy           ← 29.6 ГБ / 65 файлов  ← ПИК РАСХОДА
          latents.pt        ← 1.3 ГБ / 75 файлов
          conditioning.pt   ← 1.1 ГБ / 79 файлов
          audio.npy, audio.wav
        media\, *.log, *.csv
  2026-10-13_vault_backup\ 3.2 ГБ  ← ФИНАЛЬНЫЕ mp4 (deliverables) — НЕ ТРОГАТЬ
  2026-10-13\              739 МБ  ← 1 job
  h3_r2v\                  0
  lora_dl\                 8 КБ
```

**Безопасные шаги (по возрастанию риска):**

**Шаг 1 — удалить сырые кадры `rgb.npy` (29.6 ГБ).** Это декодированные пиксели, полностью регенерируемые из `latents.pt`. Удаляются по точному пути:
```bash
# Показать перед удалением (dry run)
find "/g/FreeVideo_output/FreeVideo" -name "rgb.npy" -printf "%s %p\n" | sort -rn | head -5
# Итог: 29.6 ГБ в 65 файлах

# Удалить (только этот файл, только в этой ветке)
find "/g/FreeVideo_output/FreeVideo" -type f -name "rgb.npy" -delete
```

**Шаг 2 — удалить промежуточные латенты (2.6 ГБ).** `latents.pt` + `conditioning.pt` регенерируются, но `.pt` несёт состояние для resume — **удалять только если job'ы не нужно перезапускать**:
```bash
find "/g/FreeVideo_output/FreeVideo" -type f \( -name "latents.pt" -o -name "conditioning.pt" \) -delete
```

**Шаг 3 — удалить старые job-папки целиком (до ~26 ГБ дополнительно).** Только для дат, чьи результаты уже в vault:
```bash
# Сначала посмотреть, что удаляется
du -sh /g/FreeVideo_output/FreeVideo/2026-10-1[345678]/
# 2026-10-25: 7.9G  2026-10-14: 5.1G  2026-10-28: 4.1G
# 2026-10-27: 3.7G  2026-10-29: 2.4G  2026-10-13: 2.2G  2026-10-26: 2.1G  2026-10-30: 1.7G
rm -rf "/g/FreeVideo_output/FreeVideo/2026-10-13" \
       "/g/FreeVideo_output/FreeVideo/2026-10-14" \
       "/g/FreeVideo_output/FreeVideo/2026-10-15" \
       "/g/FreeVideo_output/FreeVideo/2026-10-25" \
       "/g/FreeVideo_output/FreeVideo/2026-10-26" \
       "/g/FreeVideo_output/FreeVideo/2026-10-27" \
       "/g/FreeVideo_output/FreeVideo/2026-10-28" \
       "/g/FreeVideo_output/FreeVideo/2026-10-29" \
       "/g/FreeVideo_output/FreeVideo/2026-10-30"
```

**⛔ НЕ УДАЛЯТЬ:**
- `G:\FreeVideo_output\2026-10-13_vault_backup\` — 3.2 ГБ финальных mp4 (deliverables)
- `G:\FreeVideo_output\2026-10-13\` — отдельная папка, 739 МБ, не пересекается с `FreeVideo\2026-10-13`
- `G:\FreeVideo_output\FreeVideo\2026-10-31\`, `\2026-11-01\`, `\2026-11-02\` — свежие (56–164 МБ)

**Итого по G: ≈ 30–32 ГБ** (из 35 ГБ).

### Г.2 AdaLN-кэши VDN-H3 — 44 ГБ на C:

**Критический факт (проверен по манифестам, а не по именам!):**

| | `int8/` | `int8-convrot/` |
|---|---|---|
| `source_id` | `9f16ff95…e442d2` | `9f16ff95…e442d2` — **ИДЕНТИЧЕН** |
| `bf16_source_id` | `d9ead009…6510e93` | `d9ead009…6510e93` — **ИДЕНТИЧЕН** |
| `precision` | int8 | int8 — **тот же** |
| `scale_granularity` | rowwise | rowwise — **тот же** |
| `format` | `freevideo-int8-slim-v1` | `freevideo-int8-slim-v1` — **тот же** |
| `total_bytes` | 22 560 271 320 | 22 560 272 544 |
| `rotation` | **нет ключа** | `convrot`, group 256, Hadamard H4⊗H4⊗H4⊗H4 |
| `modification` | «quantized to per-row int8 (absmax/127)» | «**rotated** by a group-256 regular Hadamard transform (ConvRot) **and** quantized to per-row int8» |

**Вывод: это НЕ «база + производная».** Оба — **два варианта квантизации одного и того же H3**, отличающиеся только наличием ортогонального (Hadamard) вращения весов перед квантизацией. ConvRot — это не апгрейд качества, а отдельная схема (rot + int8), дающая другой numerical profile; `kernel-capabilities.json` фиксирует: backend `linear-int8`, **policy `int8-convrot`**, `rotation_relative_rmse: 0.00739`, `relative_rmse: 2.9e-07`.

**Что реально используется сейчас (проверено):**
```json
// C:\AI\FreeVideo\machine.json
"cache": "C:\\AI\\FreeVideo\\models\\vdn-minimax-h3-edge\\int8-convrot\\cache",
"prepared_format": "int8_convrot",
"vram_gib": 16.4, "ram_gib": 64
```
→ **Движок FreeVideo работает с `C:\AI\FreeVideo\models\vdn-minimax-h3-edge\int8-convrot\`** (30.43 ГБ, отдельная физическая копия — inode отличается).

`C:\AI\Models\comfyui\diffusion_models\VDN-H3\` — это **дубликат**, на который **ни один конфиг FreeVideo не ссылается** (проверено grep'ом по `*.json`/`*.yaml` в `C:\AI\FreeVideo\` → ни одной ссылки на `Models.comfyui`). Он нужен только для ноды `ComfyUI-VDN-H3` в ComfyUI.

**План удаления (в порядке убывания безопасности):**

```bash
# ── Шаг A: удалить НЕИСПОЛЬЗУЕМЫЙ вариант int8 (22.93 ГБ) ──
# Проверка перед удалением (что именно удалится):
du -sb "/c/AI/Models/comfyui/diffusion_models/VDN-H3/int8"
# 22928757172 bytes = 22.93 GB
# Удаление:
rm -rf "/c/AI/Models/comfyui/diffusion_models/VDN-H3/int8"

# ── Шаг B: удалить дубликат int8-convrot из Models/comfyui (22.93 ГБ) ──
# FreeVideo использует СВОЮ копию в C:\AI\FreeVideo\models\...
# НО: сначала убедиться, что нода ComfyUI-VDN-H3 (если используется) ссылается сюда.
du -sb "/c/AI/Models/comfyui/diffusion_models/VDN-H3/int8-convrot"
# 22928780738 bytes = 22.93 GB
rm -rf "/c/AI/Models/comfyui/diffusion_models/VDN-H3/int8-convrot"

# ── Шаг C: общий кэш (0.93 ГБ) — НЕ УДАЛЯТЬ без проверки ──
# manifest: format=freevideo-fp8-slim-v1, precision=fp8, scale_granularity=per_tensor
# Это FP8-вариант (другой precision!), НЕ int8. Удаление сломает fp8-путь.
du -sb "/c/AI/Models/comfyui/diffusion_models/VDN-H3/cache"
# 925254118 bytes = 0.93 GB  → ОСТАВИТЬ
```

**⚠️ Почему нельзя удалять «по подстроке имени»:**
- `int8` и `int8-convrot` — **разные квантизации** (для вас это было ключевое условие) — они взаимозаменяемы по схеме, но **не взаимозаменяемы по конфигу**: движок зафиксирован на `int8-convrot` (`prepared_format: int8_convrot` + `policy: int8-convrot`).
- `cache/` — **FP8** (precision=fp8, per_tensor), а не int8. Удаление по слову "int8" его не тронет, но удаление по слову "cache" — **сломает fp8-путь**.
- `community-sigma3-v1-20261005` / `-audio-20261007` (365/210 КБ) — **расписания сэмплирования**, критичны для 8-шаговых расписаний T2VA/I2VA/FL2VA. Не удалять.
- `rowwise/` (400 КБ), `reference-tables-v1/` (154 КБ), `config/`, `sampling-presets-*`, `sampling-steps-v1-20261009` — метаданные/пресеты. Не удалять.
- `SHA256SUMS`, `bundle.json`, `READY.json`, `manifest.json` в каждом — **манифесты верификации**. Удаление → FreeVideo перестанет доверять payload.

**Итого по C: ≈ 45.9 ГБ** (Шаг A + Шаг B). Если Шаг B делать нельзя (нужна нода ComfyUI-VDN-H3) — **22.93 ГБ** только Шаг A.

### Г.3 Бонус: дубликат H3 (безопасно, 19.9 ГБ)

```bash
# Дубликат подтверждён: размер идентичен (20970379616 байт),
# хэши head/tail/mid (по 4 МБ) совпадают:
#   head 0e8fcf48f69a2d44dea5c907b14ea673 (A == B)
#   tail 8a3eff3bf9939bc8eacada010de30175 (A == B)
#   mid@8GB 163be64fa6c1bc1d9f7c8ed4433b4691 (A == B)
rm "/c/AI/Models/comfyui/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors.bak"
# Экономия: 19.97 ГБ
```
⚠️ Полный `md5sum` на 20 ГБ не помещается в таймаут — проверено выборочным хэшированием (head/tail/mid) + побайтовое сравнение размера.

**Сводка освобождения:**

| Действие | Освободит | Диск | Риск |
|---|---|---|---|
| Шаг A: VDN-H3 `int8` | 22.93 ГБ | C: | Низкий (движок на convrot) |
| Шаг B: VDN-H3 `int8-convrot` (дубликат) | 22.93 ГБ | C: | Средний (нужна нода ComfyUI-VDN-H3?) |
| H3 `.bak` | 19.97 ГБ | C: | Низкий (проверить md5) |
| FreeVideo `rgb.npy` | 29.6 ГБ | G: | Низкий (регенерируемо) |
| FreeVideo старые job-папки | ~26 ГБ | G: | Низкий (результаты в vault) |
| **ИТОГО** | **≈ 121 ГБ** | | |

---

## (д) Что НЕ стоит ставить

| Модель | Почему |
|---|---|
| **Wan 2.5 / 2.6 / 2.7 / 3.0** | **Публичных весов нет.** `huggingface.co/api/models?search=Wan2.7` → `[]`. Официальный org Wan-AI публикует максимум до Wan 2.2. Wan 3.0 существует только как `wanvideo/wan-3-0-video` на HF — но это не официальный Wan-AI и доступ к весам/лицензии под вопросом; официального подтверждения весов нет. Все — только API |
| **LTX-2.3** | Устарел. fp8 = 29.15 ГБ (нужен GGUF). 682k dl / 144 ★ против 2.06M / 8 669 у 2.5. Reddit: «The differences 2.3 and 2.5 are hardly noticeable» |
| **LTX-2.5 официальные int8-файлы** | 21.50 ГБ (DiT) + 15.37 ГБ (Gemma4) = **36.9 ГБ весов** на карте с 16 ГБ. Официально 32 ГБ+ VRAM. Только community GGUF |
| **Wan2.2 A14B bf16** | 28.58 ГБ ×2 = 57 ГБ. Не помещается никуда |
| **Wan2.2 TI2V-5B GGUF Q8_0** | 5.4 ГБ, но 5B и так «subpar» по качеству; тратить 10 ГБ (fp16, точнее) на модель, которая проигрывает H3 по коже — нерационально. Ставить **только если** нужна нативная 720p/24fps без оффлоада |
| **HunyuanVideo 1.5** | fp8 i2v 8.33 ГБ — дёшево, но Reddit консенсус: «slower and worse than the early Wan2.2 version», «Wan2.2 with Lora outperformed it by a wide margin». Как черновой движок — ок, как основной — нет |
| **FLUX.1 Dev bf16 / Kontext bf16** | 23.8 ГБ. Есть fp8 за 11.9 ГБ |
| **Real-ESRGAN как видео-апскейлер** | Покадровый, без временного окна → flicker. Только для статики и для «фото-кадра» |
| **Sakura-MiniMax-H3-Uncensored-GGUF** | Только **текстовый энкодер** (15.08 ГБ Q4_K_M). Замена H3-энкодера на 15 ГБ ради обхода refusal — плохой размен: LoRA-путь уже даёт нужное, а 15 ГБ на E:/C: дорого. Не приоритет |
| **Mochi / CogVideoX** | Mochi fp8 10 ГБ → 480p. CogVideoX 2B — 2024-era. Не для фотореализма |
| **Kandinsky 5.0 Video Lite** | I2V 673 с на H100 — медленнее всех. 12 ГБ VRAM |
| **FramePack F1** | 6 ГБ VRAM, но 5 с клипа = 56 мин на 4060 Laptop. Основана на старом HunyuanVideo 13B. ComfyUI-поддержка только сторонние обёртки |
| **Больше H3 LoRA** | У нас 215 LoRA (54 ГБ) из них ~168 H3. Экосистема уже покрыта. LoRA строго архитектурно-специфичны: FLUX/Qwen/Wan/Z-Image LoRA **нельзя** грузить в H3 («LoRA will load, but will have severe quality issues»). И **fl2va-LoRA только с base, ref2va-LoRA только с ref** |

---

## Порядок действий (итог)

1. **Освободить место:** Г.1 шаги 1–3 (G:, ~30 ГБ) + Г.3 (C:, 19.97 ГБ) + Г.2 шаг A (C:, 22.93 ГБ) → **~73 ГБ**
2. **Поставить приоритет 1** (~5.5 ГБ): латент-апскейлер H3, RealESRGAN, ClearReality, SkinContrast, SeedVR2 fp8 3B, clip_vision_h
3. **Собрать узлы:** `ComfyUI-H3-ResCascade`, `ComfyUI-H3-FaceDetail`, `ComfyUI-MiniMaxH3_LatentUpscaler` (для H3 hires)
4. **Проверить i2v → hires → SeedVR2** на 1 тестовом клипе 5 с, замерить время
5. **Потом** (если нужна 720p-нативка или Wan-фичи): приоритет 2 (+48 ГБ) и/или 3 (+29 ГБ)

---

## Источники

**HF API (проверено 2026-11-02):** `Comfy-Org/Wan_2.2_ComfyUI_Repackaged` (9326230 dl), `Comfy-Org/Wan-Animate-2` (907940), `MiniMaxAI/MiniMax-H3` (6520 ★, 6140874 dl), `Comfy-Org/MiniMax-H3` (31098855 dl), `Lightricks/LTX-2.5`, `Comfy-Org/SeedVR2`, `Comfy-Org/Real-ESRGAN`, `Comfy-Org/flux1-kontext-dev`, `LBH-123-AI/Minimax_h3_latent_Upscaler` (318195 dl, 471 ★), `webmp3/Sakura-MiniMax-H3-Uncensored-GGUF` (26 ★), `ChrisColeTech/LTX-2.5-uncensored-v1.1-FP8`
**VRAM/размеры:** `TF6cool/comfyui-video-model-vram` (данные HF API от 2026-10-09) + `runpod.io/articles/guides/comfyui-wan-2-2` + `Wan-AI/Wan2.2-TI2V-5B` README + `docs.comfy.org/tutorials/video/wan/wan2_2` + `Tencent-Hunyuan/HunyuanVideo-1.5` README + `ComfyUI-LTXVideo`
**NSFW:** `spicyapi.ai/blog/wan-2-2-nsfw`, `spicyapi.ai/blog/wan-2-1-nsfw`, `spicyapi.ai/lora/wan-2-2-nsfw`, `blog.laozhang.ai/en/posts/qwen-image-2-1-nsfw`, `locallyuncensored.com/qwen-image-2-1/`, `cemini23/uncensored-image-gen-wiki` (FLUX), `minimax.io/news/minimax-h3-open-source`, `MiniMaxAI/MiniMax-H3` LICENSE, `design.minimax.io/h3`, `Qwen/Qwen-Image-2.1` LICENSE, `jsmanifest.com/flux-uncensored-setup-guide`
**Flicker/face-lock:** `Gourieff/ComfyUI-ReActor` discussion #215, `discuss.huggingface.co/t/1st-movie-clip/175306`, `civitai.com/articles/31600/the-definitive-guide-to-face-swapping-in-2026`, `atlascloud.ai/blog/tips/comfyui-face-swap`, `alexanderharte.com/comfyui-face-swap-workflow-guide/`
**Upscaler:** `brokenthumbsmedia/comfyui-seedvr2_videoupscaler` README, `seedvr2.net/seedvr2-comfyui-settings`, `videoproc.com/video-editor/open-source-video-upscaler.htm`, `studio.aifilms.ai/blog/seedvr2-real-esrgan-open-source-video-upscaler`, `apatero.com/blog/best-ai-image-upscaler-2026-topaz-magnific-upscayl`
**H3 1080p/качество:** `github.com/tazmannner/ComfyUI-H3-ResCascade`, `github.com/tazmannner/ComfyUI-H3-FaceDetail`, `MiniMax-AI/awesome-minimax-h3-integration`, `github.com/keboqi/minimax-h3`, `suno.bi/en/blog/minimax-h3-open-source-ecosystem-explained`, `docs.sogni.ai/models/minimax-h3/`, `minimax3.org/minimax-h3-reddit`
**Reddit:** r/comfyui `1vpd9b8`, `1vqtkpm`, `1wz3s0q`, `1w4x4sp`, `1xi1kc3`, `1w4avpc`, `1olyi0g`, `1pe6ihp`; r/StableDiffusion `1nglddj`
**Стенд (локально):** `comfy_extras/nodes_minimax_h3.py` (BASE_SHORT_EDGE/MAX_PIXELS), `comfy_extras/nodes_wan.py` (Wan22ImageToVideoLatent), `C:\AI\FreeVideo\machine.json`, `kernel-capabilities.json`, `VDN-H3/*/cache/manifest.json`, `nvidia-smi`, psutil
