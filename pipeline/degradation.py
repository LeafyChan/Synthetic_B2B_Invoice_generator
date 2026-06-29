"""
pipeline/degradation.py
=======================
OpenCV Visual Degradation Pipeline — Tiered Edition

Three realism tiers, assigned per-document by the assembler:

    TIER 1  "clean"     — squeaky clean, print-quality, near-zero artifacts
    TIER 2  "degraded"  — visible but moderate: ink stains, light fading,
                          resolution loss, mild crumple, stamp bleed, tea stains,
                          low-ink streaky/patchy printer fade
    TIER 3  "heavy"     — aggressive degradation: strong stains, heavy
                          crumple/warp, low resolution, faded/patchy ink,
                          rotation, noise, stronger low-ink streaking — but
                          still OCR-readable by a competent model (never
                          destroys text legibility entirely)

Each tier maps to a pool of degradation profiles (same profile shape as
before: noise / fade / rotation / jpeg_q) PLUS new stochastic effects:
    - stain overlays (tea/coffee blotches, ink blots)
    - crumple/warp displacement
    - resolution downscale-then-upscale (simulates low-DPI scan)
    - low-ink streaky/patchy fade (simulates a printer running out of
      toner/ink — directional bands and patches of weak print, distinct
      from random pixel-level fading and from wet-ink bleed/blot stains)

All operations remain seeded per document index for reproducibility.

Usage
-----
    from pipeline.degradation import degrade_image, get_degradation_metadata, assign_tier

    tier = assign_tier(doc_index)                      # "clean" | "degraded" | "heavy"
    png_bytes = degrade_image(raw_png, doc_index, tier)
    meta = get_degradation_metadata(doc_index, tier)
"""

import random
import io
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("degradation")

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    log.warning("OpenCV (cv2) not available — degradation will be skipped.")

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ── Tier assignment ────────────────────────────────────────────────────────────
# 50% clean / 30% degraded / 20% heavy, deterministic per doc_index so re-runs
# are reproducible and resumable.
TIER_WEIGHTS = {"clean": 0.50, "degraded": 0.30, "heavy": 0.20}
_TIER_ORDER = ["clean", "degraded", "heavy"]
_TIER_CUTOFFS = [0.50, 0.80, 1.00]   # cumulative


def assign_tier(doc_index: int) -> str:
    """
    Deterministically assign a realism tier to a document index.
    Uses a separate RNG stream (offset salt) so tier assignment doesn't
    interfere with any other per-doc randomisation.
    """
    rng = random.Random(doc_index * 104729 + 17)   # distinct prime salt
    r = rng.random()
    for tier, cutoff in zip(_TIER_ORDER, _TIER_CUTOFFS):
        if r <= cutoff:
            return tier
    return "heavy"


# ── Degradation profiles per tier ─────────────────────────────────────────────
# Same shape as before, now with an added "low_ink_prob" / "low_ink_strength"
# pair controlling the new streaky/patchy printer-fade effect independently
# from the existing random-pixel "fade" parameter.

PROFILES_CLEAN = [
    {"name": "pristine",        "noise": 0.003, "fade": 0.00, "rotation": 0.05, "jpeg_q": 99,
     "stain_prob": 0.00, "crumple": 0.00, "downscale": 1.0, "low_ink_prob": 0.00, "low_ink_strength": 0.0},
    {"name": "clean_laser",     "noise": 0.008, "fade": 0.01, "rotation": 0.15, "jpeg_q": 97,
     "stain_prob": 0.00, "crumple": 0.00, "downscale": 1.0, "low_ink_prob": 0.00, "low_ink_strength": 0.0},
    {"name": "high_res_clean",  "noise": 0.005, "fade": 0.01, "rotation": 0.10, "jpeg_q": 98,
     "stain_prob": 0.00, "crumple": 0.00, "downscale": 1.0, "low_ink_prob": 0.00, "low_ink_strength": 0.0},
]

PROFILES_DEGRADED = [
    {"name": "worn_laser",      "noise": 0.035, "fade": 0.08, "rotation": 0.8,  "jpeg_q": 85,
     "stain_prob": 0.18, "crumple": 0.15, "downscale": 0.85, "low_ink_prob": 0.30, "low_ink_strength": 0.35},
    {"name": "inkjet_old",      "noise": 0.05,  "fade": 0.14, "rotation": 1.0,  "jpeg_q": 78,
     "stain_prob": 0.25, "crumple": 0.20, "downscale": 0.80, "low_ink_prob": 0.25, "low_ink_strength": 0.30},
    {"name": "low_ink",         "noise": 0.025, "fade": 0.22, "rotation": 0.5,  "jpeg_q": 82,
     "stain_prob": 0.15, "crumple": 0.10, "downscale": 0.88, "low_ink_prob": 0.65, "low_ink_strength": 0.55},
    {"name": "archive_scan",    "noise": 0.06,  "fade": 0.10, "rotation": 0.9,  "jpeg_q": 75,
     "stain_prob": 0.30, "crumple": 0.25, "downscale": 0.78, "low_ink_prob": 0.20, "low_ink_strength": 0.30},
    {"name": "office_copy",     "noise": 0.03,  "fade": 0.06, "rotation": 0.6,  "jpeg_q": 88,
     "stain_prob": 0.10, "crumple": 0.12, "downscale": 0.90, "low_ink_prob": 0.35, "low_ink_strength": 0.35},
    {"name": "streaky_toner",   "noise": 0.02,  "fade": 0.04, "rotation": 0.4,  "jpeg_q": 90,
     "stain_prob": 0.08, "crumple": 0.08, "downscale": 0.92, "low_ink_prob": 0.85, "low_ink_strength": 0.50},
]

PROFILES_HEAVY = [
    {"name": "fax_quality",     "noise": 0.11,  "fade": 0.22, "rotation": 2.2,  "jpeg_q": 55,
     "stain_prob": 0.55, "crumple": 0.55, "downscale": 0.55, "low_ink_prob": 0.40, "low_ink_strength": 0.55},
    {"name": "tea_stained",     "noise": 0.08,  "fade": 0.28, "rotation": 1.8,  "jpeg_q": 60,
     "stain_prob": 0.75, "crumple": 0.45, "downscale": 0.62, "low_ink_prob": 0.30, "low_ink_strength": 0.45},
    {"name": "crumpled_scan",   "noise": 0.07,  "fade": 0.20, "rotation": 2.5,  "jpeg_q": 58,
     "stain_prob": 0.40, "crumple": 0.70, "downscale": 0.60, "low_ink_prob": 0.35, "low_ink_strength": 0.50},
    {"name": "low_dpi_old",     "noise": 0.09,  "fade": 0.30, "rotation": 1.6,  "jpeg_q": 50,
     "stain_prob": 0.45, "crumple": 0.35, "downscale": 0.48, "low_ink_prob": 0.45, "low_ink_strength": 0.55},
    {"name": "ink_bleed_worn",  "noise": 0.10,  "fade": 0.32, "rotation": 2.0,  "jpeg_q": 52,
     "stain_prob": 0.60, "crumple": 0.50, "downscale": 0.55, "low_ink_prob": 0.35, "low_ink_strength": 0.50},
    {"name": "dying_cartridge", "noise": 0.06,  "fade": 0.18, "rotation": 1.4,  "jpeg_q": 65,
     "stain_prob": 0.20, "crumple": 0.30, "downscale": 0.65, "low_ink_prob": 0.90, "low_ink_strength": 0.75},
]

PROFILES_BY_TIER = {
    "clean": PROFILES_CLEAN,
    "degraded": PROFILES_DEGRADED,
    "heavy": PROFILES_HEAVY,
}

ALL_PROFILES = PROFILES_CLEAN + PROFILES_DEGRADED + PROFILES_HEAVY


# ── Core pixel-level operations ───────────────────────────────────────────────

def _add_gaussian_noise(img: np.ndarray, sigma: float, rng: np.random.Generator) -> np.ndarray:
    noise = rng.normal(0, sigma * 255, img.shape).astype(np.float32)
    noisy = img.astype(np.float32) + noise
    return np.clip(noisy, 0, 255).astype(np.uint8)


def _apply_fading(img: np.ndarray, fade: float, rng: np.random.Generator) -> np.ndarray:
    """Random pixel-level fade — scattered, not directional. Distinct from
    _apply_low_ink_fade below, which is banded/streaky and printer-shaped."""
    if fade <= 0:
        return img
    gray = np.mean(img, axis=2) if img.ndim == 3 else img
    dark_mask = gray < 180
    fade_mask = (rng.random(dark_mask.shape) < fade) & dark_mask
    result = img.copy().astype(np.float32)
    if img.ndim == 3:
        fade_mask_3d = np.stack([fade_mask] * 3, axis=2)
        result[fade_mask_3d] *= rng.uniform(1.3, 2.2)
    else:
        result[fade_mask] *= rng.uniform(1.3, 2.2)
    return np.clip(result, 0, 255).astype(np.uint8)


def _rotate_image(img: np.ndarray, max_angle: float, rng: np.random.Generator) -> np.ndarray:
    if max_angle <= 0:
        return img
    angle = rng.uniform(-max_angle, max_angle)
    h, w = img.shape[:2]
    cx, cy = w // 2, h // 2
    M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    cos_a = abs(M[0, 0])
    sin_a = abs(M[0, 1])
    new_w = int(h * sin_a + w * cos_a)
    new_h = int(h * cos_a + w * sin_a)
    M[0, 2] += (new_w - w) / 2
    M[1, 2] += (new_h - h) / 2
    background = int(rng.integers(245, 255))
    rotated = cv2.warpAffine(
        img, M, (new_w, new_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(background, background, background),
    )
    return rotated


def _adjust_brightness_contrast(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    alpha = rng.uniform(0.88, 1.12)
    beta = rng.uniform(-12, 12)
    adjusted = cv2.convertScaleAbs(img, alpha=alpha, beta=beta)
    return adjusted


def _jpeg_compress(img: np.ndarray, quality: int) -> np.ndarray:
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    _, buf = cv2.imencode(".jpg", img, encode_param)
    decoded = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return decoded


def _downscale_upscale(img: np.ndarray, factor: float) -> np.ndarray:
    if factor >= 0.999:
        return img
    h, w = img.shape[:2]
    small_w = max(1, int(w * factor))
    small_h = max(1, int(h * factor))
    small = cv2.resize(img, (small_w, small_h), interpolation=cv2.INTER_AREA)
    back = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return back


def _stain_color(rng: np.random.Generator, kind: str) -> tuple:
    if kind == "tea":
        return (int(rng.integers(60, 110)), int(rng.integers(110, 160)), int(rng.integers(150, 195)))
    if kind == "coffee":
        return (int(rng.integers(30, 70)), int(rng.integers(60, 100)), int(rng.integers(90, 130)))
    return (int(rng.integers(10, 40)), int(rng.integers(10, 35)), int(rng.integers(10, 30)))


def _apply_stains(img: np.ndarray, stain_prob: float, rng: np.random.Generator, intensity: float = 1.0) -> np.ndarray:
    if stain_prob <= 0 or rng.random() > stain_prob:
        return img

    h, w = img.shape[:2]
    overlay = img.copy()
    num_stains = int(rng.integers(1, 3 + int(2 * intensity)))

    for _ in range(num_stains):
        kind = rng.choice(["tea", "coffee", "ink"], p=[0.45, 0.35, 0.20])
        color = _stain_color(rng, kind)
        cx = int(rng.uniform(0.05, 0.95) * w)
        cy = int(rng.uniform(0.05, 0.95) * h)
        radius_x = int(rng.uniform(0.03, 0.12) * w * intensity)
        radius_y = int(rng.uniform(0.02, 0.09) * h * intensity)
        angle = rng.uniform(0, 180)

        mask = np.zeros((h, w), dtype=np.uint8)
        for _ in range(int(rng.integers(2, 5))):
            jitter_x = cx + int(rng.uniform(-radius_x * 0.4, radius_x * 0.4))
            jitter_y = cy + int(rng.uniform(-radius_y * 0.4, radius_y * 0.4))
            rx = max(3, int(radius_x * rng.uniform(0.6, 1.1)))
            ry = max(3, int(radius_y * rng.uniform(0.6, 1.1)))
            cv2.ellipse(mask, (jitter_x, jitter_y), (rx, ry), angle, 0, 360, 255, -1)

        blur_k = max(5, int(min(radius_x, radius_y) * 0.8) | 1)
        mask = cv2.GaussianBlur(mask, (blur_k, blur_k), 0)
        alpha = (mask.astype(np.float32) / 255.0) * rng.uniform(0.25, 0.55) * intensity
        alpha = np.clip(alpha, 0, 0.7)

        color_layer = np.zeros_like(overlay, dtype=np.float32)
        color_layer[:, :] = color
        alpha_3d = np.stack([alpha] * 3, axis=2)
        overlay = (overlay.astype(np.float32) * (1 - alpha_3d) + color_layer * alpha_3d).astype(np.uint8)

    return overlay


def _apply_crumple(img: np.ndarray, strength: float, rng: np.random.Generator) -> np.ndarray:
    if strength <= 0:
        return img

    h, w = img.shape[:2]
    grid_h, grid_w = 6, 5
    disp_x = rng.uniform(-1, 1, (grid_h, grid_w)).astype(np.float32) * strength * (w * 0.012)
    disp_y = rng.uniform(-1, 1, (grid_h, grid_w)).astype(np.float32) * strength * (h * 0.012)
    disp_x = cv2.resize(disp_x, (w, h), interpolation=cv2.INTER_CUBIC)
    disp_y = cv2.resize(disp_y, (w, h), interpolation=cv2.INTER_CUBIC)

    map_x, map_y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    map_x = (map_x + disp_x).astype(np.float32)
    map_y = (map_y + disp_y).astype(np.float32)

    warped = cv2.remap(
        img, map_x, map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )

    grad = cv2.Laplacian(disp_x + disp_y, cv2.CV_32F, ksize=5)
    shadow = np.clip(grad * strength * 1.5, -25, 25)
    shadow_3d = np.stack([shadow] * 3, axis=2)
    shaded = np.clip(warped.astype(np.float32) + shadow_3d, 0, 255).astype(np.uint8)

    return shaded


# ── New effect: low-ink streaky / patchy printer fade ─────────────────────────

def _apply_low_ink_fade(img: np.ndarray, strength: float, rng: np.random.Generator) -> np.ndarray:
    """
    Simulate a printer running low on toner/ink: dark (text) pixels are
    selectively lightened in BANDS and PATCHES, not uniformly at random.

    Two combined patterns, both common in real low-ink prints:
      1. Vertical/diagonal STREAKS — a few narrow low-frequency bands running
         roughly top-to-bottom (toner drum / inkjet head running dry along
         one pass), each band lightening dark pixels within its width.
      2. Soft blotchy PATCHES — large, low-frequency regions (Perlin-like via
         blurred random noise) of partial fade, simulating uneven toner
         distribution across the page.

    Only affects dark/text pixels (gray < 170) so background paper colour
    is untouched — this keeps the effect looking like ink starvation rather
    than a colour-cast filter.

    `strength` in [0, 1] controls how much of the dark-pixel intensity is
    lightened where the pattern is active.
    """
    if strength <= 0:
        return img

    h, w = img.shape[:2]
    gray = np.mean(img, axis=2) if img.ndim == 3 else img
    dark_mask = gray < 170

    # ---- Pattern 1: streaky bands ----
    num_streaks = int(rng.integers(2, 5))
    streak_field = np.zeros((h, w), dtype=np.float32)
    for _ in range(num_streaks):
        # Each streak is a soft vertical-ish band with slight diagonal drift
        center_x = rng.uniform(0, w)
        band_width = rng.uniform(0.04, 0.10) * w
        drift = rng.uniform(-0.15, 0.15) * w   # horizontal drift top->bottom
        xs = np.arange(w, dtype=np.float32)
        ys = np.arange(h, dtype=np.float32)
        # band center shifts linearly with y to create a slight diagonal streak
        center_at_y = center_x + drift * (ys / max(h, 1))
        dist = np.abs(xs[None, :] - center_at_y[:, None])
        band = np.clip(1.0 - dist / max(band_width, 1.0), 0, 1) ** 1.5
        streak_field = np.maximum(streak_field, band)

    # ---- Pattern 2: blotchy low-frequency patches ----
    patch_seed = rng.random((max(4, h // 80), max(4, w // 80))).astype(np.float32)
    patch_field = cv2.resize(patch_seed, (w, h), interpolation=cv2.INTER_CUBIC)
    patch_field = cv2.GaussianBlur(patch_field, (0, 0), sigmaX=max(w, h) * 0.04)
    p_min, p_max = patch_field.min(), patch_field.max()
    if p_max > p_min:
        patch_field = (patch_field - p_min) / (p_max - p_min)
    patch_field = np.clip(patch_field - 0.35, 0, 1) / 0.65  # keep top ~65% as fade zones

    combined = np.clip(0.65 * streak_field + 0.55 * patch_field, 0, 1) * strength

    # Blend dark (text) pixels toward white in proportion to `combined`,
    # rather than multiplying — multiplying near-black values by a small
    # factor barely changes them, which made earlier iterations of this
    # effect nearly invisible. Lerp-to-white actually lightens the ink.
    result = img.copy().astype(np.float32)
    fade_amount = np.clip(combined * dark_mask.astype(np.float32) * 1.3, 0, 1)
    white = np.full_like(result, 250.0)
    if img.ndim == 3:
        fade_3d = np.stack([fade_amount] * 3, axis=2)
        result = result * (1 - fade_3d) + white * fade_3d
    else:
        result = result * (1 - fade_amount) + white[..., 0] * fade_amount

    return np.clip(result, 0, 255).astype(np.uint8)


# ── Main entry point ──────────────────────────────────────────────────────────

def degrade_image(
    image_bytes: bytes,
    doc_index: int,
    tier: str = None,
    apply_degradation: bool = True,
) -> bytes:
    """
    Apply the full tiered degradation pipeline to a PNG image.

    Parameters
    ----------
    image_bytes        : raw PNG bytes from Playwright
    doc_index          : document index (used as RNG seed)
    tier                : "clean" | "degraded" | "heavy". If None, derived
                          deterministically from doc_index via assign_tier().
    apply_degradation   : if False, return original bytes unchanged

    Returns
    -------
    PNG bytes of the degraded image
    """
    if not apply_degradation or not CV2_AVAILABLE:
        return image_bytes

    if tier is None:
        tier = assign_tier(doc_index)

    rng = np.random.default_rng(seed=doc_index * 7919)

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        log.warning(f"[{doc_index}] Could not decode image for degradation")
        return image_bytes

    profiles = PROFILES_BY_TIER.get(tier, PROFILES_CLEAN)
    profile = profiles[doc_index % len(profiles)]

    intensity = {"clean": 0.0, "degraded": 1.0, "heavy": 1.6}.get(tier, 1.0)

    # ── Step 1: Gaussian scanner noise ───────────────────────────────────────
    img = _add_gaussian_noise(img, profile["noise"], rng)

    # ── Step 2: Random pixel-level fading ────────────────────────────────────
    img = _apply_fading(img, profile["fade"], rng)

    # ── Step 3: Brightness / contrast ────────────────────────────────────────
    img = _adjust_brightness_contrast(img, rng)

    # ── Step 4: Low-ink streaky/patchy printer fade ──────────────────────────
    low_ink_prob = profile.get("low_ink_prob", 0)
    if low_ink_prob > 0 and rng.random() < low_ink_prob:
        img = _apply_low_ink_fade(img, profile.get("low_ink_strength", 0.0), rng)

    # ── Step 5: Stains (tea / coffee / ink) ──────────────────────────────────
    if profile.get("stain_prob", 0) > 0:
        img = _apply_stains(img, profile["stain_prob"], rng, intensity=intensity)

    # ── Step 6: Crumple / wrinkle warp ───────────────────────────────────────
    if profile.get("crumple", 0) > 0:
        img = _apply_crumple(img, profile["crumple"], rng)

    # ── Step 7: Resolution downscale/upscale (low-DPI simulation) ────────────
    if profile.get("downscale", 1.0) < 1.0:
        img = _downscale_upscale(img, profile["downscale"])

    # ── Step 8: Rotation ──────────────────────────────────────────────────────
    img = _rotate_image(img, profile["rotation"], rng)

    # ── Step 9: JPEG re-compression ───────────────────────────────────────────
    if profile["jpeg_q"] < 95:
        img = _jpeg_compress(img, profile["jpeg_q"])

    # ── Step 10: Encode back to PNG ───────────────────────────────────────────
    _, out_buf = cv2.imencode(".png", img)
    return out_buf.tobytes()


def get_degradation_metadata(doc_index: int, tier: str = None) -> dict:
    """Return the degradation profile + tier applied to a given document index."""
    if tier is None:
        tier = assign_tier(doc_index)
    profiles = PROFILES_BY_TIER.get(tier, PROFILES_CLEAN)
    profile = profiles[doc_index % len(profiles)]
    return {
        "tier": tier,
        "profile_name": profile["name"],
        "noise_sigma": profile["noise"],
        "fade_factor": profile["fade"],
        "max_rotation_deg": profile["rotation"],
        "jpeg_quality": profile["jpeg_q"],
        "stain_prob": profile.get("stain_prob", 0.0),
        "crumple_strength": profile.get("crumple", 0.0),
        "downscale_factor": profile.get("downscale", 1.0),
        "low_ink_prob": profile.get("low_ink_prob", 0.0),
        "low_ink_strength": profile.get("low_ink_strength", 0.0),
    }