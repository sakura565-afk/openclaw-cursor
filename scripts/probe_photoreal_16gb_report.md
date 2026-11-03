# Фотореалистичное AI-видео на 16 ГБ VRAM (RTX 4070 Ti SUPER) — ноябрь 2026

Дата: 2 ноября 2026. Стенд: RTX 4070 Ti SUPER 16 ГБ (Ada, SM89), 63.8 ГБ RAM, ComfyUI 0.43.0 (PROD :8188, LAB :8189),
FreeVideo 0.4.28 (VDN-H3, int8_convrot), i5-14400F.

Все локальные цифры получены прогоном `freevideo plan` на НАШЕЙ машине и чтением установленного кода движка.
Ссылки проверены. Где консенсуса нет — так и написано.

---

## 0. ГЛАВНЫЙ ВЫВОД (одно предложение)

**Ваша проблема не в постобработке, а в том, что FreeVideo в режиме `light` семплит на 25 % от финальной площади
(672×384 для цели 1344×768) и поднимает результат латентным 3D-conv апскейлером — микротекстура физически не может
родиться в латенте такого масштаба; лечится это сменой тарифа на `medium` (44 % площади, 896×512) или, лучше,
однопроходным `high`/`max` на полном разрешении, а не апскейлом.**

---

## (а) ТАБЛИЦА МОДЕЛЕЙ

| Модель | Нативное разрешение | fps | LoRA | Нативный звук | VRAM | HF-ссылка |
|---|---|---|---|---|---|---|
| **MiniMax H3** (fl2va / ref2va) | **1344×768** (short edge 768), 24 fps, до ~15 с; сетка 17k+5 кадров | 24 | нет обязательных; turbo-LoRA 4/8 step (опц.), realism-LoRA | **да, стерео** (голос/SFX/музыка в одном проходе) | 8 ГБ+ через FreeVideo; 16 ГБ — комфорт | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) · [MiniMaxAI/MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3) |
| **HunyuanVideo 1.5** | 720p (1280×704–720), 24 fps, 5–10 с | 24 | нет (есть step-distill варианты) | нет | ~14 ГБ BF16 с offload; 19.2 ГБ по факту ComfyUI-шаблона | [tencent/HunyuanVideo-1.5](https://huggingface.co/tencent/HunyuanVideo-1.5) · [ComfyUI-гайд](https://docs.comfy.org/tutorials/video/hunyuan/hunyuan-video-1-5) |
| **Wan2.2 TI2V-5B** | 720p @24fps, VAE 16×16×4 | 24 | нет (много turbo/4-step LoRA) | нет | ~8 ГБ+ с offload; 4090 — 8.5 мин на 5 с | [Wan-AI/Wan2.2-TI2V-5B](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B) · [GitHub](https://github.com/Wan-Video/Wan2.2) |
| **Wan2.2 I2V-A14B** (MoE) | 720p @24fps | 24 | обычно 4-step distill (2 high-noise + 2 low-noise, CFG 1) | нет | 24 ГБ+ комфортно | [Wan-AI/Wan2.2-I2V-A14B](https://huggingface.co/Wan-AI/Wan2.2-I2V-A14B-Diffusers) |
| **LTX-2 / LTX-2.5** | 768×512 база → 2-стадийный апскейл до 1280×704+ (4K/50fps — только облако LTX-2.3) | 24/25/48/50 | distilled-LoRA (8 шагов, CFG 1); есть spatial/temporal upscaler-модели | **да** (audio+video в одной модели) | 32 ГБ с FP8 — таргет; GGUF Q4_K_M — 16 ГБ | [Lightricks/LTX-2](https://huggingface.co/Lightricks/LTX-2) · [LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5) · [GGUF](https://huggingface.co/ruygar/LTX-2.5-Comfy-GGUF) |
| **LTX-Video (0.9.8)** | 1216×704 @30 fps | 30 | **да**: ICLoRA-detailer-13B + spatial/temporal upscaler 0.9.7 | нет | 2B fp8 — лёгкий; 13B fp8 — средний | [Lightricks/LTX-Video](https://huggingface.co/Lightricks/LTX-Video) |
| **CogVideoX 1.5-5B** | 480p (10 ГБ BF16) | 8/16 | нет | нет | 10 ГБ BF16 | [THUDM/CogVideo](https://huggingface.co/THUDM/CogVideo) |
| **FramePack** | 640×640 дефолт | 24 | нет (обёртка HunyuanVideo I2V) | нет | 6 ГБ (anti-drift, длинные клипы) | [lllyasviel/FramePackI2V_HY](https://huggingface.co/lllyasviel/FramePackI2V_HY) · [diffusers docs](https://huggingface.co/docs/diffusers/api/pipelines/framepack) |
| **SkyReels-V2 DF** | 544×960 @97f (1.3B), 720×1280 @121f (14B) | 24 | нет | нет | 1.3B → 14.7 ГБ пик | [Skywork/SkyReels-V2-DF-1.3B-540P](https://huggingface.co/Skywork/SkyReels-V2-DF-1.3B-540P) · [GitHub](https://github.com/skyworkai/skyreels-v2) |
| **SVD / SVD-XT 1.1** | 1024×576, 14/25 кадров | 6 | нет | нет | <10 ГБ | [stabilityai/stable-video-diffusion](https://huggingface.co/stabilityai/stable-video-diffusion) — **устарел, не для продакшна** |

**Ссылки по моделям:** [ComfyUI H3 native](https://docs.comfy.org/tutorials/video/minimax/minimax-h3-native) ·
[H3 overview](https://docs.comfy.org/tutorials/video/minimax/minimax-h3) ·
[Wan2.2 README](https://github.com/Wan-Video/Wan2.2) ·
[LTX-2.3 release notes](https://ltx.io/release-notes) ·
[HunyuanVideo-1.5 GitHub](https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5) ·
[LTX-Video GitHub](https://github.com/Lightricks/ltx-video)

---

## (б) РЕЦЕПТ КОНВЕЙЕРА ДЛЯ 16 ГБ

### Б.1 Диагноз: что именно делает FreeVideo `light` (проверено локально)

Из установленного `freevideo_engine/tiers.py` (E:\ComfyUI_LAB_037\...\FreeVideo\freevideo_engine\tiers.py):

```python
TIERS = {
    'light':  dict(steps=8,  two_pass=True,  step_cache=None,        upscale=2),   # первый проход 1/2
    'medium': dict(steps=8,  two_pass=True,  step_cache=None,        upscale=1.5), # первый проход 1/1.5
    'high':   dict(steps=16, two_pass=False, step_cache='easycache-av', upscale=2), # ОДИН проход, полный res
    'max':    dict(steps=20, two_pass=False, step_cache='easycache-av', upscale=2), # ОДИН проход, полный res
}
```

Прогон `freevideo_engine/two_pass.py` на НАШЕМ стенде даёт:

| Цель | Тариф | Разрешение 1-го прохода | Пикселей 1-го прохода | % от финала |
|---|---|---|---|---|
| 1344×768 | light | **672×384** | 258 048 | **25.0 %** |
| 1344×768 | medium | **896×512** | 458 752 | **44.4 %** |
| 1344×768 | high / max | 1344×768 (один проход) | 1 032 192 | 100 % |
| 736×1120 | light | **384×576** | 221 184 | **26.8 %** |
| 736×1120 | medium | **512×768** | 393 216 | 47.7 % |
| 1056×704 | light | 544×352 | 191 488 | 25.8 % |
| 1056×704 | medium | 704×512 | 360 448 | 48.5 % |

**Вот причина ваших измерений.** В режиме `light` FreeVideo сначала рисует кадр на 384-пиксельной сетке (23×35 = 805
токенов) и только потом поднимает латент до 736×1120 обученным 3D-conv апскейлером
(`LBH-123-AI/Minimax_h3_latent_Upscaler`, локально: `C:\AI\FreeVideo\models\latent_upscaler\`).
Латентный 3D-conv не умеет изобретать поры и волоски — он дорисовывает «гладкую деталь» в пределах того, что
видит в 32-пиксельном токене. Отсюда ровно ваши цифры: 8.68 (стоп) → 4.65 (первый кадр видео) → 5.12 (SeedVR2) → 5.00 (RIFE).

### Б.2 Почему 1056×704 — НЕ шаг вперёд (это ловушка)

Токен H3 = 32×32 пикселя (VAE жмёт 16× по площади, DiT патчит латенты 2×2).

| Конфиг | Сетка токенов | Всего токенов |
|---|---|---|
| 736×1120 (сейчас) | 23×35 | 805 |
| **1056×704** | 33×22 | **726 — МЕНЬШЕ** |
| 1280×704 | 40×22 | 880 |
| **1344×768** | 42×24 | **1008** |
| 1920×1088 | 60×34 | 2040 |

Рендер first-frame на 1056×704 даёт **меньше токенов**, чем 736×1120, т.е. для кожи это шаг назад.
Реальный рычаг — не «больше мегапикселей», а **плотность токенов на лицо**, т.е. кроп (лицо ближе к камере).

### Б.3 Рекомендуемый конвейер (16 ГБ)

**Шаг 0 — исходник.** Стоп-кадр рендерить в **1344×768 или выше** (Qwen-Image-2.1 / Z-Image-Turbo уже стоят).
Лицо в кадре — крупно (close-up/средний план, не wide).

**Шаг 1 — видео, тариф `medium` (FreeVideo).**
Цель: **1344×768, 5 с (124 кадра), 24 fps**. Первый проход 896×512 (44 % площади вместо 25 %).
Проверено `freevideo plan` на нашей машине: 896×512 ×124 f = 16 576 токенов, 8 resident blocks, head 8 — помещается с запасом.
Ожидаемое время: ~2× тарифа `light` (мера `UPSCALE_SHARE` в tiers.py: фактор 1.5 → ×1.27 к времени).

**Шаг 2 — если нужен максимум деталей: тариф `high` (16 шагов, один проход, полный res).**
Проверено `freevideo plan`: 1344×768 ×124 f = 37 296 токенов, workspace 9.58 ГиБ, 8 resident blocks → **вписывается в 13.13 ГиБ бюджета**.
1344×768 ×243 f (10 с) = 72 576 токенов, workspace 12.50 ГиБ, 1 resident block → вписывается, но впритык.
1920×1088 ×124 f = 75 480 токенов, workspace 12.74 ГиБ, 0 resident blocks → вписывается, но тяжело.
**Это единственный путь, где кожа рождается в правильном масштабе.**

**Шаг 3 — доработка деталей (если тариф `light`/`medium`).**
Не в посте, а **в латенте**: [ComfyUI-H3-ResCascade](https://github.com/tazmannner/ComfyUI-H3-ResCascade) («H3 Hires Fix»):
латентный 3D-апскейлер LBH-123-AI → re-noise до **sigma 0.45** → хвост нативного расписания beta57 на целевом размере.
Заявленный эффект: **+29 % fine-edge detail против Lanczos** того же материала, без frame-to-frame boil.
Важно (из README): стартовать надо с **полноценного нативного 1 MP рендера**, а не с turbo-первого-прохода; turbo в refine
безопасен (DMAD 0.8, 2 шага), в pass 1 — меняет take.

**Шаг 4 — доводка лиц (опционально).**
[ComfyUI-H3-FaceDetail](https://github.com/tazmannner/ComfyUI-H3-FaceDetail): находит мелкие лица, магнифицирует
каждое в свою плитку 512 px (~60 токенов вместо 2), перерисовывает, вклеивает обратно с geometry lock и fidelity gate.
Ключевое: sigma по размеру лица — 3× → 0.70, 5× → 0.80, 10× → 0.95; маленькое лицо требует глубокого перериса.

**Шаг 5 — апскейл в пиксели (только если нужно больше, чем дал рендер).**
Порядок: **апскейл → интерполяция → (face-restore последним, если нужен)**.
- Диффузионный апскейл (SeedVR2 3B fp8) — **до** интерполяции: он пересобирает детали кадра, RIFE потом интерполирует уже
  готовую деталь и не «ползёт».
- RIFE/GIMM-VFI — **после** апскейла, иначе варпфреймы дают морфинг-артефакты (это же в [benchmark](https://github.com/ismael-joffroy-chandoutis/ai-upscaler-benchmark/blob/main/docs/video-upscalers.md)).
- Face-restore (CodeFormer/GFPGAN) — **последним** и **точечно**; он перерисовывает лицо и ломает идентичность, а на видео
  даёт «прыгающую» кожу.
- Важно: SeedVR2 требует **batch_size ≥ 5** для активации временной согласованности (минимум 5 кадров), иначе flicker.

**Шаг 6 — локальный де-шин на первом кадре.** См. раздел (г).

### Б.4 Сводная таблица тарифов FreeVideo на НАШЕЙ машине (прогон `freevideo plan`)

| Геометрия | Токены | Resident blocks | Head | Вердикт |
|---|---|---|---|---|
| 1344×768, 124 f (5 с) | 37 296 | 8 | 8 | ✅ однопроходный `high` вписывается (ws 9.58 ГиБ) |
| 1344×768, 243 f (10 с) | 72 576 | 1 | 8 | ⚠️ вписывается, ws 12.50 ГиБ |
| 1344×768, 362 f (15 с) | 107 856 | 0 | 4 | ⚠️ движок просит host-resident attention outputs |
| 1920×1088, 124 f | 75 480 | 0 | 8 | ⚠️ ws 12.74 ГиБ, 0 resident — на грани |
| 1056×704, 124 f | 26 862 | 10 | 8 | ✅ комфортно |
| 736×1120, 124 f | 29 785 | 10 | 8 | ✅ комфортно |
| 896×512, 124 f (1-й проход `medium`) | 16 576 | 8 | 8 | ✅ комфортно |
| 672×384, 124 f (1-й проход `light`) | 9 324 | 8 | 8 | ✅ комфортно |

Бюджет: `gpu_budget_bytes` = 14 098 624 512 Б = **13.13 ГиБ** (free VRAM 13.38 ГиБ − резерв 0.25 ГиБ на Windows).
Движок: `sage2`, `ff_chunk 2048`, `head_chunk 8`, `projection_chunk 1024`, `stream_weights true`, `pin_host_gb 16.06`.

**Референс из таблицы сообщества FreeVideo** ([community-results.md](https://github.com/FlashML-org/FreeVideo/blob/main/docs/community-results.md)):
`4070 Ti SUPER | 16 GB | 96 GB | 1344×768 | 10 s | Light | 0.3.24 | 575 s` — **ровно наша карта, H3 на 1344×768 за 575 с**.

---

## (в) ЧТО ДАСТ ФОТОРЕАЛИЗМ КОЖИ В ПЕРВУЮ ОЧЕРЕДЬ

Ранжирование по вкладу в микротекстуру (по доказанным механизмам, не по вкусу):

**1. ПРАВИЛЬНЫЙ МАСШТАБ СЕМПЛИРОВАНИЯ (решает ~всё).**
Это не «апскейл», а то, на каком размере токенов модель рисует лицо.
Прямая формулировка из [FaceDetail README](https://github.com/tazmannner/ComfyUI-H3-FaceDetail):
> «H3's video VAE compresses 16x spatially, and the DiT then patches 2x2 latents, so **every DiT token covers 32x32 pixels**.
> At 1024x576 the model draws on a 32x18 grid. A full-body wide shot puts a 35 px face on that grid, which is about
> 1.5x1.5 tokens: the eyes, brows and mouth have nowhere to go, so the model paints a blob.»

И из [ResCascade README](https://github.com/tazmannner/ComfyUI-H3-ResCascade):
> «H3 decides layout, motion, acting **and the detail level** in the high-noise steps: with shift 12, 12 of the 20 steps
> sit above sigma 0.8. A low-res first pass spends those steps on 4x fewer 32 px tokens.»

**→ Сделайте лицо крупным в кадре и рендерите в 1344×768 одним проходом (`high`). Это даёт больше, чем любой апскейл.**

**2. ТАРИФ `medium`/`high` ВМЕСТО `light`.** 44 % → 100 % площади первого прохода. Механически: больше токенов
на тот же латент → выше вероятность, что поры вообще попадут в сетку.

**3. REALISM-LoRA (второй по силе, но только после масштаба).**
`fal/MiniMax-H3-Realism-People-LoRA` (триггер `r34l1sm`, rank 32, 1500 steps, **обучен в высоком разрешении**) —
[карточка модели](https://huggingface.co/fal/MiniMax-H3-Realism-People-LoRA). Ключевая фраза:
> «Skin texture, pores, fine hair and grain live in high spatial frequencies, and at a lower training bucket the
> latents barely carry them, so there is little for the adapter to learn.»

**У нас этот LoRA уже стоит:** `C:\AI\Models\comfyui\loras\Realism People - MinimaxH3 - r34l1sm,0.6-1str.safetensors`.
Также стоят `MMH3_Facial_Realism_CloseUp.safetensors` (в HF: [prithivMLmods/MiniMax-H3-Facial-Realism-CloseUp](https://huggingface.co/prithivMLmods/MiniMax-H3-Facial-Realism-CloseUp)).

**⚠️ НО:** [видео-рецепт Prompt Mastery (5 окт 2026, 37k просмотров)](https://www.youtube.com/watch?v=wtHrJ4qb_Wc) и
[видео Studio Seki (31 окт 2026)](https://www.youtube.com/watch?v=ZadT8iNcP1Q) дают **прямо противоположные** советы:
- Prompt Mastery: «Push skin texture as far as it goes and the model gets worse at everything else» → tier-система, и
  **«why upscaling and a second pass kill skin, and why this runs one pass at 8 steps»**.
- Studio Seki (40+ тестов): Realism People LoRA **0.5** для обычного света, **0.75** для вспышки/пота/тёплых ламп;
  **facial realism LoRA надо отключить — он работает как smoothing-фильтр**; в промпт «natural soft matte skin texture,
  fine pores and zero oily shine»; де-шин первого кадра негенеративным фильтром на полную силу.

**Консенсуса нет** по силе/типу realism-LoRA. Рабочая compromise: `Realism People` 0.5–0.75 + **НЕ** включать
`MMH3_Facial_Realism_CloseUp` + де-шин первого кадра + matte-кожа в промпте.

**4. РЕФЕРЕНС-КАЧЕСТВО.** Для ref2va: качество ref2va заметно хуже fl2va
([reddit](https://www.reddit.com/r/StableDiffusion/comments/1vygmwv/minimax_h3_how_to_deal_with_plastic_skin_when/)):
> «The quality of the ref2va is quite bad compared to the flva2va. You might want to try one of these hybrid checkpoints
> that mixes the quality of the fl2va with the ref2va» → [smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models](https://huggingface.co/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models).

Но есть **строгое НЕТ-решение** от автора белой paper
([ANe5s/ComfyUI-MiniMax-H3-Hybrid](https://github.com/ANe5s/ComfyUI-MiniMax-H3-Hybrid/blob/main/minimax_h3_hybrid_technical_whitepaper_en.md)):
> «O4: Full-range overlay is "sharpest" but accumulates artifacts… 45–49 shows no collapse and no extra anomalous objects».
> Production-пресеты: `none`, `block_range_adaln` (45–49), `all_adaln`. `ref2va_exact` **исключён** из-за структурной деградации.
Функциональный cosine AdaLN между pruned-парами = **0.9976** при весовом −0.748 → «reference-возможность несёт общий
conditioning path, а не переобученные AdaLN-модераторы Ref2VA».

**5. АПСКЕЙЛ — ПОСЛЕДНИЙ, НЕ ПЕРВЫЙ.** SeedVR2 **не** создаёт поры. Его собственная карточка предупреждает
про over-sharpen 720p AI-видео, а это ровно наш вход:
[SeedVR2-7B card](https://huggingface.co/ByteDance-Seed/SeedVR2-7B) — «tends to over-sharpen lightly degraded content
like 720p AI-generated videos» (цитата приведена в [benchmark](https://github.com/ismael-joffroy-chandoutis/ai-upscaler-benchmark/blob/main/docs/video-upscalers.md)).
Рекомендация оттуда же: **3B, малый batch, не штабелировать шарпинг**, закончить негенеративным проходом.

**6. НЕГАТИВНЫЕ ПРОМПТЫ — тупик.** [Reddit, 3 недели экспериментов](https://www.reddit.com/r/comfyui/comments/1szt9lk/i_spent_3_weeks_trying_to_fix_ai_skin_with/):
«I spent 3 weeks trying to fix AI skin with negative prompts. Here's why that entire approach is a dead end.»
Позитив — лучше: «skin pores», «film grain», «natural soft matte skin texture» работают, а «plastic skin» в негативе — нет.

---

## (г) ЧТО НЕ РАБОТАЕТ

| Не работает | Почему | Источник |
|---|---|---|
| **SeedVR2-апскейл как источник текстуры** | Диффузионный SR перерисовывает «правдоподобную деталь» в масштабе латента, но по своей карточке over-sharpens ровно 720p AI-видео. Ваши же измерения: 4.65 → 5.12 (прирост 10 %, не 45 %) | локальные замеры + [SeedVR2 card](https://huggingface.co/ByteDance-Seed/SeedVR2-7B) |
| **RIFE/интерполяция как источник деталей** | Интерполяция усредняет два соседних кадра — детализацию не создаёт. Ваши замеры: 5.12 → 5.00 | локальные замеры |
| **Face-restore на видео** | Перерисовывает лицо → «прыгающая» кожа + дрейф идентичности | [benchmark](https://github.com/ismael-joffroy-chandoutis/ai-upscaler-benchmark/blob/main/docs/video-upscalers.md): «Topaz Proteus (Manual)» — «will not invent a new face» |
| **Real-ESRGAN / 4x-UltraSharp / waifu2x на фотореалисте** | GAN без временной модели: «sharpen what is already there… crisp up plastic skin and amplify flicker». UltraSharp — CC-BY-NC-SA (не коммерция) | [benchmark, таблица GAN](https://github.com/ismael-joffroy-chandoutis/ai-upscaler-benchmark/blob/main/docs/video-upscalers.md) |
| **Lanczos как «детализация»** | Интерполяция, а не генерация. Но как **финальный** негенеративный проход — полезен: без него SeedVR2-пере-шарпинг выглядит грубо | benchmark: «Lanczos/bicubic + light sharpen» — baseline floor |
| **Рендер first-frame в 1056×704 как «улучшение»** | 33×22 = 726 токенов — **меньше**, чем у 736×1120 (805). Для кожи шаг назад | расчёт по VAE16×+patch2× |
| **FreeVideo `light` для фотореализма** | Первый проход на 25 % площади; вся микротекстура рождается в латенте 672×384 (23×12 сетка) | локальный прогон `two_pass.plan` |
| **«Render low, upscale, refine» вместо рендера в 1 MP** | Refine от ~0.9 перерешает весь take; halving разрешения только в середине расписания = −35 % скорости и потеря ~половины fine-detail (цвета радужки, текстуры); «Re-noising and 8 more full-res steps don't bring it back» | [ResCascade README](https://github.com/tazmannner/ComfyUI-H3-ResCascade) |
| **Training-free attention shortcuts (pooled/windowed keys) на H3** | «break H3: half of a video token's attention goes to frames more than 2 latent frames away» | там же |
| **Таeh3 для кодирования канваса в FaceDetail** | «thin streaks across faces» — нужен настоящий VAE | [FaceDetail README](https://github.com/tazmannner/ComfyUI-H3-FaceDetail) |
| **Размытие плитки лица перед перерисовкой** | Модель сохраняет размытие; sharpening плитки усиливает пиксельную сетку. Помогает только глубже sigma | там же |
| **Ref2VA-exact оверлей (25–49) на H3** | Даёт максимальную резкость, но накапливает структурные аномалии | [Hybrid whitepaper](https://github.com/ANe5s/ComfyUI-MiniMax-H3-Hybrid/blob/main/minimax_h3_hybrid_technical_whitepaper_en.md) |
| **CogVideoX / SVD как production-вариант** | Отстали по качеству; SVD — только 14/25 кадров | [модели-карты](https://huggingface.co/stabilityai/stable-video-diffusion), [обзор 2026](https://www.turingpost.com/p/6-opensource-video-generation-models) |

---

## (д) ПЛАВАЮЩЕЕ ЛИЦО В ХВОСТЕ КЛИПА

Это отдельная от текстуры проблема. **Измеренные данные** (reddit, r/comfyui, 12 авг 2026,
[«Measured how fast identity drifts in MiniMax H3 image-to-video»](https://www.reddit.com/r/comfyui/comments/1vm8j2c/measured_how_fast_identity_drifts_in_minimax_h3/)):
MiniMax H3, i2v, 6 с, 24 fps, 1120×1664.

| Крупность плана | Полезная длина до дрейфа |
|---|---|
| Лицо мелкое, повёрнуто в 3/4 (со спины) | **полные 6.5 с**, дрейфа нет |
| Поясной, лицо среднее | **~6.2 с** |
| **Крупный план (лицо в кадре)** | **~2.9 с** |

Правило: **для крупных планов рендерить отдельный клип на ~3 с, а не рендерить 6 с и резать на два по 3 с** — вторая
половина уже другой человек.

Режим отказа (одинаковый каждый раз): лицо становится шире и круглее, линия челюсти размывается, улыбка становится
обобщённой, **кожа становится восковой**.

Ещё три правила оттуда же:
1. **Большие эмоции ломают идентичность быстрее всего.** Широкая искренняя улыбка = другой человек в пике движения
   (полные щёки, шире челюсть, глубокие складки у носа). Держите выражение мелким, энергию — в монтаже.
   Слово **«crinkling» — исключить**: модель читает буквально и морщит ненужное.
2. **Планы без лица опасны, а не безопасны.** Тёмная неопределённая область — модель заполняет её статистически
   подходящим содержимым, обычно человеком. Один из клипов «вырастил» вторую женщину с бокалом через ~1.5 с.
   Крупные планы работают только если кадр плотно заполнен объектами и нет пустой тёмной зоны.
3. **«No push-in» не останавливает камеру.** Формулировка «не двигай камеру» игнорируется примерно в половине случаев.
   Работает **проверяемое** ограничение: «требуй, чтобы голова субъекта занимала одинаковый размер в кадре
   в первом и последнем кадре».
4. **Всегда first frame, никогда last frame.** С референсом в last frame модель должна «прийти» к нему.

**Практический набор (собран из H3-доков + reddit):**
- Длина клипа ≤ 3 с для крупных планов, ≤ 6 с для средних.
- Референс = **first frame** (fl2va), не last frame.
- **ref2va** для идентичности + гибридная модель smhfacct (b20-49/b30-49) — осторожно, `ref2va_exact` запрещён.
- ≥ 25 шагов (в локальном шаблоне `MiniMaxH3KSampler`: steps 8, cfg 1.5, euler/simple — это turbo-режим;
  для качества нужно 20–25 шагов, shift_video 12 / shift_audio 3).
- H3 «решает» take в первых 12 из 20 шагов (sigma ≥ 0.79) — поэтому любой turbo-LoRA в pass 1 меняет take.
- Для локальной стойки: `Realism People` LoRA помогает с кожей, но её собственный формант «натуральности»
  может тянуть identity — проверять на одном seed.

---

## (е) ПОРЯДОК ПАЙПЛАЙНА (итог)

```
[1] Стоп-кадр  1344×768 (или выше), лицо крупно, де-шин негенеративным фильтром
        ↓
[2] Видео      FreeVideo тариф `high` (16 шагов, ОДИН проход, полный res)
                или `medium` (8+3, первый проход 896×512) + H3 Hires Fix (sigma 0.45)
        ↓
[3] Доводка    FaceDetail (плитки 512 px, sigma 0.70/0.80/0.95 по размеру лица) — если мелкие лица
        ↓
[4] Апскейл    SeedVR2 3B fp8, batch ≥ 5, НЕ шарпить, resolution 1080
        ↓
[5] Интерпол.  RIFE / GIMM-VFI 24→48 (после апскейла, не раньше)
        ↓
[6] Финал      НЕгенеративный проход (Lanczos + лёгкий sharpen), без face-restore
```

---

## (ж) ГДЕ У НАС ДЫРЫ НА СТЕНДЕ (проверено)

| Что | Статус |
|---|---|
| `Realism People - MinimaxH3` LoRA | ✅ есть (`C:\AI\Models\comfyui\loras\`, триггер `r34l1sm`) |
| `MMH3_Facial_Realism_CloseUp` | ✅ есть, но по видео-рецепту его **отключают** |
| `minimax_h3_fl2v_turbo_4step/8step` LoRA | ❌ **НЕТ** — есть только `minimax_h3_ref2v_turbo_4step_v0.1`. Для fl2va/текст-в-видео turbo не установлен |
| `minimax_h3_video_vae_int8_convrot` | ❌ **НЕТ** — стоит `minimax_h3_video_vae_fp16` (5.2 ГБ). Официальная рекомендация ComfyUI — int8_convrot ([Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3)) |
| Wan2.2 TI2V-5B | ❌ весов нет (только `wan2.2_ti2v_5b_index.json`) |
| HunyuanVideo 1.5 | ❌ не установлен |
| SeedVR2 | ✅ 3B fp8 + VAE; есть `seedvr2_ema_3b_fp8_e4m3fn` и `seedvr2_ema_3b_fp16` |
| RIFE | ✅ в VideoHelperSuite |
| `4x-UltraSharp.pth` | ✅ есть, но CC-BY-NC-SA (не коммерция) и для фотореалиста не подходит |
| H3 latent upscaler (LBH-123-AI) | ✅ в `C:\AI\FreeVideo\models\latent_upscaler\` |
| ComfyUI-H3-ResCascade / FaceDetail / RelayHires | ❌ не установлены |

**Минимальный чек-лист доработки стенда (по убыванию отдачи):**
1. Догрузить `minimax_h3_fl2v_turbo_4step/8step` LoRA + `minimax_h3_video_vae_int8_convrot` (официальные имена — в
   [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3)).
2. Поставить [ComfyUI-H3-ResCascade](https://github.com/tazmannner/ComfyUI-H3-ResCascade) (латентный hires-fix).
3. Поставить [ComfyUI-H3-FaceDetail](https://github.com/tazmannner/ComfyUI-H3-FaceDetail) (если в кадре есть мелкие лица).
4. Опционально [Efficient-Large-Model/Open-H3-Regenerate-2K](https://huggingface.co/Efficient-Large-Model/Open-H3-Regenerate-2K)
   — открытая замена закрытому H3-Regenerate-2K: 3 шага Euler, сигмы `[0.909375, 0.857143, 0.666667, 0]`, CFG 1,
   latent upscale + refine, 1344×768 → **2560×1440**. Заявлено 87 с на B200 (dense BF16) / 37 с (Sol-Attn MXFP8) на 5 с.
   На 16 ГБ потребует проверки — но это самый прямой путь к порам на 1440p.

---

## (з) КОНСЕНСУСА НЕТ

- **Какой realism-LoRA лучше** — Prompt Mastery и Studio Seki дают противоположные рецепты по силе и по тому,
  включать ли facial-realism LoRA. Наша compromise: `Realism People` 0.5–0.75, `MMH3_Facial_Realism_CloseUp` выкл.
- **SeedVR2 vs Topaz vs Lanczos для кожи** — нет единого мнения; измерительного сравнения «текстура кожи» с
  человеческой оценкой в найденных источниках нет. Что есть: измерения PSNR/SSIM, где Lanczos выигрывает у ESRGAN
  ([HF blog](https://huggingface.co/blog/Jacob123222/ai-video-upscaling-with-real-esrgan-and-ffmpeg-a-m)),
  и предупреждение SeedVR2 о собственном failure mode.
- **ref2va vs fl2va vs hybrid** — гибридные чекпоинты дают «лучше, чем ref2va, но теряете часть деталей персонажа»
  (reddit), а белая paper запрещает `ref2va_exact` и рекомендует 45–49. Единого рабочего рецепта нет.
- **FlashVSR vs SeedVR2** — FlashVSR (arXiv 2510.12747, [ComfyUI-нода](https://github.com/1038lab/ComfyUI-FlashVSR))
  консервативнее и «меньше выдумывает», SeedVR2 — сильнее перестраивает деталь. Замеры в benchmark — на 5090.
- **HunyuanVideo 1.5 SR vs внешний SeedVR2** — сравнений не найдено.

---

## Полный список использованных источников

**Официальные / первичные:**
- [ComfyUI MiniMax H3 (native)](https://docs.comfy.org/tutorials/video/minimax/minimax-h3-native) · [overview](https://docs.comfy.org/tutorials/video/minimax/minimax-h3) · [prompt guide](https://docs.comfy.org/tutorials/video/minimax/minimax-h3-prompt-guide)
- [Comfy-Org/MiniMax-H3 (weights)](https://huggingface.co/Comfy-Org/MiniMax-H3) · [MiniMax-AI/MiniMax-H3](https://github.com/MiniMax-AI/MiniMax-H3)
- [FlashML-org/FreeVideo](https://github.com/FlashML-org/FreeVideo) · [community-results.md](https://github.com/FlashML-org/FreeVideo/blob/main/docs/community-results.md) · [execution-planning.md](https://github.com/FlashML-org/FreeVideo/blob/main/docs/execution-planning.md) · [VDN-H3 Edge README](https://huggingface.co/OpenVDN/vdn-minimax-h3-edge)
- [Tencent-Hunyuan/HunyuanVideo-1.5](https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5) · [ComfyUI-гайд](https://docs.comfy.org/tutorials/video/hunyuan/hunyuan-video-1-5) · [тех. отчёт](https://arxiv.org/html/2511.18870v1)
- [Wan-AI/Wan2.2-TI2V-5B](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B) · [Wan-Video/Wan2.2](https://github.com/Wan-Video/Wan2.2)
- [Lightricks/LTX-2](https://huggingface.co/Lightricks/LTX-2) · [LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5) · [LTX-Video](https://huggingface.co/Lightricks/LTX-Video) · [LTX release notes](https://ltx.io/release-notes) · [LTX-2 pipelines](https://github.com/Lightricks/LTX-2/blob/main/packages/ltx-pipelines/docs/pipelines.md)
- [ByteDance-Seed/SeedVR2-3B](https://huggingface.co/ByteDance-Seed/SeedVR2-3B) · [SeedVR2-7B](https://huggingface.co/ByteDance-Seed/SeedVR2-7B) · [paper arXiv 2506.05301](https://arxiv.org/html/2506.05301v1) · [ComfyUI-нода](https://github.com/comfyorg/comfyui_seedvr2)
- [smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models](https://huggingface.co/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models) · [Hybrid whitepaper](https://github.com/ANe5s/ComfyUI-MiniMax-H3-Hybrid/blob/main/minimax_h3_hybrid_technical_whitepaper_en.md)
- [fal/MiniMax-H3-Realism-People-LoRA](https://huggingface.co/fal/MiniMax-H3-Realism-People-LoRA) · [prithivMLmods/MiniMax-H3-Facial-Realism-CloseUp](https://huggingface.co/prithivMLmods/MiniMax-H3-Facial-Realism-CloseUp)
- [Efficient-Large-Model/Open-H3-Regenerate-2K](https://huggingface.co/Efficient-Large-Model/Open-H3-Regenerate-2K) · [NVlabs/Sana](https://github.com/NVlabs/Sana/tree/sol-engine/models/Open-H3-Regenerate-2K)
- [tazmannner/ComfyUI-H3-ResCascade](https://github.com/tazmannner/ComfyUI-H3-ResCascade) · [FaceDetail](https://github.com/tazmannner/ComfyUI-H3-FaceDetail) · [RelayHires](https://github.com/tazmannner/ComfyUI-H3-RelayHires) · [FastDerope](https://github.com/tazmannner/ComfyUI-H3-FastDerope)
- [LBH-123-AI latent upscaler](https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler)
- [ismael-joffroy-chandoutis/ai-upscaler-benchmark](https://github.com/ismael-joffroy-chandoutis/ai-upscaler-benchmark/blob/main/docs/video-upscalers.md) + [recipes.md](https://github.com/ismael-joffroy-chandoutis/ai-upscaler-benchmark/blob/main/docs/recipes.md)

**Reddit:** [измерение дрейфа идентичности H3](https://www.reddit.com/r/comfyui/comments/1vm8j2c/measured_how_fast_identity_drifts_in_minimax_h3/) ·
[WAN2.2 всегда гладкая кожа (175 votes)](https://www.reddit.com/r/comfyui/comments/1u9e2rb/wan22_always_creating_extremely_smooth_skin/) ·
[baseline 50 шагов vs turbo 8 шагов](https://www.reddit.com/r/StableDiffusion/comments/1wbtn70/minimax_h3_baseline_at_50_steps_vs_popular_turbo/) ·
[простое решение пластиковой кожи](https://www.reddit.com/r/StableDiffusion/comments/1wbt2ey/a_little_solution_for_the_plastic_skin_with/) ·
[plastic skin в ref2va](https://www.reddit.com/r/StableDiffusion/comments/1vygmwv/minimax_h3_how_to_deal_with_plastic_skin_when/) ·
[негативные промпты — тупик](https://www.reddit.com/r/comfyui/comments/1szt9lk/i_spent_3_weeks_trying_to_fix_ai_skin_with/) ·
[H3 1080p workflow](https://www.reddit.com/r/comfyui/comments/1w4x4sp/minimax_h3_just_got_much_better_cleaner_video/) ·
[Wan2.2 TI2V-5B на 4090 — лица ломались в 2 из 3](https://www.reddit.com/r/StableDiffusion/comments/1xholms/wan22_ti2v5b_on_a_single_rtx_4090_at_stock/) ·
[11 методов видео-апскейла](https://www.reddit.com/r/StableDiffusion/comments/1xha6jy/ive_tested_11_video_upscaling_methods/) ·
[обзор апскейлеров](https://www.reddit.com/r/Aiarty/comments/1rf1jkb/2026_opensource_ai_image_upscaler_review_updated/)

**YouTube:** [Prompt Mastery — Best H3 Realistic Skin Workflow](https://www.youtube.com/watch?v=wtHrJ4qb_Wc) ·
[Studio Seki — Why H3 Skin Looks Oily (3-Step Fix)](https://www.youtube.com/watch?v=ZadT8iNcP1Q) ·
[Ok Todd AI — Finally: Real Skin Textures in H3 Videos](https://www.youtube.com/watch?v=wf3bE7AJVBA) ·
[The Ai Blueprint — H3 Face Refinement (oily skin + identity drift)](https://www.youtube.com/watch?v=UAdBqy5Y2l4) ·
[AI Forge Lab — 4K Upscaler + H3 realism](https://www.youtube.com/watch?v=VtHC45Zen30)
