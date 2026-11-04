# H3 long-video chain (Kimi implementation)

Resumable CLI for long H3 clips. It renders independent chunks through the existing `scripts/h3_i2v_run.py`; it does not modify or import the renderer. The renderer still owns workflow validation and the H3 defaults.

## Why last-frame chaining, not a middle guide frame

A guide frame placed in the middle of a long H3 shot held only about two frames in our measurements: VAE smoothing removes the constraint, and the tail drifts again. Here every chunk starts from the actual last frame of the selected previous chunk. That puts an identity anchor at every join instead of hoping a temporary mid-shot constraint survives.

The first chunk uses `--image`. Each later chunk uses a PNG extracted from the preceding chunk and copied into ComfyUI's `input` directory. Only the PNG **basename** is passed to `h3_i2v_run.py`; its `LoadImage` validator rejects absolute paths. Every retry of the same chunk uses the same incoming anchor, so a bad take cannot contaminate its own retry. The prompt file is passed unchanged to every attempt.

The implementation intentionally does not pass `--guide-image` and does not override the renderer's denoise default. A low denoise is not a conservative face lock in this workflow: measured H3 denoise `0.25` corresponds to effective sigma `0.800`, so the supplied frame is overwritten and identity/texture can drift. Denoise `1.0` follows the normal i2v generation schedule; lowering it while expecting stronger anchoring has the opposite practical effect here. `fl2va` is the checkpoint intended to land on a supplied frame; `ref2va` only nudges toward it.

## Offline plan

```bash
.venv/Scripts/python.exe -m scripts.h3_chain_pipeline_kimi \
  --image portrait.png --prompt-file prompt.txt --audio track.m4a \
  --output long.mp4 --chunk-frames 56 --total-frames 166 \
  --seed-base 42 --plan
```

Для 56/166 это три чанка: `56 + 55 + 55 = 166`. Первый кадр каждого следующего MP4 — переснятая копия предыдущего хвоста. В финал входят 56 кадров первого чанка и по 55 из каждого следующего. Для `N > 1` полных чанков можно задать `--total-frames $((56 + (N - 1) * 55))`; последний чанк дополнительно обрезается при необходимости.

Seed первой попытки чанка `i`: `seed_base + i * 1_000_000`; попытка `a` добавляет `a`. `--plan` не читает медиа, не создаёт state, не вызывает ffmpeg и не обращается к ComfyUI.

## Render and resume

Готовая команда для трёх чанков (166 кадров). Она **не выполнялась** при разработке и требует работающий H3/ComfyUI:

```bash
export COMFYUI_PROD_DIR=/path/to/ComfyUI_windows_portable
export COMFYUI_PROD_URL=http://127.0.0.1:8188

.venv/Scripts/python.exe -m scripts.h3_chain_pipeline_kimi \
  --image "$INPUT_DIR/portrait.png" \
  --prompt-file "$INPUT_DIR/prompt.txt" \
  --audio "$INPUT_DIR/track.m4a" \
  --output "$OUTPUT_DIR/long.mp4" \
  --chunk-frames 56 --total-frames 166 \
  --seed-base 42 --max-retries 2 \
  --tail-frames 5 --max-face-drift 8.0 \
  --min-texture 0 --max-texture-drop 0.25
```

`COMFYUI_PROD_DIR` указывает на portable root с `ComfyUI/input`; иначе каталог разрешается через `scripts/local_paths.py`. URL берётся из `COMFYUI_PROD_URL`; конвейерный `--server URL` лишь задаёт эту переменную дочернему процессу. Несуществующие у рендерера флаги `--server` и `--audio-offset` ему **не передаются**. В `LoadImage` уходит только basename после копирования в input.

Обычный `run` автоматически делает рендер с гейтом, перерендер только непрошедшего чанка и сборку MP4/звука/отчёта. После аварии повторяется та же команда: `selected` chunks с существующим video не рендерятся заново. Отсутствующий last-frame PNG восстанавливается без рендера; отсутствующее selected video инвалидирует этот и downstream chunks.

Seed/config и SHA-256 image/prompt/audio входят в fingerprint. Смена `--seed-base` (или другой части fingerprint) автоматически создаёт новый state вместо reuse принятых старых чанков. State schema version 1 несовместим с версией 2 — для старого файла нужен новый output/state path.

По умолчанию рядом с `--output` создаются скрытый `.OUTPUT_STEM.h3-chain/`, `OUTPUT.state.json` и `OUTPUT.report.json`. State атомарно обновляется после каждой попытки, но не блокируется от одновременных writers: у job должен быть один активный процесс.

## Quality gate and retry algorithm

Для каждой попытки извлекаются 24 равномерно распределённых кадра (включая первый и последний). Канонический `compare_video_texture.measure` считает `face_drift` относительно первого кадра и pore energy на одном масштабе. Отдельно извлекаются последние `--tail-frames` **подряд** (по умолчанию 5): `face_drift_tail` — средний дрейф хвоста относительно первого кадра, `face_drift_last` — максимум в хвосте.

Для chunk `i` текстурный floor:

```text
max(--min-texture,
    median(положительные pore ранее selected chunks) * (1 - --max-texture-drop))
```

Попытка отклоняется, если:

1. mean `face_drift` **или** `face_drift_tail` превышает `--max-face-drift`;
2. drift/pore не измерены или не finite, измерено меньше двух кадров либо нет tail frames;
3. pore energy `<= 0` — это отказ даже при `--min-texture 0`, а не пропуск гейта;
4. положительная pore energy ниже текущего texture floor.

При отказе перерендеривается **только этот чанк** от того же входного anchor с `seed = seed_base + chunk_index * 1_000_000 + attempt`. Принятые предыдущие чанки не трогаются. Всего доступно `1 + max_retries` попыток; успешная сразу становится selected.

Если все попытки плохие, выбирается лучшая по сумме нормированных нарушений (mean drift + tail drift + texture deficit), затем по меньшему tail/mean drift, большей pore и меньшему attempt. Неизмеримые значения получают большой штраф. Итог всё равно собирается как best effort, но chunk и `summary.quality_gate_passed` остаются false, а CLI возвращает **3**. Полностью принятый run возвращает 0; input/render/assembly errors — 2.

## Assembly and report

- Chunk zero keeps all frames. Each later chunk drops frame zero with exact ffmpeg `trim=start_frame`; the final segment is trimmed to the requested `total_frames`.
- `h3_i2v_run.py` всегда мюксит дорожку с нулевой секунды (`-map 1:a:0`), поэтому chunk audio для финала игнорируется. Сборщик открывает **один исходный `--audio`** и делает `atrim=start=...:end=...` по накопленной длине retained frames.
- На каждом стыке обе стороны содержат один и тот же исходный участок длиной 40 мс и соединяются `acrossfade=d=0.04:c1=qsin:c2=qsin` (equal-power). Overlap не сдвигает последующие аудиособытия. WAV мюксится в итог как AAC.
- Fake-renderer тест специально создаёт MP4 со звуком «каждый с нуля». Численная проверка WAV click-track подтверждает исходные времена щелчков в пределах 3 мс и отсутствие новых щелчков после стыка. Другой тест проверяет exact frame count и байты последних N кадров.
- Seams compare the previous chunk's last frame with the next chunk's first **retained** frame. The report includes RGB/face deltas, before/after pore energy, texture ratio/drop, and a diagnostic `texture_ratchet` flag; seam metrics do not form an additional gate.

## Optional identity LoRA

`--identity-lora character_swap` добавляет каждому чанку один и тот же исходный reference через `--ref-image` и `minimax_h3_character_swap_v1.1_ref2va_fro.safetensors` со strength model/clip `1.0`. `--image` по-прежнему меняется на хвост предыдущего чанка, а identity reference остаётся исходным портретом. Seed закреплён за chunk/attempt; retry намеренно получает новый seed.

Выбран `character_swap`, а не `head_swap`: документация `head_swap` требует aligned source video в guide-канале, которого у image-to-video чейнинга нет. Опция экспериментальная: `--ref-image` переключает renderer в REF2VA, поэтому преимущество FL2VA для текстуры может быть потеряно. LoRA должна быть реально скачана в ComfyUI. Качество identity и совместимость связки на GPU здесь **не проверялись**; гарантии нет. По умолчанию опция `none`.

State schema version 2 contains top-level `schema_version`, `fingerprint`, full `config`, and `chunks`. A selected chunk entry looks like:

```json
{
  "index": 1,
  "initial_seed": 1000042,
  "render_frames": 56,
  "kept_frames": 55,
  "output_start": 56,
  "status": "selected",
  "anchor_name": "h3chain_job_anchor_001.png",
  "attempts": [
    {
      "attempt": 0,
      "seed": 1000042,
      "image_name": "h3chain_job_anchor_001.png",
      "video": "work/chunk_001/attempt_000/chunk_1_0.mp4",
      "texture_floor": 75.0,
      "accepted": false,
      "status": "rejected",
      "metrics": {"face_drift": 12.0, "face_drift_tail": 18.0, "face_drift_last": 20.0, "tail_frames": 5, "pore": 100.0, "frames": 24},
      "reasons": ["mean face_drift 12 exceeds 8", "tail face_drift 18 exceeds 8"]
    }
  ],
  "selected_attempt": 1,
  "selected_seed": 1000043,
  "gate_passed": true,
  "metrics": {"face_drift": 2.0, "face_drift_tail": 3.0, "face_drift_last": 4.0, "tail_frames": 5, "pore": 110.0, "frames": 24},
  "video": "work/chunk_001/attempt_001/chunk_1_1.mp4",
  "last_frame": "work/anchors/chunk_001_last.png"
}
```

The report repeats these chunk records and adds `seams` plus summary mean/max for both full-sample and tail drift, worst-drift chunks, pore statistics, render/retry counts, and texture-ratchet seam count.

## Tests and remaining validation boundary

```bash
.venv/Scripts/python.exe -m pytest -q scripts/h3_chain_pipeline_kimi/test_pipeline.py
.venv/Scripts/python.exe -m pytest -q tests/test_no_absolute_paths.py
.venv/Scripts/python.exe -m pytest -q
```

Tests cover planning, real renderer `--help` compatibility, mean/tail gate, fail-closed texture, retry/best-effort, seed invalidation, resume/anchor repair, exact tail extraction, and real local ffmpeg assembly with a numerical click-track check. They never contact ComfyUI.

Not verified without a real GPU: actual H3 rendering, queue/VRAM behavior, FL2VA/REF2VA quality, availability/effect of `character_swap`, visual identity and metric thresholds on real portraits, and long-run AV sync after AAC. Pore energy is a texture heuristic, not a pore classifier; fixed-ROI drift also responds to pose and lighting, not identity alone.
