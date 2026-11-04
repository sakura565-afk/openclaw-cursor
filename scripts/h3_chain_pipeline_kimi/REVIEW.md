# Cross-review: `scripts/h3_chain_pipeline/`

Reviewed the neighboring implementation read-only. No ComfyUI/GPU calls were made. Findings below are against the working-tree files present at review time.

## 1. Critical: seam frames are deleted before they are measured

- `assemble.py:218-240` extracts frames inside `TemporaryDirectory()` and returns their paths after leaving the context manager.
- `assemble.py:269-276` then opens those paths in `patch()` / `pore_energy()`.

On Windows (and on POSIX), leaving the context deletes the directory, so any two-chunk run reaches `Image.open()` with nonexistent paths. This prevents the required seam metrics and can abort after the final MP4 was muxed but before the report/state assembly is written.

I reproduced this locally with two synthetic five-frame MP4s, without H3:

```text
seam_metrics([v0, v1]) -> FileNotFoundError: ... h3chain_seam_.../f_00005.png
```

The frames must be read before leaving the temporary-directory scope, or the scope must cover the entire measurement.

## 2. High: resume reuse ignores `--seed-base` and other effective render settings

- `state.py:155-169` defines `accepted_for(index, prompt_digest)` and filters only index, prompt hash, accepted flag, and video existence. Although `ChunkRecord.key` contains a seed, the lookup does not compare it with the planned seed.
- `pipeline.py:345-367` uses exactly that lookup and reuses the returned record.

Consequently, changing `--seed-base` still reuses the old accepted take. The same cache also has no width/height/steps/chunk-length/input-image/audio fingerprint, so those changes can mix incompatible renders instead of invalidating state. This contradicts the per-chunk seed plan and makes resumed output depend on stale settings. The lookup needs the expected seed and a job/configuration fingerprint (or explicit invalidation of all affected downstream chunks).

## 3. High: missing texture measurements pass an enabled texture gate

- `gates.py:276-281` checks the minimum only when `metrics.texture_ratio > 0`.
- `gates.py:231-235` sets pore energy to zero when the texture metric is unavailable; a zero baseline/measurement also leaves the ratio at zero.

Thus `--min-texture-ratio 0.75` does not reject a chunk whose texture metric is zero or unavailable. Offline reproduction:

```text
evaluate_chunk(ChunkMetrics(face_drift_last=1, face_drift_mean=1,
                            pore=0, texture_ratio=0), 8, 0.75)
=> accepted=True, reason="within the limits"
```

An enabled texture gate should fail closed (or return an explicit `not_measured` status), not silently accept the chunk.

## Checked and not found

- I did not find a full-path `LoadImage` mistake in the neighbor: `render.py` copies the anchor into the input directory and passes its basename.
- I did not find use of the short-lived middle guide-frame approach; the renderer requests use the chained first image.
- The video concat path drops frame zero of every later chunk, and the audio graph uses the valid `qsin` equal-power curve.
- The repository absolute-path guard passed with the neighbor files present.
- I did not run real H3/ComfyUI jobs, so visual identity, actual server compatibility, long audio sync, and GPU retry behavior remain unverified for both implementations.

## Risks retained in my implementation

My metric gate is a heuristic: pore energy is not a pore classifier, and fixed-ROI face drift also responds to pose and lighting. Real H3 output and threshold suitability were not tested. State files are atomic but not locked against concurrent writers. These limits are documented in `README.md`.
