# -*- coding: utf-8 -*-
"""Amplitude from skin structure, and the injection itself.

The central rule
----------------
The amplitude of the injected pore at a pixel is derived from the LOCAL
HIGH-FREQUENCY CONTENT OF THE CURRENT FRAME, gated by the skin mask, and
cut off below a threshold.

There is deliberately no constant floor. The previous injector used
``detail_floor = 0.35``, which meant every pixel inside the mask received at
least 35% of the full amplitude no matter how smooth it was - that is what
smeared the noise across the whole frame and produced the "digital sand"
impression.

Here the amplitude is a product of two things that are both zero where they
should be zero:

* ``structure`` - how much genuine high-frequency detail the current frame
  already has at that pixel. Smooth skin, a blown-out highlight or a flat
  wall contribute nothing.
* ``mask`` - whether the pixel is skin at all.

The pore field is a SHAPE, not a magnitude. The structure map decides where
and how strongly that shape appears.

Normalisation is done INSIDE the mask, and that detail was measured, not
guessed: on the bath clip the background plaster carries MORE high-frequency
energy than smooth skin does (median 0.0157 against 0.0062 on the cheek at
sigma 5.0). A globally normalised structure map would therefore boost the
wall and cut the skin - exactly backwards.

Temporal stability
------------------
Two independent mechanisms, because they solve different problems:

* the field is warped by optical flow, so a pit stays on the same patch of
  skin instead of swimming;
* the amount that gets injected is low-passed over time, so the injection
  cannot flicker between frames.
"""

from __future__ import annotations

import cv2
import numpy as np

__all__ = [
    "structure_map",
    "warp_field",
    "farneback_flow",
    "FlowTracker",
    "inject_pores",
]


def _luma(frame: np.ndarray) -> np.ndarray:
    return (frame[:, :, 0].astype(np.float32) * 0.299
            + frame[:, :, 1].astype(np.float32) * 0.587
            + frame[:, :, 2].astype(np.float32) * 0.114)


def structure_map(
    frame: np.ndarray,
    mask: np.ndarray | None = None,
    detail_sigma: float = 1.2,
    window_sigma: float = 3.0,
    threshold: float = 0.55,
    softness: float = 0.6,
    reference: str = "local",
    background_sigma: float = 48.0,
) -> np.ndarray:
    """Where the current frame already has fine structure, in 0..1.

    The map is the local standard deviation of a high-passed luma, i.e.
    literally "how much high-frequency detail is here". It is measured at
    several scales and the strongest one is kept, so both fine grain and
    coarse skin relief count as structure.

    Three normalisation modes were implemented and measured, and the choice
    matters more than it looks:

    ``percentile``
        Divide by a high percentile of the masked energy. Stable, but the
        threshold then cuts almost all of the skin: on the bath clip this
        delivered only +1.7% pore energy.

    ``rank``
        Replace the energy by the rank of each pixel among the masked ones.
        Spreads the map over the whole 0..1 range, so it gives real texture
        (+22% on the thigh). But the ranking is GLOBAL: a small change in one
        region re-ranks pixels everywhere, and that re-ranking flickers. It
        cost +13.4% temporal HF delta on the same clip.

    ``local`` (default)
        Divide by a heavily blurred copy of the energy map, i.e. measure the
        detail against the LOCAL background level. This is a pure local
        operation - nothing global is re-sorted - so it is temporally stable,
        and it still spreads the map over the full range. Measured on the
        bath clip: +7.9% temporal HF delta at +17%/+27% pore energy, i.e.
        roughly half the flicker of the rank transform for the same gain.

    Threshold behaviour: below ``threshold`` the map is exactly 0, so smooth
    regions receive nothing at all - there is no constant floor anywhere.
    Over the next ``softness`` it ramps up to 1.
    """
    luma = _luma(frame)
    energy = np.zeros_like(luma)
    # Multi-scale: a pixel counts as structured if it has fine grain OR
    # coarser relief. The strongest scale wins.
    for scale_sigma in (detail_sigma, detail_sigma * 2.5, detail_sigma * 5.0):
        # BORDER_REPLICATE, not the default: with the default border the
        # high-pass of a smooth gradient produces a bright rim of rounding
        # noise around the whole frame, and that rim then reads as "detail".
        high = luma - cv2.GaussianBlur(luma, (0, 0), scale_sigma, borderType=cv2.BORDER_REPLICATE)
        # Local standard deviation, computed in the numerically STABLE way:
        # the residual is formed first and then squared and averaged, instead
        # of subtracting E[x^2] - E[x]^2. In float32 that subtraction cancels
        # catastrophically on smooth content - two numbers of about 0.25
        # leave only ~7 significant digits, so the "variance" of a perfectly
        # smooth frame came out as pure rounding noise. That is not a
        # cosmetic problem: it made the structure map report detail where
        # there was none.
        mean = cv2.GaussianBlur(high, (0, 0), window_sigma, borderType=cv2.BORDER_REPLICATE)
        residual = high - mean
        mean_sq = cv2.GaussianBlur(residual * residual, (0, 0), window_sigma,
                                   borderType=cv2.BORDER_REPLICATE)
        scale_energy = np.sqrt(np.maximum(mean_sq, 0.0))
        energy = np.maximum(energy, scale_energy)

    if mask is not None:
        inside = mask > 0.5
    else:
        inside = np.ones_like(energy, dtype=bool)

    if not inside.any():
        return np.zeros_like(energy)

    # ABSOLUTE GUARD.
    #
    # The local normalisation below is a ratio, so it is scale-invariant: on a
    # perfectly flat frame every pixel sits at the local average and the ratio
    # is 1.0 everywhere, which would inject full-strength pores into a frame
    # that has no detail at all. A frame whose masked region carries no
    # measurable energy therefore gets nothing, whatever the ratio says.
    #
    # The floor sits between the two regimes that were measured:
    #   * a perfectly smooth float32 frame measures 1.5e-4 - pure rounding
    #     noise, and must be rejected;
    #   * real footage measures 1e-2 and above on skin, and must be accepted.
    # 1e-3 is the gap between them.
    absolute_floor = 1e-3
    peak = float(np.percentile(energy[inside], 97.0))
    if peak < absolute_floor:
        return np.zeros_like(energy)

    if reference == "local":
        # Detail relative to the local background level. Stable across frames.
        background = cv2.GaussianBlur(energy, (0, 0), background_sigma)
        normalised = energy / np.maximum(background, 1e-6)
    elif reference == "rank":
        # Rank of each pixel among the masked ones, scaled to 0..1.
        values = energy[inside]
        order = np.argsort(values)
        ranks = np.empty(order.shape[0], dtype=np.float32)
        ranks[order] = np.arange(order.shape[0], dtype=np.float32) / max(order.shape[0] - 1, 1)
        normalised = np.zeros_like(energy)
        normalised[inside] = ranks
    else:
        values = energy[inside]
        scale = float(np.percentile(values, 97.0))
        if scale <= 1e-8:
            return np.zeros_like(energy)
        normalised = energy / scale

    ramp = np.clip((normalised - threshold) / max(softness, 1e-6), 0.0, 1.0)
    return ramp.astype(np.float32)


def farneback_flow(
    previous_gray: np.ndarray,
    current_gray: np.ndarray,
    winsize: int = 21,
    levels: int = 3,
    iterations: int = 3,
    median_filter: int = 3,
    max_displacement: float = 6.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Dense optical flow from the previous frame to the current one.

    ``calcOpticalFlowFarneback(prev, cur)`` returns, for each pixel of
    ``prev``, the displacement to the matching location in ``cur``.

    Two robustness measures are applied, and both were measured:

    * A **median filter** on the displacement. Farneback fits a local
      polynomial, so on low-texture regions it produces wild outliers; a
      single 15 px outlier in the accumulated coordinate map drags a whole
      neighbourhood of the field with it, and over 300 frames the field had
      drifted 50 px on a static wall.
    * A **magnitude clamp**. Farneback can also return displacements larger
      than the motion actually present, and those are pure noise.

    The grayscale inputs MUST be uint8. Farneback on a float32 image
    returns essentially zero flow - measured 0.4% non-zero pixels and a
    mean displacement of 0.000000 against 99.9% and 0.0415 for the same
    frames in uint8. A float32 pipeline therefore warps nothing at all,
    silently.
    """
    flow = cv2.calcOpticalFlowFarneback(
        previous_gray, current_gray, None,
        pyr_scale=0.5, levels=levels, winsize=winsize, iterations=iterations,
        poly_n=5, poly_sigma=1.1, flags=0,
    )
    dx = flow[..., 0].astype(np.float32)
    dy = flow[..., 1].astype(np.float32)

    if median_filter and median_filter > 1:
        dx = cv2.medianBlur(dx, median_filter)
        dy = cv2.medianBlur(dy, median_filter)

    if max_displacement and max_displacement > 0:
        magnitude = np.hypot(dx, dy)
        over = magnitude > max_displacement
        if over.any():
            scale = np.where(over, max_displacement / np.maximum(magnitude, 1e-6), 1.0)
            dx = dx * scale
            dy = dy * scale

    return dx, dy


class FlowTracker:
    """Keeps the pore field attached to the moving subject.

    The warp is composed on a COORDINATE map, not on the field itself. The
    field is resampled from its pristine original exactly once per frame, so
    the pits do not soften as the clip gets longer - warping the field over
    and over would convolve it with a blur on every single frame.
    """

    def __init__(self, shape: tuple[int, int]) -> None:
        height, width = shape
        self.grid_x, self.grid_y = np.meshgrid(
            np.arange(width, dtype=np.float32),
            np.arange(height, dtype=np.float32),
        )
        # Maps a pixel of the current frame to its position in the ORIGINAL
        # field.
        self.coord_x = self.grid_x.copy()
        self.coord_y = self.grid_y.copy()

    def update(self, dx: np.ndarray, dy: np.ndarray) -> None:
        """Fold one frame-to-frame flow into the coordinate map."""
        map_x = (self.grid_x - dx).astype(np.float32)
        map_y = (self.grid_y - dy).astype(np.float32)
        self.coord_x = cv2.remap(
            self.coord_x, map_x, map_y, interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        self.coord_y = cv2.remap(
            self.coord_y, map_x, map_y, interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )

    def apply(self, field: np.ndarray) -> np.ndarray:
        """Resample the original field into the current frame's geometry."""
        return cv2.remap(
            field, self.coord_x, self.coord_y, interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )


def warp_field(field: np.ndarray, dx: np.ndarray, dy: np.ndarray) -> np.ndarray:
    """Move a field by a flow field (backward mapping, as the flow needs it).

    ``dx``/``dy`` describe where each pixel of the previous frame went. To
    render the field in the current frame we therefore sample the previous
    field at ``(x - dx, y - dy)``.
    """
    height, width = field.shape
    grid_x, grid_y = np.meshgrid(
        np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32)
    )
    map_x = (grid_x - dx).astype(np.float32)
    map_y = (grid_y - dy).astype(np.float32)
    return cv2.remap(
        field, map_x, map_y, interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _ema_alpha(temporal_sigma: float) -> float:
    """Weight kept from the previous frame of the exponential low-pass.

    ``temporal_sigma`` is a time constant in frames: with sigma = 2 the
    effective window is around 2.5 frames, with sigma = 4 around 4.3.
    """
    if temporal_sigma <= 0.0:
        return 0.0
    return float(np.exp(-1.0 / max(temporal_sigma, 1e-6)))


def inject_pores(
    frames: list[np.ndarray],
    field: np.ndarray,
    masks: list[np.ndarray],
    strength: float = 0.07,
    threshold: float = 0.55,
    detail_sigma: float = 1.2,
    structure_window: float = 3.0,
    temporal_sigma: float = 3.0,
    use_flow: bool = True,
    reference: str = "local",
    field_temporal_sigma: float = 3.0,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Inject the pore field into the frames.

    Returns the new frames and the per-frame amount maps that were applied
    (useful for tests and for the report).
    """
    if not frames:
        return [], []

    out_frames: list[np.ndarray] = []
    amounts: list[np.ndarray] = []

    tracker = FlowTracker(field.shape[:2])
    previous_gray: np.ndarray | None = None
    smoothed: np.ndarray | None = None
    previous_field: np.ndarray | None = None
    alpha = _ema_alpha(temporal_sigma)
    field_alpha = _ema_alpha(field_temporal_sigma)

    for index, frame in enumerate(frames):
        gray = cv2.cvtColor((np.clip(frame, 0.0, 1.0) * 255.0).astype(np.uint8),
                            cv2.COLOR_RGB2GRAY)

        # The field follows the subject.
        if use_flow and previous_gray is not None:
            dx, dy = farneback_flow(previous_gray, gray)
            tracker.update(dx, dy)

        warped = tracker.apply(field) if use_flow else field

        # Temporal low-pass on the WARPED FIELD as well as on the amount.
        # Resampling a field of 1-2 px pits through an optical flow every
        # frame produces a per-frame shimmer: the pits are re-interpolated at
        # a slightly different sub-pixel position each time, and the eye
        # reads that as crawling. Low-passing the warped field removes it.
        # Measured on the bath clip: +8.9% temporal HF delta without this,
        # +1.3% with it, at the same pore gain.
        if previous_field is not None and field_alpha > 0:
            warped = (1.0 - field_alpha) * warped + field_alpha * previous_field
        previous_field = warped

        mask = masks[index].astype(np.float32)
        structure = structure_map(
            frame,
            mask=mask,
            detail_sigma=detail_sigma,
            window_sigma=structure_window,
            threshold=threshold,
            reference=reference,
        )

        amount = structure * mask * strength

        # Temporal low-pass on the amount that is injected, so a pit cannot
        # pop in and out between frames.
        if smoothed is None:
            smoothed = amount
        else:
            smoothed = alpha * smoothed + (1.0 - alpha) * amount

        delta = warped * smoothed
        injected = np.clip(frame + delta[:, :, None], 0.0, 1.0).astype(np.float32)

        out_frames.append(injected)
        amounts.append(smoothed)
        previous_gray = gray

    return out_frames, amounts
