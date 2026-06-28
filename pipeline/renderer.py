"""
pipeline/renderer.py
====================
Playwright HTML → High-Resolution PNG Renderer

Uses a pool of persistent Playwright browser contexts (one per worker) to
avoid the overhead of launching a new browser for every document.

Resolution: 794×1123px @ deviceScaleFactor=2 → effectively 1588×2246px
(A4 at 150dpi equivalent, sufficient for OCR training).
"""

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger("renderer")

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    log.warning("Playwright not installed — will use Pillow fallback renderer.")

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ── Browser pool (one instance per process / worker) ─────────────────────────
_playwright_instance = None
_browser: Optional["Browser"] = None


async def _get_browser() -> "Browser":
    global _playwright_instance, _browser
    if _browser is None or not _browser.is_connected():
        if _playwright_instance is None:
            _playwright_instance = await async_playwright().start()
        _browser = await _playwright_instance.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-extensions",
            ],
        )
        log.debug("Chromium browser launched")
    return _browser


async def close_browser():
    global _playwright_instance, _browser
    if _browser:
        await _browser.close()
        _browser = None
    if _playwright_instance:
        await _playwright_instance.stop()
        _playwright_instance = None


async def render_html_to_png(
    html_content: str,
    timeout_ms: int = 30000,
    scale: float = 2.0,
) -> bytes:
    """
    Render an HTML string to a PNG image using Playwright.

    Parameters
    ----------
    html_content  : complete HTML document string
    timeout_ms    : Playwright navigation timeout in milliseconds
    scale         : deviceScaleFactor (2.0 = retina / 150dpi equivalent)

    Returns
    -------
    PNG bytes
    """
    if not PLAYWRIGHT_AVAILABLE:
        return await _pillow_fallback_render(html_content)

    browser = await _get_browser()
    context: BrowserContext = await browser.new_context(
        viewport={"width": 900, "height": 1200},
        device_scale_factor=scale,
    )
    page: Page = await context.new_page()

    try:
        # Write HTML to a temp file and load via file:// to allow font loading
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", encoding="utf-8", delete=False
        ) as f:
            f.write(html_content)
            tmp_path = f.name

        await page.goto(f"file://{tmp_path}", timeout=timeout_ms)

        # Wait for fonts to load (Caveat from Google Fonts)
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)

        # Get document bounding box to set exact clip region
        bounding = await page.evaluate("""() => {
            const doc = document.querySelector('.document');
            if (doc) {
                const rect = doc.getBoundingClientRect();
                return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
            }
            return { x: 0, y: 0, width: 900, height: 1200 };
        }""")

        screenshot = await page.screenshot(
            clip={
                "x": max(0, bounding["x"] - 10),
                "y": max(0, bounding["y"] - 10),
                "width": min(bounding["width"] + 20, 900),
                "height": min(bounding["height"] + 20, 1200),
            },
            full_page=False,
            type="png",
        )

        return screenshot

    except Exception as e:
        log.error(f"Playwright render error: {e}")
        # Return a 1×1 transparent PNG on failure so the pipeline continues
        return _minimal_error_png()
    finally:
        await page.close()
        await context.close()
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


async def _pillow_fallback_render(html_content: str) -> bytes:
    """
    Minimal Pillow-based fallback when Playwright is unavailable.
    Renders a white canvas with key extracted text fields.
    """
    if not PIL_AVAILABLE:
        return _minimal_error_png()

    import re
    import io

    # Extract visible text (very naive — for fallback only)
    text = re.sub(r"<[^>]+>", " ", html_content)
    text = re.sub(r"\s+", " ", text).strip()[:800]

    img = Image.new("RGB", (794, 1123), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    draw.text((30, 30), "FALLBACK RENDER (Playwright unavailable)", fill=(200, 0, 0), font=font)
    draw.text((30, 60), text[:600], fill=(50, 50, 50), font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _minimal_error_png() -> bytes:
    """Return a minimal 1×1 white PNG as last-resort fallback."""
    # Minimal valid PNG: 1×1 white pixel
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
        b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x058\xce"
        b"\x90\x00\x00\x00\x00IEND\xaeB`\x82"
    )
