# Cross-review: `scripts/pore_injector/`

Reviewed the neighbour files that existed at review time: `field.py`, `mask.py`,
and `__init__.py`. I did not modify them.

## Confirmed finding

### `mask.py:104-111` — small components bypass `largest_component_only`

The candidate is replaced by the largest label only when that label is at
least `min_component_fraction` of the image. Otherwise the original candidate
is returned unchanged, potentially with every connected component still
present. This contradicts the required largest-component behaviour.

I reproduced it with two separate 10x10 skin-coloured rectangles in a 200x200
RGB float image and `min_component_fraction=0.01`. The returned soft mask had
**2 components**, each 116 pixels after thresholding at 0.5, and 0.58% total
coverage. The largest-label selection must happen unconditionally; the minimum
area check should instead reject the mask or be applied separately.

## What was not present / not verified

- There was no CLI, video loop, Farneback warp, temporal low-pass, or metrics
  writer in the neighbour package at review time, so those requirements could
  not be checked or executed.
- In the three files reviewed I found no absolute path, GPU render, ComfyUI
  call, or isotropic-noise pore generator. `field.py` does construct discrete
  signed pits with rims and two populations.

## Risks in this implementation

- Farneback can lose tracking at scene cuts and in nearly flat regions; the
  report measures the resulting temporal deltas but does not reject bad clips.
- The required largest-component HSV prior intentionally excludes separate
  smaller skin regions. An external mask is available when that is undesirable.
- OpenCV `mp4v` preserves fps and frame geometry but does not copy source audio.
