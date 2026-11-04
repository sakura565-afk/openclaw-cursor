# -*- coding: utf-8 -*-
"""Sparse pore FIELD: discrete dark pits with a slightly brighter rim.

Why a field of pits and not a noise field
-----------------------------------------
An isotropic noise field adds ENERGY everywhere at the same statistical
weight. The eye integrates that into one flat "sand" impression, which is
what our first injector produced: +77% pore energy, and the eye called it
digital sand 9/10 of the time.

A pore is not energy, it is STRUCTURE with a signature:

  * it occupies a tiny fraction of the skin area (a few percent of pixels),
    so most of the skin stays untouched;
  * it is BIMODAL - a dark depression with a slightly brighter rim where the
    skin is stretched around the opening;
  * it is anisotropic and randomly oriented, like a real follicular opening;
  * its depth varies from pit to pit.

Those four properties are what let the eye group the marks into "surface
microrelief" instead of "sensor noise". A noise field has none of them.

The field is built ONCE for a whole clip (seeded) and then warped by optical
flow, so the same pit stays on the same patch of skin for the whole take.
Regenerating it per frame makes the pores crawl, which is the single most
visible artefact of the BCNodes-style approach.

Two scale bands
---------------
Like the BCNodes reference, the field has a fine and a coarse band. Here they
are not band-passed noise octaves but two populations of real pits:

  * fine  - small shallow pits, dense enough to read as skin grain;
  * coarse - larger deeper pits, sparse, the marks the eye reads as "pores".

The coarse band is what survives a slight blur and what makes the surface
read as skin rather than as a fine-grained filter.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

__all__ = ["build_pore_field", "PoreFieldStats"]


class PoreFieldStats(dict):
    """Small dict subclass so the stats travel with the field as plain data."""

    @property
    def pit_count(self) -> int:
        return int(self.get("pit_count", 0))


def _pit_patch(
    sigma_core: float,
    rim: float,
    rim_radius_mult: float = 1.6,
    rim_sigma_mult: float = 0.55,
    aniso: float = 1.0,
    rotation: float = 0.0,
) -> tuple[np.ndarray, int]:
    """One pit as a small patch: dark core plus a brighter rim.

    The profile is the difference between a narrow negative Gaussian (the
    depression) and a slightly wider positive ring (the stretched skin around
    the opening). A plain Gaussian blob would read as a smudge; the rim is
    what makes the mark look like an opening rather than a stain.

    The ring is deliberately NARROW and sits close to the core. An earlier
    version used a wide ring at 1.75x the core radius, and the rendered
    result was described by eye as "dirt and freckles": the wide halo looked
    like a smudge around a spot instead of the lit lip of a small pit. A
    tight rim keeps the whole mark about 3-4 px across, which is the size a
    real follicular opening occupies in the 3-12 px pore band.
    """
    rim_radius = rim_radius_mult * sigma_core
    rim_sigma = rim_sigma_mult * sigma_core
    reach = max(sigma_core, rim_radius + 2.0 * rim_sigma)
    radius = int(math.ceil(2.5 * reach))
    size = 2 * radius + 1

    yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1].astype(np.float32)
    cos_r = math.cos(rotation)
    sin_r = math.sin(rotation)
    rx = xx * cos_r - yy * sin_r
    ry = xx * sin_r + yy * cos_r
    # aniso >= 1 stretches the pit along its own axis (a real follicular
    # opening is never perfectly round).
    dist2 = (rx * rx) / max(aniso, 1e-6) + ry * ry
    dist = np.sqrt(dist2)

    core = np.exp(-dist2 / (2.0 * sigma_core * sigma_core))
    ring = np.exp(-((dist - rim_radius) ** 2) / (2.0 * rim_sigma * rim_sigma))
    patch = -core + rim * ring
    return patch.astype(np.float32), radius


def _splat_band(
    field: np.ndarray,
    rng: np.random.Generator,
    density_per_10k: float,
    sigma_core: float,
    rim: float,
    keep_probability: float,
    jitter: float = 0.45,
    depth_range: tuple[float, float] = (0.62, 1.0),
    sigma_range: tuple[float, float] = (0.90, 1.10),
    aniso_range: tuple[float, float] = (1.0, 1.18),
    cluster_strength: float = 0.55,
) -> int:
    """Scatter one population of pits over the field on a jittered lattice.

    A jittered lattice (rather than pure random placement) keeps the pits
    roughly evenly spread - real skin does not have clumps and bald patches -
    while the jitter breaks the regularity so the result does not read as a
    printed dot screen.

    The per-pit variation is deliberately TIGHT. An earlier version varied
    depth over 0.35..1.0 and size over 0.80..1.25, and the eye read the
    result as "dirt and freckles" rather than pores: real follicular openings
    are much more uniform than that. Uniformity is what makes the field read
    as one tissue rather than as random debris.

    ``cluster_strength`` breaks the uniformity at a LARGER scale: the keep
    probability of each site is modulated by a low-frequency random field, so
    pores gather into small groups with smoother areas in between. That is
    how real skin looks - follicular density is not constant over a body - and
    a perfectly even field is one of the cues that makes a synthetic texture
    read as printed-on.
    """
    height, width = field.shape
    if density_per_10k <= 0.0:
        return 0
    step = math.sqrt(10_000.0 / density_per_10k)
    if step < 2.0:
        step = 2.0

    # Low-frequency density modulation: smooth random field in 0..1. It is
    # mapped to [floor, 1] rather than to [0, 1]: an earlier version let the
    # modulation reach zero, and the eye then read the empty areas as bald
    # or airbrushed patches. Skin between follicular groups still carries
    # micro-relief, so the density never falls to nothing.
    if cluster_strength > 0.0:
        cell = max(step * 6.0, 24.0)
        coarse = rng.random((max(height // int(cell), 2), max(width // int(cell), 2))).astype(np.float32)
        coarse = cv2.resize(coarse, (width, height), interpolation=cv2.INTER_CUBIC)
        coarse = (coarse - coarse.min()) / max(coarse.max() - coarse.min(), 1e-6)
        floor = 1.0 - cluster_strength
        coarse = floor + (1.0 - floor) * coarse
    else:
        coarse = None

    count = 0
    rows = np.arange(0, height, step, dtype=np.float32)
    cols = np.arange(0, width, step, dtype=np.float32)
    if rows.size == 0 or cols.size == 0:
        return 0

    for y0 in rows:
        for x0 in cols:
            probability = keep_probability
            if coarse is not None:
                iy = int(min(max(round(float(y0)), 0), height - 1))
                ix = int(min(max(round(float(x0)), 0), width - 1))
                probability *= float(np.clip(coarse[iy, ix], 0.0, 1.0))
            if rng.random() > probability:
                continue
            cy = y0 + (rng.random() - 0.5) * step * jitter
            cx = x0 + (rng.random() - 0.5) * step * jitter
            iy = int(round(cy))
            ix = int(round(cx))
            if iy < 0 or ix < 0 or iy >= height or ix >= width:
                continue

            # Per-pit variation: depth, size, elongation and orientation.
            depth = float(rng.uniform(*depth_range))
            sigma = sigma_core * float(rng.uniform(*sigma_range))
            aniso = float(rng.uniform(*aniso_range))
            rotation = float(rng.uniform(0.0, math.pi))

            patch, radius = _pit_patch(sigma, rim, aniso=aniso, rotation=rotation)
            y_start, y_end = iy - radius, iy + radius + 1
            x_start, x_end = ix - radius, ix + radius + 1
            if y_start < 0 or x_start < 0 or y_end > height or x_end > width:
                continue  # skip pits that would be cut by the frame edge
            field[y_start:y_end, x_start:x_end] += depth * patch
            count += 1
    return count


def build_pore_field(
    shape: tuple[int, int],
    seed: int = 1234,
    pit_density: float = 260.0,
    rim: float = 0.45,
    pore_sigma: float = 1.1,
    two_octave: bool = True,
    fine_keep_probability: float = 0.85,
    coarse_density_ratio: float = 0.25,
    coarse_sigma_ratio: float = 2.4,
    coarse_keep_probability: float = 0.80,
    cluster_strength: float = 0.55,
) -> tuple[np.ndarray, PoreFieldStats]:
    """Build the single pore field for a whole clip.

    Parameters
    ----------
    shape : (height, width)
    seed : makes the field reproducible; the SAME seed must be used for every
        frame of a clip, which is the whole point of generating it once.
    pit_density : pits per 10 000 pixels for the fine band.
    rim : brightness of the ring around a pit relative to the pit depth.
    pore_sigma : core sigma of a fine pit, in pixels.
    two_octave : add the sparse coarse band on top of the fine band.

    The defaults were picked by looking at the result, in this order: a first
    pass was judged as "dirt and freckles" (too sparse, too varied in size),
    and a second pass was judged as "too strong" (individual dots dominating).
    The settled values give a dense, fine, low-contrast follicular texture.

    Returns
    -------
    (field, stats) - field is float32 in normalised units where a typical pit
    core is around -1.0, so ``--strength`` reads directly as "how dark a pit
    gets, as a fraction of the full 0..1 range".
    """
    height, width = int(shape[0]), int(shape[1])
    field = np.zeros((height, width), dtype=np.float32)
    rng = np.random.default_rng(seed)

    fine_pits = _splat_band(
        field,
        rng,
        density_per_10k=pit_density,
        sigma_core=pore_sigma,
        rim=rim,
        keep_probability=fine_keep_probability,
        cluster_strength=cluster_strength,
    )

    coarse_pits = 0
    if two_octave:
        coarse_pits = _splat_band(
            field,
            rng,
            density_per_10k=pit_density * coarse_density_ratio,
            sigma_core=pore_sigma * coarse_sigma_ratio,
            rim=rim * 0.85,
            keep_probability=coarse_keep_probability,
            jitter=0.40,
            cluster_strength=cluster_strength,
        )

    # Normalise so that a typical pit core is about -1.0. The 99th percentile
    # of |field| is used rather than the maximum, so one freak pit cannot
    # squash the whole field.
    #
    # The clip is also percentile-based, not a fixed +/-3. A fixed clip
    # flattened the deepest pit cores into flat-bottomed blobs, and the eye
    # read those as "dirt". Clipping at a percentile of the actual core
    # depths keeps every core a smooth bowl.
    magnitude = float(np.percentile(np.abs(field), 99.0))
    if magnitude > 1e-6:
        field /= magnitude
    core_depth = field[field < -0.5]
    if core_depth.size:
        limit = float(np.percentile(np.abs(core_depth), 99.0))
        if limit > 1e-6:
            limit = max(limit, 2.0)
            field = -limit * np.tanh(-field / limit)
    np.clip(field, -3.0, 3.0, out=field)

    non_zero = float(np.count_nonzero(np.abs(field) > 0.05)) / float(max(height * width, 1))
    stats = PoreFieldStats(
        {
            "seed": int(seed),
            "height": height,
            "width": width,
            "pit_count": int(fine_pits + coarse_pits),
            "fine_pits": int(fine_pits),
            "coarse_pits": int(coarse_pits),
            "pit_density_per_10k": float(pit_density),
            "pore_sigma_px": float(pore_sigma),
            "rim": float(rim),
            "two_octave": bool(two_octave),
            "coverage_fraction": non_zero,
            "field_std": float(np.std(field)),
            "field_min": float(np.min(field)),
            "field_max": float(np.max(field)),
        }
    )
    return field, stats
