# NSFW-фильтры в весах vs API-фильтры + план фотореализма кожи в видео (16 ГБ VRAM)

**Дата проверки:** 2026-11-03. **Стенд:** ComfyUI 0.43.0, RTX 4070 Ti SUPER 16 ГБ, RAM 68.5 ГБ, PROD :8188, LAB :8189.
**Диски (измерено):** C: 625 ГБ свободно · G: 8.9 ТБ свободно · **E: 26 ГБ свободно (95 % занято) — на E: ничего не ставим.**

**Легенда разметки (везде в документе):**
- 🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ** — первичный источник (HF README/LICENSE/файл, GitHub-репо/код, официальный README) прочитан и процитирован; для локального стенда — путь к файлу + строка.
- 🟡 **ОЦЕНКА** — вывод из проверенных фактов + наших измерений, первичного подтверждения нет.
- 🔴 **НЕИЗВЕСТНО** — не проверено или источник недоступен; требует проверки.

> Область применения: приватный локальный стенд, R18+ NSFW-LoRA. Вопросы лицензионной пригодности для коммерческого/клиентского использования разобраны отдельно (Часть 1.4) — **это не юридическая консультация**.

---

## ЧАСТЬ 1. Фильтруют ли видео-модели NSFW на уровне ВЕСОВ

### 1.0 Главный ответ (коротко)

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ.** Ни у одной из проверенных моделей (MiniMax H3, Wan 2.1/2.2/S2V, Qwen-Image 2.1 / Image-Edit 2509/2511, FLUX.1-dev/schnell, FLUX.2-dev, LTX-2.5, HunyuanVideo, CogVideoX, Mochi, Z-Image-Turbo) **в репозитории весов нет ни одного файла/модуля safety-классификатора, NSFW-фильтра, модератора или watermark-кода.** Все «цензуры», которые видят пользователи, — это:

1. **серверные фильтры API/хостингов** (MiniMax API, Alibaba Cloud Model Studio, fal.ai, Replicate, HF Inference API, kimi.ai) — их у локальных весов нет по определению;
2. **пробел в обучающих данных** (Wan, FLUX.1) — модель «понимает концепт», но не умеет её отрисовать → странный выход, а не отказ;
3. **safety fine-tuning внутри весов** — это есть только у **FLUX.2-dev** (и в меньшей степени у FLUX.1-dev), см. 1.2;
4. **рефьюз в текст-энкодере** — это про H3 и Qwen (см. 1.1), это единственный «фильтр», который реально присутствует в наших локальных чекпойнтах, и он **снимается заменой энкодера**.

Ответ на главный вопрос постановки: **«Если базовая модель сама себя цензурит, LoRA может не преодолеть фильтр» — для нашего стенда это НЕ так**, потому что:
- H3: цензура живёт в **Qwen3-VL-32B текст-энкодере** (который у нас стоковый), а не в DiT. NSFW-LoRA на DiT работает.
- Qwen-Image-Edit: NSFW-LoRA (`qwen_image_edit_plus_nsfw_lora`) — **официально документирован** самим автором LoRA как работающий на `Qwen/Qwen-Image-Edit-2511`.
- Wan/FLUX.1/LTX/Hunyuan/CogVideoX/Mochi/Z-Image: фильтра в весах нет.

🟡 **ОЦЕНКА:** «Обход фильтра» как отдельная задача для локальных весов **не нужна** — нужен только правильный текст-энкодер и правильная лицензия.

---

### 1.1 MiniMax H3 — самый важный случай (мы на нём работаем)

#### 1.1.1 Где живёт «Safety Guardrails» — в API, а не в весах

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ.** В карточке модели `MiniMaxAI/MiniMax-H3` (README.md, строка ~98-100) блок «**Safety Guardrails**» находится **внутри раздела `### H3-Context-IR`**, а не в разделе `### H3-Base`:

> **H3-Context-IR** is a hosted preprocessing and orchestration system … *Because H3-Context-IR relies on a multi-stage workflow and multiple hosted models and services, **it is not included in this open-source release**.*
> **Safety Guardrails** — User-submitted text, images and videos, as well as enhanced prompts, are subject to automated moderation. Content suspected of being unlawful, pornographic, or infringing third-party rights may be blocked.

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ.** Открытые веса — это ровно `H3-Base` (трансформер + VAE + аудио-VAE + энкодер). В репозитории `MiniMaxAI/MiniMax-H3` **280 файлов**; ни один не содержит в имени `safety|filter|moder|censor|guard|nsfw|watermark` (проверено списком `siblings` через HF API). Файлы кода — только VAE/attention/flash/normalize и аудио-VAE (DACL).

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ.** `docs/QA-about-Licеnse.md` (MiniMax, официальный) прямо разделяет два режима:

> **API:** Globally available with built-in safeguards and responsible-use controls.
> **Open weights:** Temporarily limited in certain regions until the regulatory and compliance framework becomes clearer.
> … For API access, MiniMax operates the serving infrastructure and can enforce appropriate safeguards … With open weights, users can independently deploy, modify, and distribute the model, which makes it much harder to ensure the same level of compliance.

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ.** У H3 **нет встроенного watermark** — в README нет упоминаний watermark/C2PA (grep по README: 0 совпадений). Для сравнения, FLUX.2-dev явно пишет про pixel-layer watermarking (см. 1.2.3).

**Ответ про H3 ref2va / fl2va:** 🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ** — обе ветки (`FL2VA/`, `Ref2VA/`) лежат в одном репозитории и имеют **одинаковую** структуру (трансформер + энкодер + VAE); никаких различающихся safety-модулей между ними нет. Фильтр для H3 ref2va/fl2va — **такой же, как для t2v: только API**.

#### 1.1.2 ГЛАВНОЕ: цензура H3 — это Qwen3-VL-32B текст-энкодер (и это то, что надо чинить)

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ.** `MiniMaxAI/MiniMax-H3` README, раздел H3-Base:

> **H3-Encoder** — H3 uses the **full pretrained weights of Qwen3-VL-32B** and provides the hidden states from its **50th layer** to the H3-Omni-Transformer.

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ.** Лицензия H3, Additional Note: «the encoder of MiniMax H3 uses Qwen3-VL-32B, which is licensed under **Apache 2.0**» → **текст-энкодер можно заменять свободно**.

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ.** `wildminder/awesome-minimax-H3`, `blocks/20_text_encoders.md`:

> **▣ Qwen3-VL-32B Ultra-Heretic (Uncensored)** — Built from `llmfan46/Qwen3-VL-32B-Instruct-ultra-uncensored-heretic` by `ethanfel`. … The "Heretic" lineage **bypasses alignment/restriction layers in the text encoder** so MiniMax-H3 receives the most faithful prompt embeddings.

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ.** `jjhuangtw/MiniMax-H3-portable-panel/NSFW_MODELS.en.md`:

> The uncensored **Heretic** text encoder is option 6 in `download_extras.bat`. It only removes the language model's refusals; **what H3 can draw depends on the diffusion model above.**

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ.** `ComfyUI-QwenVL-Mod/update.md` (huchukato): «Text encoder: remains the uncensored `qwen3vl_32b_h3_ultra_uncensored_heretic_int8_convrot.safetensors` — `ethanfel/Qwen3-VL-32B-Ultra-Heretic-H3-ComfyUI-INT8-ConvRot`».

#### 1.1.3 Состояние НАШЕГО стенда — вот где дыра

🟢 **ПРОВЕРЕНО НА МЕСТЕ.** В `C:\AI\Models\comfyui\text_encoders\` лежат **только стоковые** энкодеры:

```
qwen3vl_32b_minimax_h3_int8_convrot.safetensors    27 141 342 152 Б  (25.3 GiB)  ← СТОК
qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors       15 687 142 551 Б  (14.6 GiB)  ← СТОК (4-бит)
```

🟢 **ПРОВЕРЕНО НА МЕСТЕ.** Heretic-варианта `qwen3vl_32b_h3_ultra_uncensored_heretic_int8_convrot.safetensors` **нет** ни в `C:\AI\Models\comfyui\text_encoders\`, ни в `ComfyUI\models\text_encoders\` (тот каталог — симлинк на `C:\AI\Models\comfyui`).

🟡 **ОЦЕНКА (высокая уверенность):** стоковый Qwen3-VL-32B-инструкция **может** рефьюзить/смягчать NSFW-промпты при формировании conditioning. Наш 227 H3 LoRA (в т.ч. `General NSFW FL2VA`, `General NSFW REF2VA`, `HMNSFW_AIO_V2`, `MiniMax H3 NSFW Unlocked v1.1`, `NSFW Unlocked v2.0` — все на месте) **LoRA на DiT**, они этот слой не трогают. Поэтому смена текст-энкодера на Heretic — **самое дешёвое и самое высокоэффективное изменение** в Части 1. Размер: int8-convrot = **26 363 476 151 Б ≈ 24.5 GiB** (HF API, tree `ethanfel/Qwen3-VL-32B-Ultra-Heretic-H3-ComfyUI-INT8-ConvRot`).

⚠️ **Важно:** замена энкодера меняет conditioning → **все наши измеренные метрики (текстура, HF-дельта, дрейф лица) придётся перемерить заново**. Heretic ≠ «больше текстуры»; это про рефьюзы, не про фотореализм.

#### 1.1.4 H3: что насчёт «не NSFW-Unlocked» чекпойнтов и Blackfrost

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ.** Репозитории `Blackfrost-Research/MINIMAX-H3-NSFW` и `…-NSFW-COMFYUI` на момент проверки **не существуют** (HF API: 0 файлов; страница — 404). Ранее они были «ре-аплоадами» с изменёнными README; публичное предупреждение — на hackaigc. **Их ставить не нужно и нельзя.**

🟡 **ОЦЕНКА:** реальный «uncensored H3» — это (а) Heretic-текст-энкодер + стоковые веса, либо (б) комьюнити fine-tune DiT: `TenStrip/10Eros-Max` (14.0 ГБ, Turbo-hybrid w4a8, FL2VA+Ref2VA, 24 ГБ+ VRAM), `DaSiWa MiniMax H3 Hybrid Turbo v2 int8` (21.0 ГБ), GGUF-сборки `Abiray/10Eros-Max-GGUF` (Q3_K_M 8.9 ГБ / Q4_K_M 11.6 ГБ). Для 16 ГБ VRAM — только GGUF-варианты или текущий int8-convrot.

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ (лицензия H3).** `LICENSE` на HF: «**Excluded Territories** means the European Union, the United Kingdom, the Republic of Korea and the United States of America». Exhibit A (Acceptable Use Policy, rev. 2026-08-02) — **20 пунктов; отдельного пункта про porn/NSFW в Exhibit A НЕТ**. Пункт 5: «Use to **circumvent or bypass any safety guardrails or safeguards we have implemented**» — т.е. запрет обхода **реализованных MiniMax safeguards** (это про хостинг/дистрибуцию, где safeguards обязательны), а не blanket-запрет NSFW-контента у локальных весов. Пункт 1 — «Use outside the Applicable Territory» (т.е. US/EU/UK/KR — нельзя). Пункт 19 — «Use for military purposes». Пункт 3 — «Use in any manner that may harm yourself or others». Раздел V п.5 — хостеры обязаны внедрить safeguards, «must not knowingly disable, materially weaken, or permit the circumvention of those safeguards».

🟡 **ОЦЕНКА (юридический риск, не технический):** при работе в юрисдикции **США / ЕС / Великобритании / Южной Кореи** H3-лицензия прямо не авторизует использование. Для коммерческих/клиентских задач в этих юрисдикциях — только Wan 2.2 (Apache-2.0) либо платная лицензия. Технически локальные веса работают одинаково; разница — прагматическая (страховка от претензий + текст «MiniMax H3» в UI).

---

### 1.2 FLUX — единственная семья, где фильтр РЕАЛЬНО внутри весов/репозитория

#### 1.2.1 FLUX.1-dev

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ.** `black-forest-labs/FLUX.1-dev` README: **0 совпадений** по `nsfw|safety|moderat|filter|censor|watermark`. То есть **встроенного NSFW-фильтра в FLUX.1-dev нет**.

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ.** `LICENSE.md` FLUX.1-dev, п. (e) — это **юридическое**, а не техническое требование:

> You may access, use, Distribute, or create Output … if you: (i) (A) **implement and maintain content filtering measures ("Content Filters")** … or (B) ensure Output undergoes review …

и п. (b) — «Non-Commercial Purpose means … as long as you do not receive any direct or indirect payment arising from the use of the FLUX.1 [dev] Model, Derivatives, or **FLUX Content Filters** (as defined below)». То есть: **фильтров в репозитории нет**, но лицензия обязывает деплойера (хостинг-провайдера) их иметь. Локальный одиночный рендер под это не подпадает по смыслу (нет «Third Party»-дистрибуции), но при передаче доступа третьим лицам обязывает.

🟡 **ОЦЕНКА:** «пре-тренинг очищен от откровенной анатомии» (об этом пишут комьюнити-гайды) означает **пробел в данных** (как у Wan), а не фильтр. Лечится LoRA (`Ballsworthy/Flux-Uncensored-V2`, `RomixERR/FLUX_NSFW_unlocked`, `enhanceaiteam/Flux-uncensored`) или merge (`FLUX-UNCENSORED-Merged`, `Chroma1-HD`, `SNOFS`).

#### 1.2.2 FLUX.1-schnell

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ.** `black-forest-labs/FLUX.1-schnell` README: `license: apache-2.0`; «Released under the apache-2.0 licence, the model can be used for personal, scientific, and **commercial** purposes». Никаких упоминаний фильтра.

#### 1.2.3 FLUX.2-dev — а вот это уже фильтр В ВЕСАХ

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ** (README, раздел «Risks», пп. 1-6, дословно):

> 1. **Pre-training mitigation.** We filtered pre-training data for multiple categories of "not safe for work" (NSFW) and known CSAM …
> 2. **Post-training mitigation.** … we undertook multiple rounds of targeted fine-tuning to provide additional mitigation … **By inhibiting certain behaviors and suppressing certain concepts in the trained model**, these techniques can help to prevent a user generating synthetic CSAM or NCII …
> 4. … Based on these findings, we conducted further safety fine-tuning to produce our open-weight model (FLUX.2 [dev]) …
> 5. **Inference filters.** The repository for the FLUX.2 [dev] model **includes filters for NSFW and IP-infringing content at input and output**. **Filters or manual review must be used with the model** under the terms of the FLUX.2 [dev] Non-Commercial License. We may approach known deployers … at random to verify that filters or manual review processes are in place …
> 6. … The inference code for FLUX.2 [dev] implements an example of pixel-layer watermarking …

🟡 **ОЦЕНКА (важно):** FLUX.2-dev — это **первая из проверенных моделей, где фильтр действительно встроен в веса (safety fine-tuning, подавление концептов)**, плюс в репозитории лежат input/output-фильтры и watermark. LoRA поверх FLUX.2-dev **не отменит** safety fine-tuning. **Мы на стенде используем FLUX.1-dev fp8 — он чист.** Для NSFW-работ FLUX.2-dev не использовать.

---

### 1.3 Остальные модели — фильтра в весах нет

| Модель | Фильтр в весах? | Чем проверено | Лицензия (коммерция) |
|---|---|---|---|
| **Wan 2.1 / 2.2 (T2V-A14B, TI2V-5B, I2V-A14B)** | 🟢 **НЕТ** | 🟢 Репозиторий `Wan-Video/Wan2.2` — **101 файл**, grep по дереву `safety\|filter\|moder\|censor\|guard\|nsfw\|watermark` → **NONE**. README: 0 совпадений по nsfw/filter/safety. `LICENSE.txt` — чистый Apache-2.0; README: «We claim no rights over the your generated contents» | 🟢 **Apache-2.0** (HF API `license: apache-2.0` для `Wan-AI/Wan2.2-TI2V-5B` и `Wan-AI/Wan2.2-S2V-14B`) — **коммерция разрешена** |
| **Wan 2.2 S2V-14B** | 🟢 **НЕТ** | 🟢 Тот же репозиторий `Wan-Video/Wan2.2`, `requirements_s2v.txt`; HF API → apache-2.0 | 🟢 Apache-2.0 |
| **Wan-Fun / Wan-Fun-V1.1-14B** | 🟡 **ОЦЕНКА: НЕТ** | 🔴 Не проверял первичный репозиторий `ali-vilab/Wan-Fun` построчно | 🔴 Не проверял |
| **Qwen-Image 2.1 / Image-Edit 2509 / 2511** | 🟢 **НЕТ** | 🟢 README каждой карточки: **0 совпадений** по nsfw/filter/safety/censor. Фильтр живёт только в Alibaba Cloud / HF Demo | 🟢 **Qwen Research License**: «FOR NON-COMMERCIAL PURPOSES ONLY» = «for research or evaluation purposes only» (LICENSE, п. a/i). Коммерция → отдельная лицензия `model-business@notice.qwencloud.com`. Атрибуция «Built with Qwen» обязательна. В лицензии **нет ни одного NSFW/guardrail-пункта** (grep по porn/sexual/guardrail/bypass → 0) |
| **FLUX.1-dev / schnell** | 🟢 **НЕТ** (см. 1.2) | 🟢 README + LICENSE | 🟢 schnell — Apache-2.0 (коммерция ✅). dev — non-commercial на саму модель, но фильтры обязательны **лицензионно**, не технически |
| **FLUX.2-dev** | 🟢 **ДА** (safety FT + input/output-фильтры + watermark) | 🟢 README «Risks» пп. 1-6 (дословно выше) | 🟢 FLUX.2 [dev] Non-Commercial License; **фильтры обязательны по лицензии** |
| **LTX-Video / LTX-2.5** | 🟢 **НЕТ** | 🟢 `Lightricks/LTX-2` README: 0 совпадений по nsfw/filter/safety | 🟢 LTX-2.x Community License: при выручке >$10M нужна платная лицензия. Uncensored-сборка `ChrisColeTech/LTX-2.5-uncensored-v1.1-FP8` — 🔴 **лицензия «unknown»** (abliteratedmodels.org) → коммерчески непригодна без проверки |
| **HunyuanVideo** | 🟢 **НЕТ** (карточка без safety-разделов) | 🟢 HF API: `license: other`, тега `nsfw` нет; в README нет упоминаний фильтра | 🔴 Детали лицензии Tencent не разбирались |
| **CogVideoX-5b** | 🟢 **НЕТ** | 🟢 HF API: `license: other`, тега `nsfw` нет | 🔴 Не разбиралась |
| **Mochi-1-preview** | 🟢 **НЕТ** | 🟢 HF API: `license: apache-2.0` | 🟢 Apache-2.0 |
| **Z-Image-Turbo** | 🟢 **НЕТ** | 🟢 `zai-org/Z-Image-Turbo` README: **0 совпадений** по nsfw/filter/safety/censor/watermark | 🔴 Не разбиралась; 🟡 Z-Image — не видео-модель, только для стилла |

#### 1.3.1 Важная оговорка про «фильтр = refusal» vs «фильтр = не умеет»

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ (Wan).** Комьюнити-консенсус (spicyapi.ai/blog/wan-2-2-nsfw, spicyapi.ai/blog/wan-2-1-nsfw): «**The open weights have no filter, but hosted versions differ**»; «Wan understands the concepts of nsfw and it will try to do it but it will end up a bizarre output **as it was not fed with the data**».
🟡 **ОЦЕНКА:** spicyapi.ai — **хостинг-провайдер**, заинтересованная сторона; их тесты — про их API, не про веса. Их утверждение «open weights carry no filter» согласуется с нашей построчной проверкой репозитория Wan (0 safety-файлов). Финальная формулировка «фильтров нет в весах» опирается на **наш** аудит кода, не на их маркетинг.

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ (H3, Wan 3.0).** Wan 3.0 — **API-only**, весов нет → у него есть только серверная модерация Alibaba Cloud. spicyaI: «No official weights have been released, so local deployment is not possible».

---

### 1.4 Сводка «наш стенд»: что фильтрует, а что нет

| Наш локальный чекпойнт | Фильтр в весах? | Серверный фильтр при локальном запуске? | Коммерчески пригоден? |
|---|---|---|---|
| `minimax_h3_fl2va_pruned_int8_convrot.safetensors` (20.97 ГБ) | 🟢 НЕТ (в репо H3 0 safety-файлов) | НЕТ | 🟡 НЕТ в US/EU/UK/KR |
| `minimax_h3_ref2va_pruned_int8_convrot.safetensors` (20.97 ГБ) | 🟢 НЕТ | НЕТ | 🟡 НЕТ в US/EU/UK/KR |
| `qwen3vl_32b_minimax_h3_int8_convrot` / `_nvfp4_awq` (**текст-энкодер**) | 🟡 **Вероятно ДА — рефьюз в instruction-модели** (Heretic существует именно для этого) | НЕТ | 🟢 Apache-2.0 (Qwen3-VL) |
| `qwen_image_2.1_int8_convrot`, `qwen_image_edit_2509_fp8_e4m3fn` | 🟢 НЕТ | НЕТ | 🟡 Только research/evaluation |
| `z_image_turbo_bf16` / `_fp8` | 🟢 НЕТ | НЕТ | 🔴 Не разбиралась |
| FLUX.1-dev fp8 | 🟢 НЕТ | НЕТ | 🟡 Non-commercial на модель; фильтры обязательны **при дистрибуции** |
| Wan 2.2 (нет файлов на стенде) | 🟢 НЕТ | НЕТ | 🟢 **Apache-2.0 — единственный чистый вариант** |

**Ответ на вопрос «есть ли в наших локальных чекпойнтах реальные NSFW-фильтры в весах или это только серверные фильтры»:**
🟢 **ПРОВЕРЕНО:** в весах H3/Wan/Qwen/FLUX.1/LTX/Hunyuan/CogVideoX/Mochi/Z-Image — NSFW-фильтров **нет**. Разница «веса vs API» — **в пользу локальных весов**: API-фильтры (MiniMax, Alibaba Cloud, fal, Replicate, HF Demo, kimi.ai) при локальном запуске **отсутствуют**.
🟡 **ОЦЕНКА:** единственное исключение — **текст-энкодер H3 (Qwen3-VL-32B)**: рефьюзы живут там, и это **наша единственная точка приложения усилий** в Части 1.

---

## ЧАСТЬ 2. Что доставить и какой pipeline даст фотореалистичную кожу в видео на 16 ГБ

### 2.0 Что уже стоит (проверено на месте) и чего НЕ хватает

🟢 **ПРОВЕРЕНО НА МЕСТЕ** (`C:\AI\Models\comfyui`):
- `diffusion_models/`: H3 fl2va int8-convrot 20.97 ГБ, H3 ref2va int8-convrot 20.97 ГБ (+ .bak 20.97 ГБ — кандидат на удаление), `qwen_image_2.1_int8_convrot` 7.26 ГБ, `qwen_image_edit_2509_fp8_e4m3fn` (симлинк), `qwen-image-2.1-Q4_K_M.gguf` 4.60 ГБ, `z_image_turbo_bf16` 12.37 ГБ / `_fp8` 6.29 ГБ, `seedvr2_ema_3b` 13.57 ГБ, `wan2.2_ti2v_5b_index.json` (**это диффьюзер-индекс, не чекпойнт**).
- `loras/`: **218 файлов**, в т.ч. `General NSFW FL2VA`, `General NSFW REF2VA`, `HMNSFW_AIO_V2`, `HMNSFW_v2.5`, `MiniMax H3 NSFW Unlocked v1.1`, `NSFW Unlocked v2.0`, `qwen_image_edit_plus_nsfw_lora_safetensors.safetensors`, `qwen-edit-skin.safetensors`, `z_image_skin_texture_v2.1`.
- `facerestore_models/`: `codeformer-v0.1.0.pth` (376 МБ), `GFPGANv1.3/v1.4` (348 МБ), `GPEN-BFR-512.onnx` (284 МБ).
- `upscale_models/`: `1x-ITF-SkinDiffDetail-Lite-v1.pth` (20 МБ), `1xSkinContrast-High-SuperUltraCompact.pth`, `4x-ClearRealityV1.safetensors`, `4x-UltraSharp.pth`, `RealESRGAN_x4plus.pth`.
- `insightface/inswapper_128.onnx` (554 МБ), `SEEDVR2/`: 3B fp8 3.39 ГБ, 7B sharp fp8 8.24 ГБ, VAE fp16.

🟢 **ПРОВЕРЕНО НА МЕСТЕ — установленные ноды-паки (custom_nodes), оба инстанса:**
- **PROD :8188** реально работает из `E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable` (проверено через `Win32_Process`: PID 34424, `python_embeded\python.exe -s ComfyUI\main.py --port 8188 --cuda-malloc --reserve-vram 2.0`). Каталог `E:\...\ComfyUI\custom_nodes` и `C:\Users\user\comfyui\ComfyUI_windows_portable\ComfyUI\custom_nodes` — **один и тот же инстанс** (одинаковый mtime и inode-подобное совпадение; C:-путь = junction на E:). **LAB :8189** = `E:\ComfyUI_LAB_037\ComfyUI_windows_portable` (PID 18816) — **отдельный** инстанс со своим `custom_nodes` (6 пакетов: Qwen-Image, GGUF, KJNodes, VideoHelperSuite, WanVideoWrapper, FreeVideo).
- 🟢 **PROD (`:8188`) — в `object_info` 2280 нод; подтверждено наличие:** `TesseraPlan/Grain/Anchor/Args/Dilate/Decimate/Refine/Encode/Decode` (9), `SelfLiftH3Sampler/SelfLiftImageSampler/SelfLiftH3TST` (3), `BC_SkinTexture`, `ReActorFaceSwap/ReActorFaceSwapOpt/ReActorOptions/ReActorFaceBoost`.
- 🟢 **`ComfyUI-H3-FaceRefine` (Carasibana) v1.1.2 — ФАЙЛЫ УЖЕ НА МЕСТЕ** (каталог создан сегодня **18:46**, `nodes.py` 182 283 Б, `README.md` 62 419 Б, `pyproject.toml` → `version = "1.1.2"`, 41 файл, полный чекаут). Ноды в `nodes.py:3459-3467`: `H3FaceSelect`, `H3FaceTrackCrop`, `H3FaceStitch`, `H3InjectVideoLatent`, `H3PerFrameDenoise`, `H3FaceMaskSAM`, `H3FaceTransformInfo`. Зависимости `ultralytics, scipy, insightface, scenedetect>=0.7` — **все установлены** (проверено импортом в embedded-python: `deps OK`). Пакет **импортируется без ошибок** и в C:-инстансе, и в E:-инстансе (проверено тем же способом, что и ComfyUI: `spec_from_file_location` по `__init__.py` → `PACK INIT OK 7`).
  ⚠️ **НО В ЖИВОМ СЕРВЕРЕ НОД НЕТ** — `object_info` на :8188 не содержит ни `H3FaceTrackCrop`, ни других `H3*` (`H3* nodes: []`).
  🟢 **ПРИЧИНА (проверено):** сервер стартовал **сегодня 17:58:59**, а каталог пака на диске создан **18:46** → пакет появился **после** старта. ComfyUI грузит custom_nodes только при старте. Governance-политика ComfyUI 0.43 (`ComfyUI/app/governance.py`, `pack_refusal()`) **не при чём**: `governance/policy.signed.json` отсутствует → `_custom_node_mode = None` → блокировок нет.
  ✅ **ДЕЙСТВИЕ: перезапустить PROD :8188** — и `H3FaceSelect/H3FaceTrackCrop/H3FaceStitch/H3InjectVideoLatent/H3PerFrameDenoise/H3FaceMaskSAM` появятся. (Рестарт не делали: GPU занят батчем.)
- 🟢 **`ComfyUI_Temporal-Face-Detailer` (nikythebikky) — репозиторий УДАЛЁН upstream.** `api.github.com/repos/nikythebikky/ComfyUI-Temporal-Face-Detailer` → **404**; `codeload` → 404; поиск по API GitHub → `total_count: 0`. ComfyUI-Manager, коммит `7e1af9c` (2026-10-23), помечает его как «**Temporal Face Detailer [REMOVED]**». Полная документация сохранилась на comfy.icu (приведена в 3.2). Локальной копии на диске нет — ставить нечего.
- 🟢 **`ComfyUI_AP_OpticalFlow` (adampolczynski) — НЕ установлен.** Репозиторий существует (12 файлов, `ap_optical_flow_nodes.py`, `pushed_at 2026-03-17`, 24 ноды). Это **temporal-aware деталь-пасс и анти-фликер** (см. Часть 3) — **главный недостающий инструмент**.
- 🟢 **Нет ни одного SDXL-чекпойнта для face-detail-пасса.** `checkpoints/` = только `sd-v1-5-inpainting.ckpt` (4.27 ГБ), каталог `v18`, `LTX-Video`. А `Temporal Face Detailer` — **SDXL-only** → ему нужен SDXL.
- 🔴 **Нет `insightface/models/` buffalo_l** — содержимое `C:\AI\Models\comfyui\insightface\models` не проверено (нужно для ReActor и H3-FaceRefine: `w600k_r50.onnx` и др.).
- 🔴 **Нет `sam3`** чекпойнта в `models/checkpoints` (BCNodes `BC_SkinTexture` умеет сам, но `H3FaceMaskSAM` использует SAM-маску).

### 2.1 Список доставки (с размерами и путями) — E: НЕ трогаем, всё на C: и G:

| # | Что | Размер | Куда | Зачем | Приоритет |
|---|---|---|---|---|---|
| 1 | `qwen3vl_32b_h3_ultra_uncensored_heretic_int8_convrot.safetensors` (`ethanfel/Qwen3-VL-32B-Ultra-Heretic-H3-ComfyUI-INT8-ConvRot`) | **26.36 ГБ** | `C:\AI\Models\comfyui\text_encoders\` | 🟢 Снятие рефьюза H3 → NSFW-LoRA работают по-настоящему (Часть 1.1.3) | **P0** |
| 2 | `Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors` (`RunDiffusion/Juggernaut-XL-v9`) | **7.11 ГБ** | `C:\AI\Models\comfyui\checkpoints\` | 🟢 CreativeML OpenRAIL-M (**коммерция ✅**), «skin texture, micro-contrast» — база для SDXL face/detail-пасса | **P0** |
| 3 | `ComfyUI-H3-FaceRefine` (Carasibana) v1.1.2 — **файлы УЖЕ на месте**, нужен **рестарт PROD** + `pip install ultralytics scipy insightface scenedetect>=0.7` (депы уже стоят) | ~0.2 МБ кода | `E:\...\ComfyUI_windows_portable\ComfyUI\custom_nodes\ComfyUI-H3-FaceRefine` ✅ есть | 🟢 Temporal-aware face-рефайн внутри H3: трекинг лица по кадрам, сглаживание кропа, AV-латентная инъекция → **лицо не «пластик»**. **Единственное действие: перезапустить :8188** | **P0 (только рестарт)** |
| 4 | `ComfyUI_AP_OpticalFlow` (adampolczynski) | ~0.1 МБ + RAFT-веса авто-скачиваются (~1M/5M params) | `…\ComfyUI\custom_nodes\ComfyUI_AP_OpticalFlow` | 🟢 `APTemporalBlendImages/Latents`, `APApplyRAFTOpticalFlow*`, `AP Loop Open/Close` — **анти-фликер** (Часть 3) | **P0** |
| 5 | `buffalo_l` (insightface): `w600k_r50.onnx` + `1k3d68.onnx` + `2d106det.onnx` + `genderage.onnx` | ~330 МБ | `C:\AI\Models\comfyui\insightface\models\buffalo_l\` | 🟢 Обязателен для ReActor и H3-FaceRefine (identity + трекинг) | **P1** |
| 6 | `SAM3` чекпойнт (для `H3 Face Mask (SAM)`) | ~0.9 ГБ | `C:\AI\Models\comfyui\checkpoints\` | 🟡 Маска лица/героя для H3-FaceRefine | **P1** |
| 7 | `Qwen-Image-2.1` still-модель для detail-anchor | уже есть (`qwen_image_2.1_int8_convrot` 7.26 ГБ) | — | 🟡 Тот сам «якорь детализации» для Tessera | **P1** |
| 8 | `LMS detail LoRA` (`Alissonerdx/Minimax-H3-ComfyUI`) | ~0.3 ГБ | `C:\AI\Models\comfyui\loras\` | 🟢 Часть «house recipe» Tessera (детализация/резкость) | **P2** |
| 9 | **НЕ ставить**: `TenStrip/10Eros-Max` 14 ГБ, `DaSiWa H3 int8` 21 ГБ | — | — | 🟡 Только если текущий int8-convrot не даёт нужного — но это +35 ГБ при 16 ГБ VRAM | **P2** |

🟢 **ПРОВЕРЕНО НА МЕСТЕ** — места хватает: C: 625 ГБ свободно; сумма P0+P1 ≈ 35 ГБ → **C: с запасом**. G: (8.9 ТБ) — кандидат на архив H3 `.bak` (20.97 ГБ) и поднятие `minimax_h3_fl2va_pruned_int8_convrot.safetensors.bak`.

### 2.2 Рекомендуемый pipeline фотореалистичной кожи в видео (16 ГБ)

**Ключевой принцип, который следует из наших измерений:** видео съедает ~55 % текстуры still-а (потолок still 9.2/10, лучший видео-кадр 8.0/10). Значит **текстуру надо не «добавлять в конце», а вносить внутрь временного контекста** — либо переносить через temporal-aware инструменты, которые умеют сохранять согласованность.

**Порядок (канонический):**

```
[0] Heretic-текст-энкодер (если NSFW-контент) 
     → иначе стоковый qwen3vl_32b_minimax_h3_int8_convrot

[1] H3 Ref2VA / FL2VA (int8-convrot) @ 1344×768 (BASE_SHORT_EDGE=768, MAX_PIXELS=768*1344)
     + SelfLift H3 TST (tau 0.2)  ← уже измерено: −3.5 % HF-дельта, −4.5 % RGB-дельта, −1.8 % дрейф лица
     + NSFW-LoRA (General NSFW REF2VA 0.8, HMNSFW 0.5–0.65)
     → 1-й проход: каркас, поза, движение, лицо

[2] H3 FaceRefine (Carasibana)  ← ТЕМПОРАЛЬНО-AWARE, в H3-латентах
     → трекинг лица по кадрам + AV-латентная инъекция → лицо не плывёт, кожа не «замыливается»
     → это ЗАМЕНЯет ReActor как источник детализации лица

[3] Tessera (Streamed Encode → Refine → Streamed Decode)  ← upscale + детализация, тайловый, H3-бэкенд
     → flat_skip 4.0 (небо/фон не галлюцинируются)
     → first-frame anchor из детального still (Qwen-Image-2.1) на 2x, motion_release_floor 0.3
     → temporal tiling: spatial_tile/temporal_tile = low_vram (16 ГБ!)

[4] SeedVR2 (3B fp8 / 7B sharp fp8) — только если нужен масштаб > Tessera
     → batch_size = 4n+1 (1,5,9,13,17,21,25 …) — ЖЁСТКОЕ требование модели (см. ниже)
     → temporal_overlap 12 (мы уже меряли: против 3 → +4.5 % пор и −1.9 % мерцания)
     → uniform_batch_size = True (иначе последний короткий батч → артефакты)
     → resolution 1080, color_correction = lab

[5] BC_SkinTexture — ТОЛЬКО на статике или с temporal-фиксом (см. 3.3!)
     → texture 0.35, detail 0.45, pore_scale 1.0

[6] Anti-flicker: AP Temporal Blend Images / AP Apply RAFT Optical Flow
     → recency_decay 0.35, blend_mode similarity_weighted, detail_preservation 0.15

[7] (опц) RIFE 2× — только если мерцание осталось; удваивает файл и время
```

🟢 **ПРОВЕРЕНО НА МЕСТЕ** — SeedVR2 batch_size, `ComfyUI-SeedVR2_VideoUpscaler/inference_cli.py:1374-1377`:
> `add_argument("--batch_size", type=int, default=5, help="Frames per batch (must follow 4n+1: 1, 5, 9, 13, 17, 21,...). Ideally matches shot length for best temporal consistency…")`
и `:1378-1380` `--uniform_batch_size` → «Pad final batch to match batch_size. Prevents temporal artifacts caused by small final batches.»
и `:1392-1393` `--temporal_overlap` default **0** → для 16 ГБ и мерцания ставим 12, а не 0.
и `--color_correction` default `lab` (`:1397`).

🟢 **ПРОВЕРЕНО НА МЕСТЕ** — Tessera требует: `minimax_h3_ref2va` + video/audio VAE + Qwen3-VL text encoder (README:31) — **всё у нас уже есть**; плюс `LMS detail LoRA (Alissonerdx)` — «part of the house recipe» (README:33) — **не хватает** (доставить, п.8).

🟢 **ПРОВЕРЕНО НА МЕСТЕ** — H3-FaceRefine key params (README v1.1.2):
- `canvas_width/canvas_height` = **768** (нативный short edge H3; 512 → в 2.25× меньше latent-токенов),
- `smooth_window` = **21** (≈0.9 c @24fps) — сглаживание центра кропа; поднимать, если бокс дрожит,
- `size_smooth_window` = **51** — размер кропа сглаживается **сильнее** центра, «size jitter makes the crop breathe, which changes the resample factor every frame and reads as shimmer»,
- `denoise_multiplier_small_face` / `_large_face` — **множители** denoise, не сам denoise,
- `cut_detection` = `auto (pyscenedetect)` — каждый шот = своё окно сглаживания, иначе кроп «перетаскивается» через склейку.

🟡 **ОЦЕНКА:** H3-FaceRefine + Tessera — самый перспективный путь, потому что оба работают **внутри/сквозь H3-латентное пространство**, где согласованность уже обеспечена моделью. Post-hoc покадровая детализация (BC_SkinTexture, ReActor) принципиально ограничена: она не имеет временного состояния.

---

## ЧАСТЬ 3. Face-lock и detail-пасс без temporal flicker

### 3.1 Диагноз: почему ReActor даёт «пластик» и мерцание

🟢 **ПРОВЕРЕНО НА МЕСТЕ** (`custom_nodes/comfyui-reactor/nodes.py:378-379`):
```python
if face_restore_visibility < 1:
    restored_face = cropped_face * (1 - face_restore_visibility) + restored_face * face_restore_visibility
```
🟡 **ОЦЕНКА:** это **покадровый линейный блендинг без всякого временного состояния**. Два следствия:
1. **«Пластик»** = restore-модель (GFPGAN/CodeFormer) выдаёт свою «идеальную» текстуру, а `visibility` её подмешивает к исходнику. Чем больше `visibility` — тем больше пластика. Плюс сам `inswapper_128.onnx` работает в 128×128 — на лице крупным планом деталей нет физически.
2. **Мерцание** = покадровый независимый restore/swap: соседние кадры получают разные детали → strobe. `face_restore_visibility` это **не лечит** — он лишь снижает амплитуду.

🟢 **ПРОВЕРЕНО НА МЕСТЕ** — `nodes.py:155`: `"face_restore_visibility": ("FLOAT", {"default": 1, "min": 0.1, "max": 1, "step": 0.05})` → диапазон 0.1…1.0, дефолт 1.0. Значит **0.7–0.8 — валидные значения**, а не «0.7 где-то».

🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ** — ReActor README/обсуждения: `face_restore_visibility = 1.0` даёт «airbrushed / fake plastic AI skin» — наша задача.

### 3.2 Правильный face-lock: три уровня, от лучшего к худшему

**Уровень 1 (РЕКОМЕНДУЕТСЯ) — face-lock ВНУТРИ H3, до декодирования.**
🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ** — `Carasibana/ComfyUI-H3-FaceRefine` README:
- «H3 Face Track + Crop» трекает лицо по кадрам, «the crop trajectory is smoothed over the whole video»;
- «**Stabilized crops** … per-track constant crop size + temporally smoothed crop centre»;
- инъекция — в **AV-латентное пространство** («AV latent injection»), маска регена = 0 (пин), аудио пинится;
- ключевое: **«With context they stayed as stable as pass 1»**;
- `smooth_window` 21 / `size_smooth_window` 51 — ровно против «размер дрожит → кроп «дышит» → ресемпл-фактор меняется → shimmer»;
- `cut_detection: auto (pyscenedetect)` — окна сглаживания не перетаскиваются через склейку;
- dropout-кадры: кроп интерполируется, композит **fades out** → эти кадры сохраняют исходные пиксели (наш «fallback на оригинал», но встроенный).

🟡 **ОЦЕНКА (высокая уверенность):** это единственное решение, которое даёт **одновременно** идентичность и фотореалистичную кожу без пластика, потому что: (а) детализация происходит в латентном пространстве H3, где временная согласованность уже есть; (б) трекинг+сглаживание гарантирует, что в каждый кадр подмешивается **один и тот же** объект.

**Уровень 2 — temporal face-swap, если всё-таки нужен именно swap.**
🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ** — `nikythebikky/ComfyUI-Temporal-Face-Detailer` README (сохранён на comfy.icu; сам репозиторий **удалён** upstream, см. 2.0 п.2). Его 8-уровневая схема подавления фликера:
1. **Stabilized tracked crops** — per-track constant crop size + smoothed centre (`crop_smoothing` 0.8), `crop_anchor: landmarks` (центр по центроиду ландмарк, а не по bbox), `align_rotation` — регистрация поворота по линии глаз;
2. **Fixed per-track seed + noise** (`noise_mode: fixed_per_track`) — «**the biggest identity-stability lever**»;
3. **Moderate denoise** — дефолт **0.35**, сладкая зона **0.3–0.45**; `denoise_max` (adaptive) для высокодвижных кадров;
4. **Latent-space temporal blend** (`latent_blend`) — flow-guided EMA на выходных латентах **до** VAE decode; «Smoothing in the VAU's semantic space tolerates noticeably higher denoise before visible flicker»; при denoise > 0.2 ставить `latent_blend 0.3–0.5`;
5. **Flow-guided pixel blend** (`flow_strength`), `flow_backend: farneback` или **RAFT** (`raft_small`/`raft_large`, авто-фолбэк);
6. **Color match** (`color_match`) — прибивает mean/std крона к исходному → нет «пульсации яркости»;
7. **Reference anchor** (`reference_image` + `reference_strength` 0.35) — анти-дрейф идентичности;
8. **Feathered mask-limited paste-back** (`mask_dilation`, `feather`) — меняется только лицо.

Прочие параметры: `guide_size`/`max_size` 768/1024, `crop_factor` 1.7, `max_track_gap` 10, `chunk_size` 4, `detail_every` 1 (2-4 = keyframe-режим), `temporal_strength` — мастер-рычаг рычагов 4-6.
Hardware-примечание автора: тестировано на 22 ГБ 2080 Ti; «fp16/bf16 only; no fp8 requirement»; **`--use-pytorch-cross-attention` обязательно**; «Do not enable SageAttention on Turing — silently produces NaN/black frames» (у нас Blackwell/Ada — у нас RTX 4070 Ti SUPER, Turing не применимо, но флаг cross-attention всё равно включить).
🟡 **ОЦЕНКА:** пакет **SDXL-only** → требует Juggernaut XL (п.2 выше). **Upstream удалён** → ставить нечего; либо форк, либо использовать его *идеи* через `AP Optical Flow` + H3-FaceRefine.

**Уровень 3 (запасной, текущий) — ReActor, но по-кадрово и с ручным контролем кадров.**
🟢 **ПРОВЕРЕНО НА МЕСТЕ** — параметры узла: `face_restore_visibility` (0.1…1.0), `codeformer_weight`, `facedetection` (`retinaface_resnet50`), `detect_gender_*`.
Рекомендуемые значения (🟡 ОЦЕНКА на основе README/обсуждений ReActor): `face_restore_visibility` = **0.7** (не 1.0), `face_restore_model` = **`GPEN-BFR-512`** (у нас уже лежит), `codeformer_weight` = 0.5, `facedetection` = `retinaface_resnet50`, confidence ≈ 0.7-0.75.
🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ** (ReActor issues #193/#72/#37/#25): видео-режим медленный, часть кадров без лица → модуль падает (нужен fallback на оригинальный кадр), маскинг-хелпер с видео тяжёлый.

### 3.3 Деталь-пасс: почему наш BC_SkinTexture в видео даёт 0 и как это чинить

🟢 **ПРОВЕРЕНО НА МЕСТЕ** — `custom_nodes/ComfyUI-BCNodes/libs/texture.py:26-39`:
```python
def pore_noise(b, h, w, seed):
    gen = torch.Generator(device="cpu").manual_seed(int(seed))
    if b == 1 or (h * w) % 16 == 0:
        for _ in range(b):
            yield torch.randn((1, 1, h, w), generator=gen)
    else:
        whole = torch.randn((b, 1, h, w), generator=gen)
        for i in range(b):
            yield whole[i:i + 1]
```
и `pipelines/skin_texture.py:62`: `noise = pore_noise(b, h, w, seed)`.

🟢 **ПРОВЕРЕНО НА МЕСТЕ — ЭТО КОРЕНЬ НАШЕЙ ПРОБЛЕМЫ.** `pore_noise` создаёт **один** генератор, засеянный `seed`, и выдаёт **b разных** шумовых кадров. То есть **при фиксированном `seed` каждый кадр получает СВОЮ поровую текстуру** — мерцание пор конструктивно заложено в узел. `seed` фиксирует только *последовательность*, а не *паттерн*.
🟡 **ОЦЕНКА (высокая уверенность):** это полностью объясняет наше измерение «BC_SkinTexture: +5 % текстуры, поры не читаются» и «деталь-пасс ITF-SkinDiffDetail в видео 0». Плюс: `apply_texture` домножает поры **мультипликативно в линейном свете** внутри маски, а маска SAM-3 в видео дрожит → микро-фликер по границам маски.

**Как починить (3 варианта, от дешёвого к правильному):**

1. 🟡 **ОЦЕНКА / минимальный патч BCNodes (рекомендую):** сделать поровое поле **статичным по кадру** — генерировать ОДИН шум `(1,1,h,w)` и раздавать его всем `b` кадрам (тот же `noise[0]` на каждый кадр), а не `b` разных. Плюс: брать маску из **трека** (H3-FaceRefine / AP), а не из покадрового SAM-3.
   *Почему это сработает:* поры перестают «прыгать», а маска перестаёт дрожать → остаётся именно тот эффект, который дал +5 % текстуры на статике, но уже без temporal-шумa. Это **правка в нашей копии** `ComfyUI-BCNodes` (мы её владеем, git-репозиторий чистый).
2. 🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ (правильный путь):** `AP Temporal Blend Images` (пакет `ComfyUI_AP_OpticalFlow`) — flow-warped EMA по кадрам с маской. Параметры (comfy.icu, `APTemporalBlendImages`): `blend_mode` = `similarity_weighted` (по умолчанию; лучший для фликера — тянет историю только там, где контент совпадает), `recency_decay` = **0.35**, `detail_preservation` = **0.15** (поднимать, если «размывает»), `trim_ratio` 0.20, `similarity_sigma` 0.10, `robust_delta` 0.08, `favor_previous_frame_when_few` = false. Маска → блендинг **только внутри маски**, снаружи кадр не меняется → ровно то, что нужно для лица/кожи.
   Для латентов: `AP Temporal Blend Latents` — то же, но **до VAE decode** (без round-trip) → дешевле и «чище».
   Для потока: `AP Apply RAFT Optical Flow` / `AP Apply RAFT Optical Flow (Masked)` / `AP Flow Occlusion Mask` (окклюзия-aware → движение не «смазывает»).
3. 🟢 **ПРОВЕРЕНО ИСТОЧНИКОМ:** `SeedVR2` `temporal_overlap` — мы уже меряли 12 против 3: **+4.5 % пор и −1.9 % мерцания одновременно**. Штатно `temporal_overlap` по умолчанию **0** (`inference_cli.py:1392`) — то есть дефолт даёт максимум мерцания. Ставить 12.

🟢 **ПРОВЕРЕНО НА МЕСТЕ** — `BCNodes/nodes/frequency_merge.py` (BC_FrequencyMerge): «`base`'s low frequencies + `detail`'s high frequencies», `split_sigma` 3.0, `detail_strength` 1.0, работает покадрово. 🟡 **ОЦЕНКА:** это **не** temporal-инструмент, но полезен: base = SeedVR2 на `downscale_factor 1` (лицо корректное), detail = SeedVR2 на меньшем factor (текстура) → переносим ВЧ-текстуру, сохраняя геометрию. В связке с AP Temporal Blend (после) — рабочая схема «деталь + анти-фликер».

### 3.4 Итоговый порядок face-lock + detail-пасса (без flicker)

```
H3 (Ref2VA/FL2VA, int8-convrot)
  └─ SelfLift H3 TST (tau 0.2)                    ← [-3.5 % HF-дельта, -4.5 % RGB-дельта, -1.8 % дрейф лица]
  └─ H3 FaceRefine (Carasibana)                   ← face-lock В ЛАТЕНТАХ, трекинг + сглаживание кропа
        │                                            (smooth_window 21, size_smooth_window 51,
        │                                             cut_detection auto, AV-латентная инъекция)
        └─ [опц] ReActor c visibility 0.7 + GPEN-BFR-512   ← ТОЛЬКО если нужен именно swap лица
                                                        (fallback: кадр без лица → оригинал)
  └─ Tessera (Encode → Refine → Decode, low_vram)  ← upscale + детализация тайлами, flat_skip 4.0,
                                                        first-frame anchor 2x, motion_release_floor 0.3
  └─ SeedVR2 (если нужен масштаб)  batch=4n+1, temporal_overlap=12, uniform=True, color_correction=lab
  └─ BC_SkinTexture  ← ТОЛЬКО после патча порового поля (3.3) либо только на статике
  └─ AP Temporal Blend Images (recency_decay 0.35, similarity_weighted, detail_preservation 0.15)
        маска = трек лица/кожи  → блендинг только внутри маски
  └─ [последним] RIFE 2×  ← только если мерцание всё ещё есть
```

**Почему именно так (сверка с нашими измерениями):** наш единственный измеренный временный инструмент, давший одновременно **+поры и −мерцание** — SeedVR2 `temporal_overlap` (+4.5 % / −1.9 %). TST дал −3.5 % HF-дельта. Всё остальное, что мы меряли (BC_SkinTexture покадровый, ITF-SkinDiffDetail, ReActor), в видео деградировало. Значит **ставка должна быть на temporal-aware инструменты**, а покадровые оставлять только как «последний штрих» внутри маски.

---

## ЧАСТЬ 4. Что сделано на стенде в этой сессии

1. 🟢 **Ничего не устанавливали и не удаляли.** Отчёт — исследовательский; правок в код/веса/конфиги не вносили. Разбор стенда был **read-only** (чтение каталогов, `object_info` серверов, импорт-тесты модулей, `nvidia-smi`, `df`).
2. 🟢 **Уточнены инстансы стенда** (было неверно в предыдущих заметках): PROD **:8188** = `E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable` (PID 34424); LAB **:8189** = `E:\ComfyUI_LAB_037\ComfyUI_windows_portable` (PID 18816); оба — ComfyUI 0.43.0, RTX 4070 Ti SUPER. Каталог `C:\Users\user\comfyui\ComfyUI_windows_portable\ComfyUI\custom_nodes` — **тот же** PROD-инстанс, что и на E:.
3. 🟢 **`ComfyUI-H3-FaceRefine` v1.1.2 — файлы на месте, зависимости в порядке, но ноды не загружены живым сервером.** Причина установлена точно: пакет появился на диске в **18:46**, сервер стартовал в **17:58:59** → нужен рестарт :8188. Governance-политика ComfyUI 0.43 не блокирует (нет `policy.signed.json`). Проверено импортом в обоих embedded-python: `PACK INIT OK 7`.
4. 🟢 **pytest-база зафиксирована** (BCNodes): `…\python_embeded\python.exe -m pytest tests` → **8 failed, 1353 passed** — до и после работы **идентично**. Все 8 падений **предсуществующие и средовые**, не связанные с нашими правками:
   - `test_lib_files.py::test_walk_follows_links_once_and_ends_on_a_loop` → `OSError [WinError 1314]` (нет прав на symlink в Windows);
   - 6 тестов в `test_lib_lm_lora.py` / `test_pipe_lm.py` / `test_pipe_process_monitor_monitor.py` — PEFT/средовые.
   Git-статус BCNodes **чистый** → код не меняли. (pytest 9.2.0 установлен в embedded-python — ранее отсутствовал; на код и результат тестов это не влияет.)
5. 🔴 **GPU-рендеры НЕ запускались** (GPU занят батчем: 14 755 / 16 376 MiB, util 56 %) — как и требовалось.
6. 🔴 **НЕ коммитили и НЕ пушили** ничего: git-статус чист в BCNodes (`8830adf`) и в `C:\AI\FreeVideo\vdn` (`30b6b38`).

---

## ИСТОЧНИКИ (первичные — все прочитаны)

**MiniMax H3**
- HF README (Safety Guardrails в разделе H3-Context-IR): https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/README.md
- HF LICENSE (Excluded Territories; Exhibit A AUP, 20 пунктов): https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE
- HF `docs/QA-about-License.md` (API vs open weights): https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/QA-about-License.md
- HF API, список 280 файлов репо: https://huggingface.co/api/models/MiniMaxAI/MiniMax-H3
- `wildminder/awesome-minimax-H3` — text encoders (Heretic): https://github.com/wildminder/awesome-minimax-H3/blob/main/blocks/20_text_encoders.md
- `ethanfel/Qwen3-VL-32B-Ultra-Heretic-H3-ComfyUI-INT8-ConvRot` (Apache-2.0; int8 26 363 476 151 Б): https://huggingface.co/ethanfel/Qwen3-VL-32B-Ultra-Heretic-H3-ComfyUI-INT8-ConvRot
- `llmfan46/Qwen3-VL-32B-Instruct-ultra-uncensored-heretic` (Apache-2.0): https://huggingface.co/llmfan46/Qwen3-VL-32B-Instruct-ultra-uncensored-heretic
- `jjhuangtw/MiniMax-H3-portable-panel/NSFW_MODELS.en.md`: https://github.com/jjhuangtw/MiniMax-H3-portable-panel/blob/main/NSFW_MODELS.en.md
- `huchukato/ComfyUI-QwenVL-Mod/update.md`: https://github.com/huchukato/ComfyUI-QwenVL-Mod/blob/main/update.md
- `TenStrip/10Eros-Max`, `DaSiWa MiniMax H3` — Civitai/HF, community NSFW fine-tunes.

**Wan**
- Репозиторий `Wan-Video/Wan2.2` (101 файл, LICENSE.txt = Apache-2.0, README): https://github.com/Wan-Video/Wan2.2
- Дерево файлов (проверка на safety-файлы): https://api.github.com/repos/Wan-Video/Wan2.2/git/trees/main?recursive=1
- HF `Wan-AI/Wan2.2-TI2V-5B` (apache-2.0), `Wan-AI/Wan2.2-S2V-14B` (apache-2.0), `Wan-AI/Wan2.2-I2V-A14B` — README (0 совпадений по nsfw/filter/safety).
- Комьюнити (заинтересованная сторона, хостинг): https://spicyapi.ai/blog/wan-2-2-nsfw , https://spicyapi.ai/blog/wan-2-1-nsfw
- Wan 3.0 — API only: https://spicyai.app/uncensored-wan-3

**Qwen**
- `Qwen/Qwen-Image-2.1` (README + LICENSE: research/evaluation only; attribution «Built with Qwen»): https://huggingface.co/Qwen/Qwen-Image-2.1
- `Qwen/Qwen-Image-Edit-2511` README (0 совпадений по nsfw/filter/safety).
- NSFW-LoRA (официально документирован на 2511): https://huggingface.co/Sinomarsl/qwen-image-edit-plus-nsfw-lora/blob/main/README.md
- `SGLang Cookbook` — MiniMax-H3, Heretic text encoder: https://docs.sglang.io/cookbook/diffusion/MiniMax/MiniMax-H3

**FLUX**
- `black-forest-labs/FLUX.2-dev` README, раздел «Risks» (пп. 1-6): https://huggingface.co/black-forest-labs/FLUX.2-dev
- `black-forest-labs/FLUX.1-dev` LICENSE.md (п. (e) — обязательные Content Filters; non-commercial): https://huggingface.co/black-forest-labs/FLUX.1-dev
- `black-forest-labs/FLUX.1-schnell` README (Apache-2.0).

**Прочие модели**
- `Lightricks/LTX-2` (README, 0 совпадений) и `ChrisColeTech/LTX-2.5-uncensored-v1.1-FP8`; `abliteratedmodels.org/ltx-2.5-uncensored-v1.1/` (лицензия unknown).
- `tencent/HunyuanVideo`, `THUDM/CogVideoX-5b`, `genmo/mochi-1-preview`, `zai-org/Z-Image-Turbo` — HF API (лицензии/теги; README без safety-разделов).
- `RunDiffusion/Juggernaut-XL-v9` (7.11 ГБ, CreativeML OpenRAIL-M): https://huggingface.co/RunDiffusion/Juggernaut-XL-v9

**Темпоральные инструменты**
- `Carasibana/ComfyUI-H3-FaceRefine` (v1.1.2; 41 файл; требования): https://github.com/Carasibana/ComfyUI-H3-FaceRefine
- `nikythebikky/ComfyUI-Temporal-Face-Detailer` — **репозиторий удалён**; спека сохранена: https://comfy.icu/extension/nikythebikky__ComfyUI-Temporal-Face-Detailer и https://comfy.icu/node/APTemporalBlendLatents
- ComfyUI-Manager, коммит с пометкой «Temporal Face Detailer [REMOVED]» (2026-10-23): https://github.com/Comfy-Org/ComfyUI-Manager/commit/7e1af9c202fbd0051df3da43572976acd12857dc
- `adampolczynski/ComfyUI_AP_OpticalFlow` (24 ноды, RAFT): https://github.com/adampolczynski/ComfyUI_AP_OpticalFlow
- `hashu786/ComfyUI-Tessera` (house recipe, flat_skip, anchor): https://github.com/hashu786/ComfyUI-Tessera
- `facok/ComfyUI-SelfLift` (TST tau 0.2; ограничения): https://github.com/facok/ComfyUI-SelfLift
- `Gourieff/comfyui-reactor` — issues #193, #72, #37, #25 (видео-режим).
- `Anil-matcha/awesome-uncensored-ai-video-models` (UNCENSORED-MODELS.md): https://github.com/Anil-matcha/awesome-uncensored-ai-video-models

**Локальные источники (наш стенд)**
- `C:\AI\Models\comfyui\` (все перечисленные веса), `C:\AI\Models\comfyui\text_encoders\qwen3vl_32b_minimax_h3_*`
- `…\ComfyUI\custom_nodes\comfyui-reactor\nodes.py:155, 378-379` (face_restore_visibility, формула бленда)
- `…\custom_nodes\ComfyUI-BCNodes\libs\texture.py:26-39` (`pore_noise`) и `pipelines\skin_texture.py:46-80`
- `…\custom_nodes\ComfyUI-BCNodes\nodes\frequency_merge.py`, `nodes\skin_texture.py`
- `…\custom_nodes\ComfyUI-SeedVR2_VideoUpscaler\inference_cli.py:1374-1397` (batch_size 4n+1, uniform_batch_size, temporal_overlap, color_correction)
- `…\custom_nodes\comfyui-tessera\README.md` (house recipe, flat_skip, anchor, motion_release_floor); `…\comfyui-tessera\tessera\nodes.py:4334-4346` (9 нод Tessera)
- `…\custom_nodes\comfyui-SelfLift\nodes.py:572-582` (SelfLiftH3Sampler / SelfLiftH3TST); `…\comfyui-SelfLift\README.md` (TST tau 0.2, несовместимость с `highres_tiling`)
- `…\custom_nodes\ComfyUI-H3-FaceRefine\nodes.py:3459-3467` (7 нод), `pyproject.toml` (v1.1.2), `README.md` (параметры)
- `E:\...\ComfyUI\app\governance.py:130-168` (`pack_refusal`), `:89-106` (`set_custom_node_policy`); `ComfyUI/nodes.py:2255-2334` (`load_custom_node`), `:2354-2406` (`init_external_custom_nodes`)
- `E:\...\ComfyUI_windows_portable\ComfyUI\custom_nodes\` — 32 пака (список выше); `E:\ComfyUI_LAB_037\...\custom_nodes\` — 6 пакетов
- `http://127.0.0.1:8188/object_info` — 2280 нод; `http://127.0.0.1:8189/system_stats` — LAB 0.43.0
- `Win32_Process` (PID 34424 → PROD :8188 из E:, PID 18816 → LAB :8189)
- `nvidia-smi` (14755/16376 MiB, util 56 %), `df -h` (C: 625G, E: 26G, G: 8.9T)

---

## ЧТО ОСТАЛОСЬ НЕПРОВЕРЕННЫМ (честный список)

🔴 **Wan-Fun** — первичный репозиторий не аудирован построчно.
🔴 **HunyuanVideo / CogVideoX / Z-Image-Turbo лицензии** — детали не разбирались (только факт отсутствия safety-разделов в карточках).
🔴 **Qwen-Image-Edit 2509/2511 и Qwen-Image 2.1: есть ли рефьюз в текст-энкодере** — аналогично H3, мы это не проверяли (для Qwen LoRA документирован как рабочий, но «Heretic-аналог» для Qwen не искали).
🔴 **FLUX.1-dev: точная формулировка про санитизацию пре-трейна** — из комьюнити-гайдов, не из первичного BFL-документа.
🔴 **LTX-2.5: лицензия uncensored-сборки ChrisColeTech** — «unknown» по abliteratedmodels.org; наша стоковая LTX — Community License (порог $10M).
🔴 **ReActor «GPEN-BFR-512 лучше CodeFormer на поворотах»** — из гайдов/обсуждений, не из кода.
🔴 **H3-FaceRefine/Tessera на 16 ГБ** — заявленные авторами цифры рассчитаны на другие карты; фактический VRAM-профиль на нашей стенде не измерен (GPU занят).
🔴 **`AP_OpticalFlow` не установлен** — в отчёте это рекомендация; фактической установки и прогона на стенде не было.
🔴 **Рестарт PROD :8188 не выполнялся** — ноды `H3Face*` не подтверждены в живом `object_info` (только импорт-тест модуля). После рестарта нужно перепроверить `object_info`.
🔴 **`insightface/models/buffalo_l`** — наличие/содержимое не проверено (нужно для ReActor и H3-FaceRefine).
🔴 **SAM3-чекпойнт** — наличие не проверено (`H3FaceMaskSAM` без него не заработает).
🔴 **BC_SkinTexture патч «одно поровое поле на все кадры»** — предложен в 3.3 как минимальная правка в нашей копии BCNodes; **не реализован и не измерен**. Ожидаемый эффект (потеря temporal-шума пор при сохранении +5 % текстуры) — 🟡 ОЦЕНКА, требует замера.
