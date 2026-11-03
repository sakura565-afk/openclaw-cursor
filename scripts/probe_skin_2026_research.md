# Фотореалистичная кожа и детали лица в ВИДЕО: что реально работает в 2025–2026

Исследовательский отчёт. Дата: 2026-11-03. Стенд: ComfyUI 0.43.0, RTX 4070 Ti SUPER 16 ГБ,
MiniMax H3, Qwen-Image-Edit 2509/2511, Z-Image, FLUX.1-dev, SeedVR2 3B/7B, RIFE, FreeVideo (LAB :8189).

## Легенда разметки

| Метка | Значение |
|---|---|
| **[И]** | Подтверждено источником — есть прямая цитата/цифра/документ по ссылке |
| **[О]** | Моя оценка на основе источников и нашего стенда, в источнике этого нет |
| **[?]** | Неизвестно / не подтверждено, требует собственной проверки |

**Важно:** это отчёт о том, что *сообщество и авторы инструментов* заявляют/измеряют.
Ничего из перечисленного мы ещё не гоняли на нашем стенде. Все замеры из нашей прошлой работы
(поры 3–12 px, потолок still 9.2 / видео 8.0 / H3 7.4, деталь-пасс в видео = 0, SeedVR2 = −0.5 кожи)
здесь **не переоткрываются** — они входят как ограничения, на фоне которых оцениваются новые варианты.

---

## 0. Главный вывод (TL;DR)

**[О]** Мы нашли не «ещё один LoRA для пор», а **три класса решений, которых у нас ещё нет**:

1. **Детализация ВНУТРИ видео-модели, а не пост-пассом.** `ComfyUI-Tessera` (hashu786) —
   тайловый рефайнер на H3 с per-tile conditioning, фазовым «освобождением» гайда и
   оптимизированной сигма-схемой. Заявлено **+67% tile-local detail** при near-baseline shimmer.
   Это прямо отвечает на наш провал: деталь-пасс даёт +169% temporal HF-дельта (кипение),
   потому что он работает **покадрово вне модели**. Tessera работает *внутри* одной
   temporal-диффузии, поэтому temporal-consistency не разрушается.
2. **TST (Temporal State Transport)** — `comfyui-SelfLift` (facok): патч `MODEL→MODEL`,
   который на H3 выправляет temporal attention. Это ближайший аналог «стабилизации через
   оптический поток», но **на уровне attention, а не на уровне пикселей** — то есть деталь
   не «переносится», а сам attention начинает вести себя когерентно.
3. **H3-специфичный face-рефайнер с per-frame denoise** — `ComfyUI-H3-FaceRefine` (Carasibana,
   560 ⭐) + `H3 Per-Frame Denoise`: денойз зависит от размера лица *в пикселях кадра*.

**[О]** Отдельный важный вывод: **пластичность H3 частично — это баг референсного режима, а не
потолок модели.** Community (несколько независимых веток) утверждает, что **ref2va** ломает
текстуру кожи, а **fl2va** — нет, причём разница видна даже без смены промпта.

---

## 1. Кожа: конкретные модели / LoRA / ноды с параметрами и порядком применения

### 1.1. Порядок, который community считает рабочим (H3)

**[И]** Паттерн «低 res draft → upscale → refine» описан явно у hashu786 (Tessera), и там же —
почему он работает: **plate (低-res upscale) используется как in-band guide, и деталь приходит
из фазового освобождения гайда**, а не из «потёртой» текстуры гайда.

**[И]** Отдельно: сообщество H3 сходится на том, что **«turbo LoRA + 8 шагов» = пластик**,
а **50 шагов без turbo** = лучшая текстура.
- Reddit: «Opt for 50 steps instead of the standard 20, and maintain a significant distance
  from turbo loras, as they tend to give a very artificial appearance»
  — [r/StableDiffusion, "Super realistic human with H3???"](https://www.reddit.com/r/StableDiffusion/comments/1w4bxn8/super_realistic_human_with_h3/)
- Reddit: «20 steps is the bare minimum. see how it looks at 50» —
  [r/StableDiffusion, "Minimax H3: how to deal with plastic skin when generating with ref2va?"](https://www.reddit.com/r/StableDiffusion/comments/1vygmwv/minimax_h3_how_to_deal_with_plastic_skin_when/)
- Reddit: «I use the fl2va model on any ref2va workflow… better sharpness and skin details,
  even identity is preserved» + «use fl2va loras instead of ref2va» — там же.
- Reddit: «Ref2va has a bug. They are working on it.» — там же.

**[И]** Шаг-каунт community-консенсус (отдельно от turbo): 25 — минимум, 30 — «noticeably better»,
50 — дефолт у тех, кому важно качество.

**[О]** Практический вывод для нас: наш текущий H3-поток, вероятно, работает на turbo-LoRA
(у нас 227 H3 LoRA, включая «A Little More Sharpness R64», «Add Micro Details»).
**Первым делом надо проверить чистый H3 (без turbo) на 30–50 шагах** — это стоит 0 GPU-часов
на настройку и может дать больше, чем любой пост-пасс. Это не «открытие» — это проверка
нашего собственного стенда, но эффект в community описывается как очень заметный.

### 1.2. Realism People LoRA (у нас уже стоит — нужны правильные параметры)

**[И]** `fal/MiniMax-H3-Realism-People-LoRA`:
- файл `h3-realism-people-t2v-i2v-r2v.safetensors`, триггер `r34l1sm`,
- **сила 1.0 — рекомендуемая; 0.6–0.8 для лёгкого касания**,
- официально работает на t2v / i2v **и r2v** (LoRA трогает shared attention projections),
- автор показывает 19 пар «base vs adapter, same prompt, same seed» — с триггером в обоих,
  то есть работает именно адаптер, а не слово.
- Источник: [huggingface.co/fal/MiniMax-H3-Realism-People-LoRA](https://huggingface.co/fal/MiniMax-H3-Realism-People-LoRA)
  (зеркала: `Stankyspence1/…`, `taurusduan/…`, `Alex995647/loras-minimax-h3` — 63.5 ГБ / 110 LoRA)

**[И]** Наш замер: r34l1sm 0.65 даёт ещё +12.6% поверх FreeVideo high. **[?]** Не проверено,
что будет при силе 1.0 (официальный рекомендованный) — это надо замерить.

### 1.3. [MMH3] Realism Slider v1.0 (у нас уже стоит — неверные параметры)

**[И]** CivArchive: «designed to improve skin realism and **reduce the overly oily, shiny
appearance**… preserving fine details such as pores, wrinkles, freckles».
**«Use only with positive values from 0.25 to 2. Usually 1~1.5 are the best values on my setup.»**
Автор отдельно рекомендует **тестировать без turbo LoRA**.
Источник: [civarchive.com/models/3014078?modelVersionId=3421124](https://civarchive.com/models/3014078?modelVersionId=3421124)

**[О]** У нас этот LoRA стоит, но неизвестно, при какой силе и в каком сочетании. Рекомендую
пробег: r34l1sm × (Realism Slider 1.0–1.5) на H3 без turbo, 30–50 шагов, fl2va.

### 1.4. Polyhedron Perfect Eyes // Perfect Skin // Perfect Hands (H3-специфичный)

**[И]** Civitai, 3 980 скачиваний, рейтинг «Very Positive (143)», 3000 шагов / 10 эпох,
bf16, 295.8 МБ, триггеры: `perfe8ct` `perfect hands` `perfect skin` `perfect eyes`.
**[И]** Автор прямо предупреждает: *«It's the final LoRA model and might be already overtrained
so use it with lower weights»*, и *«they do have an effect, but the whole thing is very
prompt-sensitive»*.
Источник: [civitai.com/models/2899220](https://civitai.com/models/2899220/minimax-h3-t2va-t2v-polyhedron-perfect-eyes-perfect-skin-perfect-hands)

**[О]** У нас в списке H3 LoRA нет `Polyhedron Perfect Eyes` (есть `Polyhedron Perfect Eyes` —
да, в задании указано «Polyhedron Perfect Eyes» есть). **Тогда**: он у нас есть, но, вероятно,
не проверен на H3 в наших замерах. Особенность именно для нас: этот LoRA бьёт по **глазам + коже +
рукам** одновременно — то есть покрывает нашу задачу «детали лица» из п.4.

**[О]** Дополнительно: автор рекомендует в связке **Photorealistic Image Generator LoRA**
(`civitai.com/models/2884206`) — он у нас есть.

### 1.5. LMS Sharpness/detail LoRA (Alissonerdx) — ключевой для Tessera

**[И]** `Alissonerdx/Minimax-H3-ComfyUI` → `minimax_h3_lms_v1.0_r64`:
«Sharpness/detail enhancement **using an aligned source-video guide**», rank 64,
триггер — «Fixed enhancement caption» (не свободный текст), workflow `minimax_h3_lms_workflow.json`.
Источник: [huggingface.co/Alissonerdx/Minimax-H3-ComfyUI](https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI)

**[И]** Этот же LoRA — часть «house recipe» Tessera (см. 1.6).
**[И]** Open-H3-Regenerate-2K тоже использует **merged LMS Sharp** в LoRA-конкатенации.

**[О]** В нашем стенде его нет — надо поставить. Обратите внимание: он требует *выровненного
source-video guide*, то есть работает в связке с AddGuide по исходному видео, а не в одиночку.

### 1.6. ★ ГЛАВНОЕ: ComfyUI-Tessera (hashu786) — тайловый H3-рефайнер

**[И]** Репозиторий: [github.com/hashu786/comfyui-tessera](https://github.com/hashu786/comfyui-tessera)
(создан 2026-10-30, MIT, 9 нод). Описание: «Production video upscaling for ComfyUI, built on
MiniMax H3 — **544p to 4K/8K on one consumer GPU**». Ключевая документация:
[docs/LAWS.md](https://github.com/hashu786/comfyui-tessera/blob/main/docs/LAWS.md) — «Everything here
was established by measurement on real renders; failed hypotheses are recorded next to the ones
that survived».

Состав:
- `Tessera Streamed Encode` — source → upscaled plate latent + guide conditioning, потоково по
  временным окнам; заменяет цепочку upscale/encode, которая OOM-ится на 4x.
- `Tessera Refine` — тайловый рефайнер: per-tile conditioning, temporal chunking, hero tiles,
  anti-hallucination tile skip.
- `Tessera Streamed Decode` — отдельный потоковый декодер + путь восстановления.
- `Tessera Tile Plan` — dry run: сетка тайлов, layout чанков, стоимость — **до** траты GPU-часов.
- `Tessera Guide Anchor` — вклеивает детальный still в fine-band гайда (shift-corrected,
  flow-propagated).
- `Tessera Temporal Dilate / Decimate`, `Tessera Guide Grain`, `Tessera Extra Options`.

**Валидированный рецепт v3 (дословно из LAWS.md):**
```
border_mode     overlap          seam_regen      32
seam_direction  forward          seed_mode       global
context_border  64               ref_crop        per_tile
image_ref_crop  per_tile         guide_noise     0
guide_release   0.8              frame_match     dc
motion_adapt    on               motion_release_floor 0.0
fine_below_sigma 0.75            sampler_fine    euler
sigmas          1.0, 0.7, 0.4, 0.0   (1 turbo head + 2 euler tail)
dc_match        off              pixel_color_match off
clip            wired, prompt = the ref node's prompt
conditioning    video ref + image ref + guide, CLEAN (no added noise anywhere)
```

**[И]** Заявленный измеренный результат: «**+67% tile-local detail** over the frozen-border
baseline at near-baseline shimmer, run-2-class full-frame detail (129.6 vs 130) with the best
end-of-clip persistence ever recorded (103.6 vs 91), a dead chunk boundary, and thin-edge-only
diffs against the faithful baseline».

**[И]** Ключевой механизм, ради которого это стоит делать:
- «**Guide release is phase-split, not global.** The in-band guide is a spectrum clamp: the model
  matches its softness, **vetoing detail LoRAs**. Corrupting the guide for ALL steps… re-decides
  content… per-frame corruption shimmers while persistent corruption reads as CONTENT and ghosts».
  То есть: деталь берётся не из «испорченного» гайда, а из **фазы хвоста** — голова решает
  структуру под полными доказательствами, хвост текстурирует под свободой.
- «**The tail needs capacity, not just freedom.** One released step saturates (release 0.5 -> 0.8
  moved detail <1%)… **Two tail steps let texture be BORN then ORGANIZED (+67%). Three overfeed:
  +7% energy that reads as hardness, with +17% shimmer. The capacity curve has a peak at two.**»
- «**Euler for the tail, never a multistep sampler.** LMS extrapolates the previous step's
  derivative — in a released tail that direction contains fresh per-frame texture, and the
  extrapolation commits it as grain (measured: switching the identical schedule to euler gave
  **+56% detail AND cut shimmer by a third**). **Momentum and freedom don't mix.**»
- «**More head = more obedience.** A second turbo head step… the tail loses raw material
  (**-21% detail**). One head step is correct.» — и это же safety-правило: distilled turbo,
  пройденный дважды через mid-sigmas, выдал NaN в latent.

**[И]** Стабильность:
- «**frame_match is the stability layer — and it must be TRANSIENT-only.** One global DC offset
  per channel per frame… pins the exposure arc and kills the chunk-entry transient with zero
  spatial structure.» Ошибка (якорь полного оффсета к plate) дала «**319 → 87 tile-detail
  collapse**», починено вычитанием time-mean оффсета.
- «**Source shimmer is inherited, not created.** A low-res source whose own fine band churns
  (measured 0.28-0.62 frame-to-frame on a 1792px gen) upscales into magnified swimming: the refine
  LIFTS coherence toward its ~0.75 ceiling (0.40 -> 0.75 measured) but **never past it**, and
  neither release, schedule, sampling-noise coherence, nor pre-stabilizing the plate moves the
  ceiling.»

**[И]** v4 «detail relay»: `TesseraAnchor` вклеивает детальный still в fine-band гайда; `spread`
(r68) переносит fine band на ±N кадров через **two-level block flow** (1/8-res wide match + 1/2-res
refinement), с пере-anchor'ом к кадру-источнику на каждом шаге и confidence-gating.
**[И]** Спот-anchor law: «only frame 0 relays detail forward on its own; a mid-clip 1-frame splice
is a ±2 frame blip — VAE packing averages it away».

**[И]** Настройки anchor'а из README: «feed a detailed still at 2x for real detail relay; at 4x and
above leave the anchor unplugged and let the model render. When anchoring, set
`motion_release_floor` to 0.3.»

**[И]** Требования: MiniMax H3 **ref2va** + video/audio VAEs + Qwen3-VL text encoder;
**LMS detail LoRA (Alissonerdx)** — «part of the house recipe»; опционально Qwen Image 2.1
(first-frame detail anchor still) и SAM3 (hero-subject mask).

**[И]** Статус (из LAWS.md): «Overlap mode + per-tile text encode validated on production clips
(seam-free at 2× and 4K second hops). **Temporal chunking under overlap mode uses the latent
crossfade write-back; first long-clip validation still pending.**»

**[О]** Почему это прямо отвечает на наш провал с деталь-пассом: наш деталь-пасс вносил деталь
**после** декодирования, покадрово → +169% temporal HF-дельта. Tessera вносит деталь **внутри
одной temporal-диффузии**, где temporal attention H3 сам обеспечивает связность между кадрами.
Плюс `guide_release` фазовый, а не глобальный — значит «кипение» от глобального зашумления
принципиально не возникает.
**[?] Но**: автор прямо признаёт, что «first long-clip validation still pending» для temporal
chunking — то есть на длинных клипах (121 кадр) стабильность подтверждена слабо.
**[О]** Практический путь: сначала 2× на 24–49 кадрах, замер pore-spectrum + temporal HF-дельта
(наши существующие пробы `scripts/probe_pore_spectrum.py` подходят) → потом длинные клипы.

**[?] VRAM**: README заявляет «544p to 4K/8K on one consumer GPU», но **конкретной цифры VRAM нет**;
`MiniMax-AI/awesome-minimax-h3-integration` для Tessera прямо пишет «a general minimum-memory
configuration is not established». **[О]** На 16 ГБ начинать с 2× и коротких клипов.

### 1.7. ★ ComfyUI-SelfLift (facok) — прогрессивный сэмплер + TST

**[И]** [github.com/facok/comfyui-SelfLift](https://github.com/facok/comfyui-SelfLift) (367 ⭐,
создан 2026-09-07).

**H3 Temporal State Transport (TST)** — патч `MODEL→MODEL`:
- **[И]** «improves the temporal stability of H3 videos at inference time — no training, no extra
  model, **~1–2% runtime overhead**».
- **[И]** «Typical problems it counteracts: details that flicker or morph between frames
  (logos, on-screen text, textures), identity drift of people and outfits».
- **[И]** Параметр `tau` = 0.2 рекомендуется; **0.5 — visibly too strong**; 0 отключает
  коррекцию, но оставляет диагностику.
- **[И]** Механика: «instead of blindly strengthening cross-frame attention, TST measures a signed
  «Spectral Tension» of the frame-level attention operator and corrects only imbalanced heads —
  sharpening over-mixed ones, softening fragmented ones — with stronger correction in deeper layers
  and earlier steps».
- **[И]** Теория/код: arXiv 2609.08505 (Temporal State Transport in Video Generation),
  [github.com/lytang63/temporal-state-transport](https://github.com/lytang63/temporal-state-transport).
- **[И]** Ограничения: «**MiniMax H3 models only** (other models are skipped silently);
  **not compatible with `highres_tiling`**».
- **[И]** «**It cannot create detail the model does not have.**» ← ключевое: TST не добавит пор,
  он сделает существующую текстуру стабильной.

**SelfLift Progressive Sampler (MiniMax H3)**:
- **[И]** Два режима: external upscaler (H3-чекпойнт, `rho=0`) или SelfLift-zero
  (`upscaler_model=none`, `rho>0`). Suggested H3 starting point: `rho=0.6`, `w_min=w_max=1`.
- **[И]** Внешний апскейлер: `LBH-123-AI/Minimax_h3_latent_Upscaler` →
  `ComfyUI/models/latent_upscale_models/`.
- **[И]** Диагностика H3 (таблица): nearest route даёт «widespread artifacts»; `rho=0.3` (paper
  settings) «mostly left visible»; `rho=0.6` с `w_min=w_max=1` «removed the main artifacts».
- **[И]** Важно: «**H3's 768-pixel short edge becomes 384 px at `lowres_scale=0.5`, which may sit
  outside the backbone's training distribution** — validate per model».
- **[И]** Только Euler с `s_churn=0`.

**[И]** Форки с H3-спецификой: `rogen84/SelfLiftH3Sampler`, `slmonker/selflift-Avatar`;
Reddit-пост «selflift-Fish: Longer H3 videos & higher resolutions on a **16GB GPU** — 20s clips /
1920×1088 tested» ([r/comfyui](https://www.reddit.com/r/comfyui/comments/1x9sgr1/selfliftfish_longer_h3_videos_higher_resolutions/)),
`ComfyUI-CoachBate` (H3 Per-Subject Denoise: «`denoise` 0.35-0.55 adds detail without changing
the shot; higher rebuilds more»).

**[О]** TST — самый дешёвый эксперимент в этом отчёте: одна нода, +1–2% времени, `tau=0.2`.
Если наш H3-клип «кипит» деталью, TST может срезать HF-дельту без потери пор. Но **он не
добавляет** деталь — поэтому его надо комбинировать либо с clean-генерацией на 50 шагов, либо
с Tessera.

### 1.8. Open-H3-Regenerate-2K (NVlabs) — НЕ для нашего стенда, но эталон

**[И]** [huggingface.co/Efficient-Large-Model/Open-H3-Regenerate-2K](https://huggingface.co/Efficient-Large-Model/Open-H3-Regenerate-2K)
— открытая замена закрытому H3-Regenerate-2K API: трёхшаговый H3-рефайн, перегенерирует
768p драфт (1344×768, 121 кадр) в 2560×1440.
- **[И]** Механика: latent upscaler (3D-conv) → 3 refine-шага (sigmas 0.909375, 0.857, 0.667),
  CFG off, один **merged LoRA** (refiner + LightX2V Turbo + LMS Sharp, exact rank concatenation)
  на замороженном H3-трансформере.
- **[И]** **[О]** ⚠️ **«One GPU needs about 150 GB for the SoL-Engine forwards with CUDA graphs
  (B200 / GB200)»** — нам не подходит. Официальные тайминги (B200): standard 73.11 s,
  sol_engine 40.61 s, sol_engine_fast 20.46 s (upscale + refine 5-секундного клипа).
- **[И]** «`sol_engine_fwd` is lossy (sparse attention, MXFP8); the timing table's quality was
  checked by eye… **Same-seed PSNR is not a usable metric for this model**».
- **[И]** Важно: «the 3-step output is visibly **softer on fine texture** (fur, moss)** than 3 steps»
  — автор прямо отмечает, что 2-шаговая схема мягче по мелкой текстуре. Для нас (поры) это
  прямой сигнал: **2 шага рефайна = потеря микротекстуры**.

**[И]** Что всё-таки можно взять: **сам merged-LoRA подход** и
`Kijai/MiniMax-H3-experimental` → `loras/minimax_h3_open_regenerate_2k_refiner_rank_16_bf16.safetensors`
(«The refiner part ONLY, doesn't include the LMS or the lightx2v turbo»).
**[И]** Reddit-видео-гайд: «ComfyUI Tutorial: PIKA H3 + MiniMax H3 Singularity V2 + NVIDIA
Regenerative 2K Upscaling KIJAI LORA (With 6GB of VRAM)» —
[r/comfyui](https://www.reddit.com/r/comfyui/comments/1xmrcyl/comfyui_tutorial_pika_h3_minimax_h3_singularity/)
— автор утверждает, что гоняет это на **6 ГБ VRAM** с Ultimate MiniMax H3 Upscale node и
пониженным весом Kijai LoRA. **[?]** Не проверено, сколько VRAM и сколько шагов.

### 1.9. Krea 2 skin LoRA — эталон для still-части нашей цепочки

**[И]** `inlineresearch/skin-lora-krea-2-raw`: photorealistic skin texture LoRA для Krea 2 RAW,
`instance_prompt: inline-skin-lora`, эффекты: «visible pores, fine vellus hair, natural subsurface
scattering, and honest imperfection». Рекомендованные сэмплы включают «extreme macro beauty
close-up… visible pores and vellus peach fuzz».
**[И]** `houseofboern/skintexture-krea2-v3-ckpts`: «**an always-on style LoRA — there is NO trigger
word.** Its only job is to put real skin texture (pores, unevenness, real specular sheen) into
Krea 2 output».
**[И]** `jay1000/krea2-loraholic-skin-details`: «Strength — Author range: **0.5 to 3.5** on Krea 2».

**[О]** Это still-модели. Применимо к нашей цепочке как «эталон детализации лица/кожи» — если мы
строим still-anchor для Tessera (`Tessera Guide Anchor` вставляет детальный still!), то Krea 2
skin LoRA — естественный источник этого still. Но у нас нет Krea 2 на стенде; есть FLUX.1-dev
и Qwen-Image-Edit.
**[?] Перенос Krea-2-скиновых LoRA на FLUX.1-dev/Qwen — не подтверждено.**

### 1.10. Модели для остальных баз (справочно, из-за отсутствия у нас)

**[И]** `Alex995647/loras-minimax-h3` — зеркало **110 H3 LoRA (63.5 ГБ)** с Civitai + HF.
Содержит «H3 Realism People», «Minimax H3 Authentic cinematic texture», «H3 → MiniMax-H3-Realism-People-LoRA».
**[И]** CivArchive: «Minimax H3 Authentic cinematic texture v1.0» — «The recommended model strength
is **0.85**»; «Cinematic Style LoRA + Detail Enhancer for MiniMax H3 - v2.0».
**[И]** FLUX-семейство: `Polyhedron_all // Perfect skin. Perfect hands. Perfect eyes.` для FLUX.1 D
(trigger: `Perfect Eyes Detailed skin Skin blemish`) — [civitai.com/models/654412](https://civitai.com/models/654412/flux-polyhedronall-perfect-skin-perfect-hands-perfect-eyes-mf),
Very Positive (477).
**[И]** Wan 2.2: `Polyhedron_all` для WAN2.2 T2I A14B — [civitai.com/models/1947995](https://civitai.com/models/1947995/wan22-t2i-highlow-noise-polyhedronall-perfect-skin-perfect-hands-perfect-eyes-mf).

---

## 2. Temporal consistency: оптический поток / multi-frame / propagate-from-reference

### 2.1. Что реально есть на рынке (готовые реализации)

| Инструмент | Что делает | Где | Статус для нас |
|---|---|---|---|
| **ComfyUI-Tessera** | Per-tile conditioning + фазовый guide release + temporal chunking; деталь рождается **внутри** temporal-диффузии | github.com/hashu786/comfyui-tessera | **[О]** приоритет №1, H3-native, consumer GPU |
| **comfyui-SelfLift → TST** | Патч attention на H3; «Spectral Tension» коррекция | github.com/facok/comfyui-SelfLift | **[О]** приоритет №2, дешевле всего |
| **ComfyUI-H3-FaceRefine → H3 Per-Frame Denoise** | Per-frame denoise через `noise_mask`, сила зависит от размера лица в пикселях | github.com/Carasibana/ComfyUI-H3-FaceRefine | **[О]** приоритет №3 для лиц |
| **Temporal Smooth (flow blend)** | Flow-guided EMA: Farneback / RAFT-small / RAFT-large, per-pixel confidence, bidirectional | comfy.icu/node/TemporalSmooth (пак ComfyUI-Temporal-Face-Detailer) | **[О]** анти-фликер, не детализация |
| **Tracked Face Detail (SDXL)** | Per-track fixed seed + noise + flow-guided temporal blending (pixel и/или latent) | comfy.icu/node/TemporalFaceDetailer | **[?]** SDXL-only, не H3 |
| **ProPainter** (raft/lama/frame-interpolation nodes) | Flow-based propagation + spatio-temporal transformer для видео-инпейнтинга | github.com/daniabib/ComfyUI_ProPainter_Nodes и др. | **[О]** для инпейнтинга, не для детализации кожи |
| **Tessera Guide Anchor** (`spread`) | Two-level block flow (1/8-res wide match + 1/2-res refinement) переносит fine band гайда на ±N кадров | github.com/hashu786/comfyui-tessera | **[О]** propagate-from-reference, уже реализовано |

### 2.2. Temporal Smooth — конкретные параметры (полезно как анти-фликер)

**[И]** [comfy.icu/node/TemporalSmooth](https://comfy.icu/node/TemporalSmooth):
- `strength` 0.5 (default) — сколько warped running result каждая кадр впитывает; 0 = passthrough.
- `bidirectional` true (default) — форвард+бэквард, усредняются.
- `flow_backend` — `farneback` (default, лёгкий, CPU) / `raft_small` / `raft_large`
  (torchvision RAFT, чище на быстром движении, RAFT-входы капятся на 512 px, веса грузятся лениво
  и освобождаются; при любой ошибке — автоматический откат на Farneback с предупреждением,
  «It can never hard-fail your render»).
- **[И]** Механика: «computes dense optical flow between adjacent frames, warps the running result
  along the motion, and **only blends where the flow is trustworthy**» — confidence падает на
  окклюзиях, поэтому нет ghost-шлейфов.
- **[И]** Место в графе: «between any upscaler or AnimateDiff output and Video Combine».
- **[И]** Грабля (дословно): «**Real motion gets soft.** If your video has fast pans or …»
  — движение мылится. **[О]** Значит, strength > 0.5 на наших клипах с быстрыми панелями
  съест остаток микротекстуры, которую мы так бережно получили.

### 2.3. Что community говорит про «стабилизацию детализации через поток»

**[И]** Формулировка, которую community считает корректной (Reddit, «Help Fixing the H3 Face
Detailer»): «**anything that detects and repaints the face frame by frame is solving each frame
on its own, so the mask edge and the repaint land slightly differently every time. That reads as
jitter no matter how well you tune dilation and feather. Temporal consistency is the piece that's
missing, not the mask settings.**»
И продолжение: «lock the face in the first frame instead and let i2v carry it… because the model
is propagating from a frame that already has the right face rather than correcting it after the fact.»
— [r/StableDiffusion](https://www.reddit.com/r/StableDiffusion/comments/1vsn8ge/help_fixing_the_h3_face_detailer/)

**[О]** Это ровно тот принцип, который мы уже нащупали эмпирически (наш деталь-пасс в видео = 0),
но community формулирует его прямо: **temporal-consistency должна быть В модели, а не в пост-пассе**.

**[И]** Иерархия от ComfyUI-Agent-Kit (сводный reference, обновлён 2026-06-24):
«Use a native video model (it enforces frame coherence **inside the model**, not as a post-pass)…
**Finishers, not fixers:** RIFE / FILM interpolation smooths judder but can hide flicker and ghost
on fast motion; **SuperBeasts Deflicker / PixelDeflicker fixes luminance shimmer only (it is
temporal blur and will smear fine detail)**. Use lightly.»
И: «**What still flickers (say so):** fine texture and identity (hair, fabric weave, foliage) that
depth/pose do not constrain…»
— [github.com/SlavaSexton/ComfyUI-Agent-Kit/blob/master/docs/ADVANCED.md](https://github.com/SlavaSexton/ComfyUI-Agent-Kit/blob/master/docs/ADVANCED.md)

**[И]** Там же, ключевая формулировка нашего главного ограничения:
«**The core tension: per-frame max detail flickers; cross-frame stability blurs detail.** Resolve
it three ways: (a) a sequence-native upscaler that does both (**SeedVR2**, batch >= 5,
`temporal_overlap`); (b) lock structure (low denoise + ControlNet Tile) and vary only fine detail;
(c) detail-pass then a light deflicker.»

**[О]** Наш SeedVR2 уже отвергнут (−0.5 кожи). Вариант (c) — ровно то, что мы делали и что
провалилось. Вариант (b) — **ControlNet Tile для структуры + низкий denoise** — это то, чего
мы **не пробовали** (у нас нет ControlNet Tile для H3; надо проверить, есть ли H3 ControlNet).
Вариант (a) — SeedVR2 — отвергнут. **Tessera — это по сути новый ответ, объединяющий (b) с
генеративным рефайном, и он единственный из трёх, который community измерил на видео.**

### 2.4. Прямое «propagate from reference» для кожи

**[И]** Tessera Guide Anchor — ближайшая реализация: «splice a DETAILED still into the guide clip
at any frame… Default `texture` mode keeps the **PLATE's low band** (geometry, lighting —
pop-proof by construction) and transplants only the anchor's fine band after auto shift-correction,
so the still does NOT need to be pixel-aligned».
**[И]** `spread` (r68): fine band переносится наружу на ±N кадров через two-level block flow;
confidence-gated; «full strength across the VAE packing group (±2f), linear decay beyond».
**[И]** Ограничение: «only frame 0 relays detail forward on its own; a mid-clip 1-frame splice is a
±2 frame blip».

**[О]** Прямой ответ на нашу задачу «передача детали между кадрами вместо независимой генерации
на кадр»: **реализовано, но только для fine-band гайда и только в Tessera**.

**[И]** Отдельно — Propainter/ProPainterInpaint (RAFT flow + transformer) — но это video-inpainting
(заполнение дыр), не перенос детали кожи. **[О]** Прямое применение к нашей задаче — сомнительно.

**[I]** `OpenSourceFilms/opensourcefilms-comfyui-workflows` содержит собственный пакет
`custom_nodes/mask_propagate_optical_flow/` — **[?] не подтверждено**, что он решает именно
задачу детализации (по описанию — распространение масок по потоку).

---

## 3. Лица в видео: глаза / зубы / волосы без пластика

### 3.1. ★ ComfyUI-H3-FaceRefine (Carasibana, 560 ⭐) — H3-native face refinement

**[И]** [github.com/Carasibana/ComfyUI-H3-FaceRefine](https://github.com/Carasibana/ComfyUI-H3-FaceRefine),
MIT, v1.1.2 (2026-09-13), 7 нод.

Проблема, которую он решает (дословно):
«**MiniMax H3 renders faces poorly when the head occupies a small fraction of the frame. This is a
property of head-size-in-frame, not of output resolution, so it persists at 720p and above.**»
Моделировано на Impact Pack FaceDetailer, адаптировано со still на видео:
«detect the face on every frame, crop to it so it fills a canvas, let H3 re-generate it, and
composite the result back into the original video».

**Ключевой момент, который нас интересует больше всего:**
«**The reason it works is what H3 is handed. Instead of a distant head a few dozen pixels tall, it
gets the face tracked and normalized to fill the canvas.**»

**[И]** Canvas: `canvas_width`/`canvas_height` **по умолчанию 768** (нативный короткий край H3),
auto-режимы клампят минимум 512×512 — «on a small face in a low-resolution clip the canvas used
to track the crop down to a couple of hundred pixels — **handing H3 the same small face it renders
badly**».

**[И]** **H3 Per-Frame Denoise** (ключевая нода):
- **[И]** Механика: «The sampler builds one sigma schedule for a whole clip, so every frame normally
  gets the same denoise. ComfyUI's `noise_mask` is the escape hatch: it scales denoising per latent
  position, and varied along the temporal axis it gives per-frame strength out of one sampling pass.
  The node computes face height per frame from the transform (crop height ÷ `crop_factor`),
  interpolates between `strength_small_face` and `strength_large_face`, applies a `gamma` curve, and
  smooths the resulting strength over `smooth_frames` — because an abrupt denoise change between
  neighbouring frames shows up as a texture pop.»
- **[И]** Параметры: `strength_small_face` = 1.0, `strength_large_face` = **0.35**,
  `face_px_small` = 30, `face_px_large` = 120, `gamma` = 1.0, `smooth_frames` = 9,
  `scale_mode` = `absolute_px` (по умолчанию) или `relative_to_clip`.
- **[И]** Размещение: «goes **after** `MiniMaxH3NativeAudioLock`», и он намеренно сохраняет
  audio-side нули в noise mask.
- **[И]** Гранулярность: «one latent frame, which is roughly 3.4 pixel frames».
- **[И]** В 1.1.0 нода сменила тип: теперь `MODEL` → `MODEL` (в 1.0.0 брала latent), т.е. её
  надо ставить **в model path**, и её `model`-выход идёт в guider. Старые воркфлоу падают с
  `Required input is missing: model`.
- **[И]** Логика: «A tiny face has no detail worth preserving; it wants a strong pass so H3
  synthesizes a face where there was a smear. A large face has real, genuine detail, and a strong
  pass would rewrite it into something slightly wrong. One global denoise can't be both.»

**[И]** Прочие ноды: `H3 Load Video + Face Select` (находит все лица и все hard cuts одним
проходом, PySceneDetect `cut_threshold` 3.0 — «Cuts matter because a cut renumbers every face and
breaks continuity»); `H3 Face Track + Crop` (select из 9 режимов: `largest_face`, `smallest_face`,
`left_most`, `right_most`, `top_most`, `bottom_most`, `centre_most`, `closest_to_xy`,
`detector_score`; `identity_model`: insightface / clip_vision / ccip; `absent_shots`);
`H3 Face Stitch Back`; `H3 Face Mask (SAM)`; `H3 Inject Video Latent (img2img)`;
`H3 Face Transform Info`.

**[И]** Gotchas (важно для планирования на 16 ГБ):
- «**Cost is `canvas² × frames`.** Auto canvas sizing considers face size only, so on a long video
  it can pick a canvas that exceeds VRAM and falls back to streaming weights from system RAM, an
  **order-of-magnitude slowdown** rather than a clean error.»
- «Frame count is snapped up to H3's 17k+5 grid (5, 22, 39 … 175, 226, 362)».
- `SAMLoader` с `AUTO` оставляет модель на CPU до `prepare_device()`.

**[И]** Предупреждение автора о temporal-поведении (дословно):
«**These two are what to watch for temporal behaviour: the box has to sit still on a moving
subject, or the refined face boils.**»

**[I]** Отдельная ревизия 1.1.2: трекер больше не перепрыгивает на другого человека, когда
субъект потерян; такие кадры остаются нетронутыми (crop интерполируется, composite fades out).

**[О]** Для нас: это **не** «face_restore_visibility=0.7 из Impact Pack». Impact Pack восстанавливает
лицо отдельной моделью (GFPGAN/CodeFormer) → пластик. Здесь лицо **перегенерируется тем же H3**,
который и так умеет людей — просто ему дают кроп, где голова занимает кадр. Это принципиально
другой механизм, и он прямо нацелен на «маленькое лицо → каша», а не на «пластик».
**[?]** Порядок применения к нашей цепочке (после FreeVideo/после рефайна?) — не проверено.

### 3.2. Deroping / face refine на H3

**[И]** Reddit: «there are two well-regarded methods… **face refine and deroping**. I observed one
offurress's videos deroping can also improve f…» —
[r/StableDiffusion, "Face detailer or deroping to fix faces in MM H3?"](https://www.reddit.com/r/StableDiffusion/comments/1wfvfg0/face_detailer_or_deroping_to_fix_faces_in_mm_h3/)
**[?]** Термин «deroping» в этом контексте не определён — вероятно, отдельный инструмент
(«one offurress's videos» → возможно, "One Offuress"). Требует уточнения.

### 3.3. Практики 2025–2026, которые community считает рабочими

**[И]** Пайплайн «crop → refine → stitch» — базовая практика:
«The approach that yields the best results involves cropping, refining, and t…» —
[r/comfyui, "Most Optimal Face Refining Workflow for H3"](https://www.reddit.com/r/comfyui/comments/1xjgkwm/most_optimal_face_refining_workflow_for_h3/)
**[И]** Там же: «distant faces in H3 are mostly a **pixel budget problem**. At 704x1248 a face in a
wide shot is maybe 40 px tall, there just isn't enough there to hold the identity.»
**[И]** И там же (альтернативный путь): «**the direct full generate path is the way to go, push it
to 2k or higher, then rescale back down as needed.** Can easily generate a 4k image, scale it down,
then animate it, for things such as people walking in the distance».

**[И]** «OK this is cool, Minimax H3 Face detailer!» — 455 upvotes, 142 комментария —
[r/StableDiffusion](https://www.reddit.com/r/StableDiffusion/comments/1vn4uho/ok_this_is_cool_minimax_h3_face_detailer/)

**[I]** Важная практика, которую мы НЕ применяли: **держать лицо «в кадре», а не «в зоне»** —
т.е. не upscale'ить лицо отдельно post-фактум, а генерировать так, чтобы голова занимала
достаточно кадров ещё на генерации. Это дешёвый и надёжный путь.

**[И]** BF16 vs INT8: «BF16 models while bigger, larger and slower, are **superior in terms of
motion, face, and image quality**» — «The minimax H3 Hybrid BF16 model, even pruned, is over 40GB»,
«face refinement is still necessary and worthwhile as demonstrated» —
[r/StableDiffusion](https://www.reddit.com/r/StableDiffusion/comments/1xher16/minimax_h3_bf16_and_face_refinement/)
**[О]** 40 ГБ на 16 ГБ не влезает. Только если ComfyUI умеет offload — но тогда надо мерить.

**[I]** «Minimax H3 BF16 and face refinement» комментарий: «**Reference test — Authentic skin
texture, display pores.**» — [тот же тред](https://www.reddit.com/r/StableDiffusion/comments/1xher16/minimax_h3_bf16_and_face_refinement/)
**[O]** Прямое подтверждение нашего target'а от community: «authentic skin texture, display pores».

### 3.4. Глаза / зубы / волосы — отдельных подтверждённых решений для H3 не найдено

**[И]** Единственное, что нашлось конкретное по глазам: **Polyhedron Perfect Eyes** для H3
(см. 1.4) — триггер `perfect eyes`, LoRA на 3000 шагов, Very Positive (143). Тот же автор
отдельно говорит, что его LoRA улучшает «skin texture, the eyes and the hands».
**[I]** По зубам: ничего специфичного для H3 не найдено. Общая практика от ComfyUI-Agent-Kit:
«Faces are the thing viewers notice first and forgive least, and diffusion models love to mangle
them — smeared eyes, **mushy teeth**, that slightly-melted look, especially on smaller or
further-away faces» — [comfy.icu/node/Load-Face-Enhance-Model-mtb](https://comfy.icu/node/Load-Face-Enhance-Model-mtb).
**[I]** Load Face Enhance Model (mtb) грузит face restoration модели (CodeFormer, GFPGAN,
RestoreFormer) — но это тот же класс, что мы уже отвергли (пластик).
**[I]** По волосам: отдельного temporal-safe hair-strand LoRA для H3 не найдено. В Matting-разделе
Agent-Kit: «Per-frame matting of a sequence flickers unless the model is temporal» — для волос
релевантен **MatAnyone2** (temporal alpha, исследовательская лицензия NTU S-Lab 1.0).
**[O]** Практика для волос: MatAnyone2/SAM3 keyframe → temporal matte → композит. Это про альфу,
а не про текстуру волос.

---

## 4. ★ «Что НЕ работает» — грабли (самый ценный раздел)

### 4.1. Подтверждено источником

**[И] A. Per-frame деталь-пасс = кипение.** «**anything that detects and repaints the face frame by
frame is solving each frame on its own… That reads as jitter no matter how well you tune dilation
and feather. Temporal consistency is the piece that's missing, not the mask settings.**»
— [r/StableDiffusion, "Help Fixing the H3 Face Detailer"](https://www.reddit.com/r/StableDiffusion/comments/1vsn8ge/help_fixing_the_h3_face_detailer/)
(это совпадает с нашим измерением: +169% temporal HF-дельта)

**[И] B. Deflicker/сглаживание = размазывание мелкой детали.** «**SuperBeasts Deflicker /
PixelDeflicker fixes luminance shimmer only (it is temporal blur and will smear fine detail). Use
lightly.**» — [Agent-Kit ADVANCED.md](https://github.com/SlavaSexton/ComfyUI-Agent-Kit/blob/master/docs/ADVANCED.md)

**[И] C. Ref2VA ломает текстуру кожи (баг/особенность режима).**
- «**Ref2va has a bug. They are working on it.**»
- «I just use the fl2va model on any ref2va workflow… better sharpness and skin details, even
  identity is preserved» → «use fl2va loras instead of ref2va».
- «The quality of the ref2va is quite bad compared to the flva2va… it works ok, better than
  ref2va sharpness, but you do loose a bit of the character details» (про hybrid-модель
  `smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models`, файл
  `minimax_h3_hybrid_fl2va_ref2va_b20-49-int8.safetensors`).
- **Мы используем H3 для I2V/R2V. Если текстура теряется именно в ref2va — часть нашей «потолка
  7.4/10» может быть не свойством H3, а свойством режима.** Это надо проверить.
— [r/StableDiffusion](https://www.reddit.com/r/StableDiffusion/comments/1vygmwv/minimax_h3_how_to_deal_with_plastic_skin_when/)

**[И] D. Turbo LoRA = искусственная кожа.** «**maintain a significant distance from turbo loras,
as they tend to give a very artificial appearance**» —
[r/StableDiffusion](https://www.reddit.com/r/StableDiffusion/comments/1w4bxn8/super_realistic_human_with_h3/);
«turbo loras… **oily or plastic look from acceleration LoRAs**» —
[r/comfyui](https://www.reddit.com/r/comfyui/comments/1w4x4sp/minimax_h3_just_got_much_better_cleaner_video/)

**[I] E. RefMod (packed reference frames) «deep fry» качество.**
Заголовок треда: «**PSA for non-turbo users: New RefMod likeness «breakthrough» is deep frying your
H3 videos**». Комментарии: «it **oversharpens everything** and most of the style info from the
prompt gets overpowered by ref image data»; «**at small resolutions (like .245 mpx)** you could
still make out faces instead of them being mushy, this should've been my first red flag»;
«throwing too many images at the model starts degrading quality» (автор RefMod, acedelgado).
— [r/StableDiffusion](https://www.reddit.com/r/StableDiffusion/comments/1wx5okm/psa_for_nonturbo_users_new_refmod_likeness/)

**[I] F. H3 FaceRefine: reference-картинка может игнорироваться.** «The Original Face Detailer… **does
not use the reference image at all. You can disable the input image and you will get exactly the
same result.**» (и починено пересборкой графа).
Там же — рабочие условия: «**the model must be the same, the clip must be the exact same prompt,
the reference image must be the same and the seed must be the same** as the generated video that
needs the face-fixing. It's also best to use the same step count. So in the end, it doesn't make
sense to have the face detailer as a separate workflow, **it should be directly chained into the
ref2va workflow**».
**[O]** → Прямое указание: наш H3-пайплайн должен быть **единым графом** (генерация + face refine
в одном прогоне), а не «сгенерировали → потом отдельно перегенерировали лицо».
— [r/StableDiffusion](https://www.reddit.com/r/StableDiffusion/comments/1vsn8ge/help_fixing_the_h3_face_detailer/)

**[I] G. «Face detailer прыгает и варпит» — не чинится dilation/feather.** «the jumping and warping
at the end might not be a parameter problem… **temporal consistency is the piece that's missing,
not the mask settings.**»
**[I] H. Трекинг: `smooth_window=3` для быстрых движений, `smooth_window=1` для абсолютного
трекинга** (из комментария к тому же треду).
**[I] I. FaceRefine может требовать RTX-апскейлера, чтобы лицо вообще распозналось** — «For this
example I needed to put an RTX upscaler in it so the face gets recognized».

**[I] J. Tessera: «мёртвые» гипотезы (дословно из LAWS.md).**
- **Frozen-border refinement (наследование выхода соседа) — устарел и вредит:** «each tile's small
  registration drift compounds into the next (measured along one row: **+1.5, −2.1, −9.3, −15.5 px**)».
  Правильно: `border_mode=overlap` + `seam_regen=32` + `seed_mode=global` (одно общее поле шума
  на весь latent).
- **Глобальное зашумление гайда (guide_noise / pixel grain) — вредно:** «per-frame corruption
  shimmers while persistent corruption reads as CONTENT and ghosts (both measured)».
  → деталь берётся из `guide_release` (хвост), а не из порчи гайда.
- **Multistep/LMS-семплер в освобождённом хвосте — вредно:** «+56% detail AND cut shimmer by a
  third» при переходе на euler. «Momentum and freedom don't mix.»
- **Два turbo-head шага — вредно:** «-21% detail»; и distilled turbo, дважды пройденный через
  mid-sigmas, **выдал NaN** в latent.
- **Три tail-шага — вредно:** «+7% energy that reads as hardness, with +17% shimmer».
- **Полный DC-якорь к plate — вредно:** «drags the refined canvas ~1.4 off-manifold in latent space
  and the VAE decodes it as mush — measured as a **319 → 87 tile-detail collapse**».
- **Latent-интерполяция — вредно:** «Pixel-space resizing is legal, **latent interpolation is
  not**» (референс-латент должен быть на целочисленной сетке: q=2 → 64px snap, q=3 → 96px).
- **Пустые тайлы галлюцинируют** («the flat-tile law»).
- **Промежуточная переоценка «на полпути» — вредно:** «Never launder detail away between hops:
  the second hop's plate is the first hop's output (pixel-resized on a legal ref grid), not a
  re-softened intermediate».
- **Metric caveat:** «**Laplacian energy ranks etchers above drawers** — a LoRA that presses harder
  on edges outscores one that draws finer organic structure. LoRA comparisons are settled by faces
  and jewelry at 1:1, not by the benchmark.»
  → **[О] ПРЯМОЕ ПОДТВЕРЖДЕНИЕ нашего вывода, что средний |лапласиан| не годится как единственный
  критерий. Наш выбор спектрального замера (3–12 px vs >25 px) — правильный подход.**

**[I] K. Tessera: потолок coherence неизбежен.** «the refine LIFTS coherence toward its ~0.75 ceiling
(0.40 -> 0.75 measured) but **never past it**, and neither release, schedule, sampling-noise
coherence, nor pre-stabilizing the plate moves the ceiling. **Fix the worst sources before
generation or accept the ceiling + the MC post pass at delivery.**»
→ **[О]** Очень важно для нас: наш текущий best-видео 8.0/10, возможно, упирается в этот потолок
потому, что исходный клип уже «кипит». **Пре-стабилизация исходника ДО рефайна** — отдельный
измеримый шаг.

**[I] L. 2-шаговый рефайн = потеря мелкой текстуры.** «the 2-step pass… is about 30% faster but
**visibly softer on fine texture (fur, moss)** than 3 steps».
— [Open-H3-Regenerate-2K README](https://huggingface.co/Efficient-Large-Model/Open-H3-Regenerate-2K)

**[I] M. Open-H3-Regenerate-2K не измеряем PSNR.** «Same-seed PSNR is not a usable metric for this
model: rounding-level changes move the 3-step output a lot.»

**[I] N. RIFE/интерполяция прячет мерцание, но даёт ghosting на быстром движении.**
«RIFE / FILM interpolation smooths judder but can hide flicker and ghost on fast motion.»

**[I] O. Depth/Pose ControlNet не фиксирует текстуру и идентичность.** «This suppresses geometry
flicker; **it does NOT lock texture or identity**.»

**[I] P. SeedVR2: «temporally coherent, needs batch >= 5 to engage»** — и отдельно Reddit:
«This method adds impressive detail, bringing out features like skin pores, fabric textures» —
[r/comfyui, "SeedVR2 vs RTX Video Super Resolution"](https://www.reddit.com/r/comfyui/comments/1x6jx3m/seedvr2_vs_rtx_video_super_resolution_for/)
**[O]** Community-описание SeedVR2 как «вытягивающего поры» расходится с нашим замером
(SeedVR2 = −0.5 кожи). Либо у них другой ROI/разрешение, либо SeedVR2 добавляет *видимые*
микро-контрасты, но не поры в спектральном смысле. **Это стоит перепроверить на нашем
спектральном замере** — community и мы измеряем разные вещи.

**[I] Q. VAE round-trip сдвигает цвет/контраст и накапливается.** «**VAE round-trip shifts color
and contrast and compounds across passes**; fp16 VAE can overflow to NaN → black images.
Workaround: `--fp32-vae` (or `--bf16-vae`); **encode once, stay in latent, decode once at the end**»
— [Agent-Kit](https://github.com/SlavaSexton/ComfyUI-Agent-Kit/blob/master/docs/ADVANCED.md)
**[О]** Совпадает с нашим измерением «латентные трюки = no-op (1.01x → 1.006x)»: VAE round-trip
убивает текстуру. Agent-Kit формулирует это как **цветовой/контрастный** сдвиг, мы — как потерю
текстуры. Оба симптома одного явления.

**[I] R. «Photorealistic» LoRA на базе, которая уже это умеет, может конфликтовать.** Автор
Polyhedron: «We need to be a bit careful with LoRAs here, because **it's the same story as with
Wan 2.2: the base model already knows(!) what all of this is supposed to look like, and that can
clash**… They do have an effect, but the whole thing is very prompt-sensitive.»

**[I] S. H3 «plastic texture» не лечится хаками.** Комментарий под «A little solution for the
plastic skin with Minimax H3»: «It does look better, but I feel at this point we're trying to make
H3 do something that it's not capable of doing. **Hacks and upscales are the only way**, it simply
will not give you hollywood level quality even at higher resolution. I just usually run it through
an upscale as the last pass. **It doesn't sort it**, but it makes things a bit crisper.»
**[I]** Там же — практика, которую стоит повторить: «add a node between SamplerCustomAdvanced and
the VAE Decode (video) and **make it less contrasty**, it will bring so much more details but don't
go too far because it can cause loss of quality.»
**[O]** «Меньше контраста перед VAE Decode» — это НЕ наш 1x-ITF-SkinDiffDetail-пасс (это деталь-пасс
по ROI). Это глобальное снижение контраста латента/изображения. **Мы это не пробовали.** Дёшево,
стоит замерить: один параметр на узле между сэмплером и VAE Decode.

**[I] T. «Recap the whole frame» подход (Ref2VA + H3 face detailer) в 2026 — спорный.**
Reddit: «I still continue using it for my Deep Space 9 memes because the overpowering likeness
stuff, **while deep fried, is really cool**» — т.е. автор сознательно принимает артефакты.

### 4.2. Наши собственные выводы (совпадают с источниками)

**[О]** 1. Деталь-пасс после декодирования в видео = 0 (наш замер + Reddit §4.1 A).
**[О]** 2. Средний |лапласиан| — плохой критерий (наш замер + Tessera §4.1 J «metric caveat»).
**[О]** 3. SeedVR2/RIFE поры не создают (наш замер) — при этом community-описание SeedVR2
расходится; расхождение стоит проверить.
**[О]** 4. VAE round-trip убивает текстуру (наш замер) — Agent-Kit формулирует то же как цветовой
сдвиг.

---

## 5. SSS (subsurface scattering) в ComfyUI — что реально работает

**[И] A. `Subsurface Diffusion PRO`** (пак «Post Processing PRO»,
[github.com/Guillaume-127/ConfyUI_Post_Processing_PRO](https://github.com/Guillaume-127/ConfyUI_Post_Processing_PRO)),
автор Guillaume-127, 3 ⭐ на comfy.icu:
- Механика: «splits the image into RGB channels, applies a Gaussian blur to the **red channel** at
  `red_scatter_radius`, then blends the blurred red with the original red by `strength` before
  recombining with the untouched green and blue».
- **[И]** Параметры: `red_scatter_radius` default **15** (диапазон 1–100), `strength` default **0.8**
  (0–1). «15 is a natural portrait value; tighter for macro, larger for softer skin-light».
- **[И]** «Back off to ~0.5 if the skin starts to lose its warm blush».
- **[I]** Без моделей и без требований — один split каналов + OpenCV blur, инстант на CPU.
- **[I]** Грабля: «crank it to 60 and you get a red smear over the whole image… **Keep it in the
  10–25 zone.**»
- **[I]** Второй половина проблемы: «expecting it to fix skin on its own. Subsurface diffusion
  addresses why skin looks plastic, but the «too smooth» complaint has a second half — the
  missing micro-speculars on pores and sweat — and that's **Specular Micro-Bloom PRO**'s job. The
  two are a matched pair».
- **[О]** Ключевое свойство для нас: **зелёный и синий каналы (где живут поры) не теряют ни пикселя
  резкости**. Т.е. SSS добавляет «живой тон» кожи, не размывая поры. Именно то, что нужно.
- **[?] Не проверено на наших клипах.** Внимание: это **пиксельный** пост-пасс на все изображение,
  не только на ROI кожи → есть риск изменить баланс фона.

**[И] B. `PhotoLab`** (пак ComfyUI-ialhabbal, ialhabbal):
«applies effects to generated images like JPG compression, pixelation, color grading, film grain,
vignette, blur, and optional lighting matching with a reference image» + «advanced skin retouching
for portraits». [comfy.icu/node/PhotoLab](https://comfy.icu/node/PhotoLab),
[github.com/ialhabbal/ComfyUI-ialhabbal](https://github.com/ialhabbal/ComfyUI-ialhabbal).
**[?]** Точные параметры SSS-части — не подтверждены.

**[I] C. DAZSSS / DiffSynth-Studio / «SSS ControlNet»** — **конкретных подтверждённых ComfyUI-нод
с измеренными параметрами для 2026 в этом исследовании НЕ НАЙДЕНО.** Первые два результата
по запросу «comfyui DAZSSS node subsurface scattering skin how to use parameters 2026» — это
Blender/Notch/Unreal, не ComfyUI. **[?] Не подтверждено** — считаем эту часть задачи
нерешённой и отмечаем, что community-рекомендуемый путь — Subsurface Diffusion PRO (пиксельный).

**[I] D. Ограничение ComfyUI по цветоуправлению (важно для SSS-работы):**
«**No native layers / timeline / compositing, and no real color management** (sRGB/linear handled
naively, ICC effectively ignored…)» и «**Manual transforms belong in log, not linear.** Any non-AI
pixel geometry… crushes highlight/shadow detail when done on linear data» —
[Agent-Kit](https://github.com/SlavaSexton/ComfyUI-Agent-Kit/blob/master/docs/ADVANCED.md)
**[О]** Значит SSS по красному каналу в sRGB-пространстве — это не физически корректный SSS,
а «дешёвый, физически мотивированный» (так и написано у автора: «a legitimate,
physically-motivated simplification»). Для нашей цели (живой тон на лице) — приемлемо.

---

## 6. Модели на HF: repo id, файл, триггер, сила, VRAM

| Repo id | Файл | Триггер | Сила | VRAM / примечание |
|---|---|---|---|---|
| `fal/MiniMax-H3-Realism-People-LoRA` | `h3-realism-people-t2v-i2v-r2v.safetensors` | `r34l1sm` | **1.0** (0.6–0.8 легче) | **[И]** у нас есть; LoRA ~125 MB |
| CivArchive `[MMH3] Realism Slider v1.0` | — | (slider, без триггера) | **1.0–1.5** (допустимо 0.25–2) | **[И]** у нас есть; тестировать **без turbo LoRA** |
| `Alissonerdx/Minimax-H3-ComfyUI` | `minimax_h3_lms_v1.0_r64` | «Fixed enhancement caption» | **[?]** | **[И]** rank 64, требует aligned source-video guide; ключевой для Tessera |
| `Alex995647/loras-minimax-h3` | 110 LoRA (63.5 ГБ) | — | — | **[И]** зеркало Civitai+HF, удобно для обхода отсутствующих |
| `Kijai/MiniMax-H3-experimental` | `loras/minimax_h3_open_regenerate_2k_refiner_rank_16_bf16.safetensors` | — | «at a reduced weight» (Reddit) | **[I]** rank-16 **только refiner** (без LMS/Turbo); официально комбинируется с Ultimate MiniMax H3 Upscale на 6 ГБ (Reddit, не проверено) |
| `LBH-123-AI/Minimax_h3_latent_Upscaler` | модели в `models/latent_upscale_models/` | — | — | **[I]** 24-канальный 3D-conv, обходит 5B VAE; есть temporal chunking |
| `Sensen02/Open-H3-Regenerate-2K` | weights (через `download.py`) | — | — | **[I]** ⚠️ **~150 ГБ VRAM** — не для нас |
| `inlineresearch/skin-lora-krea-2-raw` | `inline-skin-lora-krea-2-raw.safetensors` | `inline-skin-lora` | **[?]** | **[I]** для Krea 2 RAW (у нас нет Krea 2) |
| `houseofboern/skintexture-krea2-v3-ckpts` | `skintexture_krea2_v3` | **нет триггера** (always-on) | **[?]** | **[I]** для Krea 2 Raw |
| `jay1000/krea2-loraholic-skin-details` | — | **[?]** | **0.5–3.5** | **[I]** для Krea 2 |
| Civitai `Polyhedron Perfect Eyes/Skin/Hands` (H3) | `polyhedron_minimax_h3_image_lora_v2__perfe8ct.safetensors` | `perfe8ct` `perfect hands` `perfect skin` `perfect eyes` | **[I]** «use with lower weights» (овертрейн) | **[I]** bf16 295.8 MB, 3000 шагов / 10 эпох |
| Civitai `Polyhedron_all` (FLUX.1 D) | — | `Perfect Eyes Detailed skin Skin blemish` | **[?]** | **[I]** Very Positive (477) |
| Civitai `Polyhedron_all` (Wan 2.2 T2I A14B) | — | `Perfect skin Perfect hands Perfect eyes` | **[?]** | **[I]** Very Positive (105) |

---

## 7. План проверки на нашем стенде (по приоритету, без коммита)

**[О]** Порядок по (ожидаемый эффект / стоимость):

| # | Что | Ожидание | Стоимость |
|---|---|---|---|
| 1 | Чистый H3 (без turbo LoRA), 30 и 50 шагов, fl2va vs ref2va | Разделить «пластик из-за режима» и «пластик из-за turbo». Может дать основную часть дельты до 7.4/10 | 2–3 рендера |
| 2 | TST (`comfyui-SelfLift`) `tau=0.2` на лучшем текущем клипе | Срезать temporal HF-дельту без потери пор | 1 рендер, ~0 GPU-стоимости |
| 3 | `Subsurface Diffusion PRO` `radius=15, strength=0.8` (и 0.5) | Живой тон кожи без потери резкости пор | секунды, CPU |
| 4 | «Меньше контраста перед VAE Decode» (Reddit §4.1 S) | Деталь без потери качества | 1 рендер |
| 5 | Realism Slider 1.0–1.5 × r34l1sm 1.0, без turbo | Заявленный «natural skin finish» | 2 рендера |
| 6 | `ComfyUI-H3-FaceRefine` (+ `H3 Per-Frame Denoise`, `large_face=0.35`) | Лицо без пластика, потому что перегенерируется H3, а не GFPGAN | 1–2 рендера, VRAM! |
| 7 | `ComfyUI-Tessera` 2× на 24–49 кадрах + LMS detail LoRA | **+67% tile-local detail** — главный кандидат на поры в видео | долго, VRAM! |
| 8 | `Tessera Guide Anchor` с детальным still (наш still 9.2/10) | Перенос детали из still в видео | в рамках #7 |

**[О]** Замеры — наши существующие: `scripts/probe_pore_spectrum.py` (спектр 3–12 px vs >25 px +
выживание пор после блюра 1.2) и `scripts/probe_skin_temporal.py` (temporal HF-дельта).
**[О]** Критерий приёмки: поры должны **выжить blur sigma 1.2** и давать **HF-дельту < наша текущая**
при росте pore-spectrum. Порог отбора — не средний |лапласиан| (Tessera §4.1 J).

**[O]** Чего НЕ делать: не пробовать снова GFPGAN/CodeFormer/UltraSharp/RealESRGAN/SeedVR2/RIFE
для пор; не ставить Qwen-Image-2.1 masked refine; не пробовать FLUX.1-dev img2img для текстуры;
не ждать от `Temporal Smooth` роста детали (он только сглаживает, §4.1 B).

---

## 8. Что осталось НЕИССЛЕДОВАННЫМ (честный список)

- **[?]** DAZSSS / DiffSynth-Studio / «SSS ControlNet» в ComfyUI — конкретных нод с измеренными
  параметрами не найдено. Требует отдельного захода (GitHub `DAZSSS`, DiffSynth-Studio repo).
- **[?]** «Deroping» как метод для H3 — термин не определён, инструмент не идентифицирован.
- **[?]** «Star7 node» (упомянут в Reddit как не-проверенный) — не идентифицирован.
- **[?]** Конкретные параметры SSS-части `PhotoLab`.
- **[?]** VRAM-потребление Tessera (нет числа; README: «544p to 4K/8K on one consumer GPU»).
- **[?]** Как `Polyhedron Perfect Skin` (H3) ведёт себя конкретно в нашем видео-пайплайне.
- **[?]** Kijai Regenerate-2K LoRA на 6 ГБ (только Reddit-видео, не проверено).
- **[?]** Multi-frame diffusion для детализации кожи как самостоятельная техника (кроме Tessera) —
  готовых реализаций в ComfyUI не найдено; AnimateDiff-Evolved / FreeNoise решают *длину и
  согласованность*, а не детализацию.
- **[?]** Формат `P2P temporal` (Prompt-to-Prompt temporal) для детализации — не найдено.
- **[?]** H3 ControlNet Tile для структурной фиксации (вариант (b) из Agent-Kit) — есть ли
  H3-ControlNet в 2026, не выяснено.

---

## 9. Источники (все ключевые)

**Репозитории / модели**
- https://github.com/hashu786/comfyui-tessera + [docs/LAWS.md](https://github.com/hashu786/comfyui-tessera/blob/main/docs/LAWS.md)
- https://github.com/facok/comfyui-SelfLift (TST; arXiv 2609.08505; arXiv 2609.02036)
- https://github.com/Carasibana/ComfyUI-H3-FaceRefine
- https://github.com/nikythebikky/ComfyUI-Temporal-Face-Detailer
- https://github.com/Guillaume-127/ConfyUI_Post_Processing_PRO
- https://github.com/ialhabbal/ComfyUI-ialhabbal
- https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler
- https://github.com/NVlabs/Sana/tree/sol-engine/models/Open-H3-Regenerate-2K
- https://github.com/lytang63/temporal-state-transport
- https://github.com/daniabib/ComfyUI_ProPainter_Nodes
- https://github.com/karcsiha/comfyUi-deflicker
- https://github.com/SlavaSexton/ComfyUI-Agent-Kit/blob/master/docs/ADVANCED.md
- https://github.com/rogen84/SelfLiftH3Sampler , https://github.com/slmonker/selflift-Avatar
- https://github.com/MiniMax-AI/awesome-minimax-h3-integration
- https://huggingface.co/Efficient-Large-Model/Open-H3-Regenerate-2K
- https://huggingface.co/fal/MiniMax-H3-Realism-People-LoRA
- https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI
- https://huggingface.co/Kijai/MiniMax-H3-experimental
- https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler
- https://huggingface.co/inlineresearch/skin-lora-krea-2-raw
- https://huggingface.co/houseofboern/skintexture-krea2-v3-ckpts
- https://huggingface.co/jay1000/krea2-loraholic-skin-details
- https://huggingface.co/Alex995647/loras-minimax-h3
- https://civitai.com/models/2899220 (Polyhedron H3 Perfect Eyes/Skin/Hands)
- https://civitai.com/models/654412 (Polyhedron_all FLUX.1 D)
- https://civitai.com/models/1947995 (Polyhedron_all Wan 2.2)
- https://civarchive.com/models/3014078 ([MMH3] Realism Slider)
- https://civitai.com/models/2835250 (H3 Ultra Fastest workflow)

**Reddit**
- https://www.reddit.com/r/StableDiffusion/comments/1wbt2ey/ (plastic skin: less contrast before VAE Decode)
- https://www.reddit.com/r/StableDiffusion/comments/1w4bxn8/ (50 steps, away from turbo)
- https://www.reddit.com/r/StableDiffusion/comments/1wxy7zd/ (H3 non-turbo settings)
- https://www.reddit.com/r/StableDiffusion/comments/1w4x4sp/ (turbo → oily/plastic; two-stage latent upscale)
- https://www.reddit.com/r/StableDiffusion/comments/1vygmwv/ (**ref2va vs fl2va для кожи**; 20→50 шагов; hybrid model)
- https://www.reddit.com/r/StableDiffusion/comments/1vsn8ge/ (**per-frame = jitter**; FaceRefine reference игнорируется; same seed/prompt/steps)
- https://www.reddit.com/r/StableDiffusion/comments/1vn4uho/ (H3 Face detailer, 455↑)
- https://www.reddit.com/r/StableDiffusion/comments/1xher16/ (BF16 + face refinement; "authentic skin texture, display pores")
- https://www.reddit.com/r/StableDiffusion/comments/1vx5okm/ → /1wx5okm/ (**RefMod deep-frying**)
- https://www.reddit.com/r/StableDiffusion/comments/1x6jui1/ (LongTakeNoCuts: masking + refine)
- https://www.reddit.com/r/comfyui/comments/1xjgkwm/ (Most Optimal Face Refining Workflow for H3)
- https://www.reddit.com/r/StableDiffusion/comments/1wfvfg0/ (face refine vs deroping)
- https://www.reddit.com/r/comfyui/comments/1x9sgr1/ (selflift-Fish на 16 ГБ)
- https://www.reddit.com/r/comfyui/comments/1xmrcyl/ (PIKA H3 + Singularity + Regenerate-2K на 6 ГБ)
- https://www.reddit.com/r/comfyui/comments/1x6jx3m/ (SeedVR2 vs RTX VSR — «skin pores»)

**Справочные / ноды**
- https://comfy.icu/node/TemporalSmooth , /TemporalFaceDetailer , /H3PerFrameDenoise ,
  /SubsurfaceDiffusionPRO , /PhotoLab , /Load-Face-Enhance-Model-mtb
- https://comfyui-wiki.com/en/news/2026-10-30-tessera-h3-upscaler
- https://docs.comfy.org/tutorials/video/minimax/minimax-h3
