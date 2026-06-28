"""
pipeline/degradation.py
=======================
OpenCV Visual Degradation Pipeline — Tiered Edition

Three realism tiers, assigned per-document by the assembler:

    TIER 1  "clean"     — squeaky clean, print-quality, near-zero artifacts
    TIER 2  "degraded"  — visible but moderate: ink stains, light fading,
                          resolution loss, mild crumple, stamp bleed, tea stains
    TIER 3  "heavy"     — aggressive degradation: strong stains, heavy
                          crumple/warp, low resolution, faded/patchy ink,
                          rotation, noise — but still OCR-readable by a
                          competent model (never destroys text legibility
                          entirely)

Each tier maps to a pool of degradation profiles (same profile shape as
before: noise / fade / rotation / jpeg_q) PLUS new stochastic effects:
    - stain overlays (tea/coffee blotches, ink blots)
    - crumple/warp displacement
    - resolution downscale-then-upscale (simulates low-DPI scan)

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
# Same shape as the original PROFILES list, now grouped by tier.

PROFILES_CLEAN = [
    {"name": "pristine",        "noise": 0.003, "fade": 0.00, "rotation": 0.05, "jpeg_q": 99,
     "stain_prob": 0.00, "crumple": 0.00, "downscale": 1.0},
    {"name": "clean_laser",     "noise": 0.008, "fade": 0.01, "rotation": 0.15, "jpeg_q": 97,
     "stain_prob": 0.00, "crumple": 0.00, "downscale": 1.0},
    {"name": "high_res_clean",  "noise": 0.005, "fade": 0.01, "rotation": 0.10, "jpeg_q": 98,
     "stain_prob": 0.00, "crumple": 0.00, "downscale": 1.0},
]

PROFILES_DEGRADED = [
    {"name": "worn_laser",      "noise": 0.035, "fade": 0.08, "rotation": 0.8,  "jpeg_q": 85,
     "stain_prob": 0.18, "crumple": 0.15, "downscale": 0.85},
    {"name": "inkjet_old",      "noise": 0.05,  "fade": 0.14, "rotation": 1.0,  "jpeg_q": 78,
     "stain_prob": 0.25, "crumple": 0.20, "downscale": 0.80},
    {"name": "low_ink",         "noise": 0.025, "fade": 0.22, "rotation": 0.5,  "jpeg_q": 82,
     "stain_prob": 0.15, "crumple": 0.10, "downscale": 0.88},
    {"name": "archive_scan",    "noise": 0.06,  "fade": 0.10, "rotation": 0.9,  "jpeg_q": 75,
     "stain_prob": 0.30, "crumple": 0.25, "downscale": 0.78},
    {"name": "office_copy",     "noise": 0.03,  "fade": 0.06, "rotation": 0.6,  "jpeg_q": 88,
     "stain_prob": 0.10, "crumple": 0.12, "downscale": 0.90},
]

PROFILES_HEAVY = [
    {"name": "fax_quality",     "noise": 0.11,  "fade": 0.22, "rotation": 2.2,  "jpeg_q": 55,
     "stain_prob": 0.55, "crumple": 0.55, "downscale": 0.55},
    {"name": "tea_stained",     "noise": 0.08,  "fade": 0.28, "rotation": 1.8,  "jpeg_q": 60,
     "stain_prob": 0.75, "crumple": 0.45, "downscale": 0.62},
    {"name": "crumpled_scan",   "noise": 0.07,  "fade": 0.20, "rotation": 2.5,  "jpeg_q": 58,
     "stain_prob": 0.40, "crumple": 0.70, "downscale": 0.60},
    {"name": "low_dpi_old",     "noise": 0.09,  "fade": 0.30, "rotation": 1.6,  "jpeg_q": 50,
     "stain_prob": 0.45, "crumple": 0.35, "downscale": 0.48},
    {"name": "ink_bleed_worn",  "noise": 0.10,  "fade": 0.32, "rotation": 2.0,  "jpeg_q": 52,
     "stain_prob": 0.60, "crumple": 0.50, "downscale": 0.55},
]

PROFILES_BY_TIER = {
    "clean": PROFILES_CLEAN,
    "degraded": PROFILES_DEGRADED,
    "heavy": PROFILES_HEAVY,
}

# Flattened, in fixed order, for stable index-based lookup (kept for any
# code that still wants "global profile index by doc_index", e.g. legacy
# get_degradation_metadata callers).
ALL_PROFILES = PROFILES_CLEAN + PROFILES_DEGRADED + PROFILES_HEAVY


# ── Core pixel-level operations (unchanged behaviour, same as before) ────────

def _add_gaussian_noise(img: np.ndarray, sigma: float, rng: np.random.Generator) -> np.ndarray:
    noise = rng.normal(0, sigma * 255, img.shape).astype(np.float32)
    noisy = img.astype(np.float32) + noise
    return np.clip(noisy, 0, 255).astype(np.uint8)


def _apply_fading(img: np.ndarray, fade: float, rng: np.random.Generator) -> np.ndarray:
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


# ── New effect: resolution downscale/upscale (simulates low-DPI scan) ────────

def _downscale_upscale(img: np.ndarray, factor: float) -> np.ndarray:
    """
    Shrink then enlarge back to original size to simulate a low-resolution
    scan being upsampled. factor=1.0 means no-op; factor=0.5 means the
    image was effectively scanned at half the linear resolution.
    """
    if factor >= 0.999:
        return img
    h, w = img.shape[:2]
    small_w = max(1, int(w * factor))
    small_h = max(1, int(h * factor))
    small = cv2.resize(img, (small_w, small_h), interpolation=cv2.INTER_AREA)
    back = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return back


# ── New effect: stain overlays (tea / coffee / ink blots) ────────────────────

def _stain_color(rng: np.random.Generator, kind: str) -> tuple:
    """BGR colour for a stain type."""
    if kind == "tea":
        # warm amber-brown, semi-translucent
        return (int(rng.integers(60, 110)), int(rng.integers(110, 160)), int(rng.integers(150, 195)))
    if kind == "coffee":
        return (int(rng.integers(30, 70)), int(rng.integers(60, 100)), int(rng.integers(90, 130)))
    # ink blot — near-black/blue-black
    return (int(rng.integers(10, 40)), int(rng.integers(10, 35)), int(rng.integers(10, 30)))


def _apply_stains(img: np.ndarray, stain_prob: float, rng: np.random.Generator, intensity: float = 1.0) -> np.ndarray:
    """
    Draw 1-3 irregular blotchy stains using overlapping translucent ellipses
    with soft Gaussian-blurred edges. `intensity` scales opacity and count
    for heavier tiers.
    """
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

        # Build a soft mask for this single stain
        mask = np.zeros((h, w), dtype=np.uint8)
        # Composite of 2-4 overlapping ellipses gives an irregular blob shape
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


# ── New effect: crumple / warp (simulates folded or wrinkled paper) ──────────

def _apply_crumple(img: np.ndarray, strength: float, rng: np.random.Generator) -> np.ndarray:
    """
    Simulate paper crumple/wrinkle via a smooth random displacement field
    plus subtle local shading (wrinkle shadows). `strength` in [0, 1].
    """
    if strength <= 0:
        return img

    h, w = img.shape[:2]

    # Low-frequency random displacement field (smooth, not noisy)
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

    # Subtle wrinkle shading: derive shadow bands from displacement gradient
    grad = cv2.Laplacian(disp_x + disp_y, cv2.CV_32F, ksize=5)
    shadow = np.clip(grad * strength * 1.5, -25, 25)
    shadow_3d = np.stack([shadow] * 3, axis=2)
    shaded = np.clip(warped.astype(np.float32) + shadow_3d, 0, 255).astype(np.uint8)

    return shaded


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
                          (overrides tier entirely — useful for fast test runs)

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

    # Heavier tiers get a slightly stronger stain/crumple intensity multiplier
    intensity = {"clean": 0.0, "degraded": 1.0, "heavy": 1.6}.get(tier, 1.0)

    # ── Step 1: Gaussian scanner noise ───────────────────────────────────────
    img = _add_gaussian_noise(img, profile["noise"], rng)

    # ── Step 2: Pixel fading ──────────────────────────────────────────────────
    img = _apply_fading(img, profile["fade"], rng)

    # ── Step 3: Brightness / contrast ────────────────────────────────────────
    img = _adjust_brightness_contrast(img, rng)

    # ── Step 4: Stains (tea / coffee / ink) ──────────────────────────────────
    if profile.get("stain_prob", 0) > 0:
        img = _apply_stains(img, profile["stain_prob"], rng, intensity=intensity)

    # ── Step 5: Crumple / wrinkle warp ───────────────────────────────────────
    if profile.get("crumple", 0) > 0:
        img = _apply_crumple(img, profile["crumple"], rng)

    # ── Step 6: Resolution downscale/upscale (low-DPI simulation) ────────────
    if profile.get("downscale", 1.0) < 1.0:
        img = _downscale_upscale(img, profile["downscale"])

    # ── Step 7: Rotation ──────────────────────────────────────────────────────
    img = _rotate_image(img, profile["rotation"], rng)

    # ── Step 8: JPEG re-compression ───────────────────────────────────────────
    if profile["jpeg_q"] < 95:
        img = _jpeg_compress(img, profile["jpeg_q"])

    # ── Step 9: Encode back to PNG ────────────────────────────────────────────
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
    }