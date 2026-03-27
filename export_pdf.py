"""
gdrive-pdf-exporter
====================
Export download-restricted PDFs from Google Drive and Google Classroom.

Usage:
    python export_pdf.py <URL> [--output DIR] [--width PX]

URL may be a Google Classroom material page or a direct Google Drive file URL.
On first run a browser window opens for Google login; the session is then
persisted at ~/.pdf-exporter/browser-profile/ for subsequent runs.
"""

from __future__ import annotations

import asyncio
import argparse
import io
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------------------
# Bootstrap: ensure third-party deps are present before importing them
# ---------------------------------------------------------------------------

def _find_python_with_pip() -> str | None:
    """Return the first Python executable that ships with pip."""
    for candidate in ("py", sys.executable, "python", "python3"):
        try:
            result = subprocess.run(
                [candidate, "-m", "pip", "--version"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return None


def _bootstrap() -> None:
    """Install missing packages and Playwright browser on first run."""
    required = {"playwright": "playwright", "PIL": "Pillow", "img2pdf": "img2pdf"}
    missing = [pkg for mod, pkg in required.items() if not _importable(mod)]

    if missing:
        python = _find_python_with_pip()
        if not python:
            sys.exit(
                "[ERROR] No Python with pip found.\n"
                "        Install Python from https://www.python.org/downloads/\n"
                "        and make sure to tick 'Add Python to PATH'."
            )
        print(f"[SETUP] Installing: {', '.join(missing)}")
        subprocess.check_call([python, "-m", "pip", "install", *missing])
        print("[SETUP] Done. Please relaunch the script.")
        sys.exit(0)

    _ensure_playwright_browser()


def _importable(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def _ensure_playwright_browser() -> None:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            if not Path(pw.chromium.executable_path).exists():
                raise FileNotFoundError
    except Exception:
        python = _find_python_with_pip() or sys.executable
        print("[SETUP] Installing Playwright browser (one-time download)...")
        subprocess.check_call([python, "-m", "playwright", "install", "chromium"])


_bootstrap()

# Third-party imports — safe after bootstrap
from playwright.async_api import async_playwright, BrowserContext, Page  # noqa: E402
from PIL import Image  # noqa: E402
import img2pdf  # noqa: E402

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration."""

    output_dir: Path = field(default_factory=lambda: Path(__file__).parent / "output")
    page_width: int = 1920
    jpeg_quality: int = 92
    login_timeout: int = 120
    profile_dir: Path = field(
        default_factory=lambda: Path.home() / ".pdf-exporter" / "browser-profile"
    )

    def __post_init__(self) -> None:
        if not (1 <= self.jpeg_quality <= 95):
            raise ValueError(f"jpeg_quality must be 1–95, got {self.jpeg_quality}")
        if self.page_width < 100:
            raise ValueError(f"page_width must be >= 100, got {self.page_width}")


@dataclass(frozen=True)
class PageImage:
    """A single fetched page: its 0-based index and raw JPEG bytes."""

    index: int
    data: bytes

    @property
    def size_kb(self) -> int:
        return len(self.data) // 1024


# ---------------------------------------------------------------------------
# Browser context manager
# ---------------------------------------------------------------------------

@asynccontextmanager
async def managed_browser(config: Config) -> AsyncIterator[Page]:
    """Launch a persistent Chromium context and yield a ready Page."""
    config.profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser: BrowserContext = await pw.chromium.launch_persistent_context(
            str(config.profile_dir),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            viewport={"width": 1280, "height": 900},
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()
        try:
            yield page
        finally:
            await browser.close()
            print("  Browser closed.")


# ---------------------------------------------------------------------------
# ClassroomScraper
# ---------------------------------------------------------------------------

class ClassroomScraper:
    """Extracts an attached Google Drive file URL from a Classroom material page."""

    _SELECTORS = (
        "a[href*='drive.google.com/file']",
        "a[href*='drive.google.com/open']",
        ".MRjpXd a",
        "[data-drive-id] a",
        "a[href*='id=']",
    )

    def __init__(self, page: Page, login_timeout: int) -> None:
        self._page = page
        self._login_timeout = login_timeout

    async def get_drive_url(self, classroom_url: str) -> str | None:
        """Navigate to *classroom_url* and return the first Drive attachment URL."""
        print("  Loading Classroom page...")
        await self._page.goto(classroom_url, wait_until="networkidle")
        await self._handle_login()
        return await self._find_drive_url()

    async def _handle_login(self) -> None:
        if "accounts.google.com" not in self._page.url:
            return
        print("  [AUTH] Google login required — please log in via the browser...")
        try:
            await self._page.wait_for_url(
                "**/classroom.google.com/**",
                timeout=self._login_timeout * 1_000,
            )
            print("  [AUTH] Login successful.")
        except Exception as exc:
            raise RuntimeError("Login timed out.") from exc

    async def _find_drive_url(self) -> str | None:
        for selector in self._SELECTORS:
            try:
                el = await self._page.query_selector(selector)
                if el and (href := await el.get_attribute("href")):
                    if "drive.google.com" in href:
                        return href
            except Exception:
                continue

        # Brute-force fallback: scan every anchor on the page
        for link in await self._page.query_selector_all("a"):
            href = await link.get_attribute("href") or ""
            if "drive.google.com" in href and (
                "file/d/" in href or "open?id=" in href
            ):
                return href

        return None


# ---------------------------------------------------------------------------
# DriveViewerSession
# ---------------------------------------------------------------------------

class DriveViewerSession:
    """
    Opens a Drive file in the viewer, intercepts thumbnail network requests
    to recover the opaque viewer ID, then fetches each page at full resolution.
    """

    _VIEWER_IMG_HOST = "drive.google.com/viewer/img"

    def __init__(self, page: Page, config: Config) -> None:
        self._page = page
        self._config = config

    async def open(self, drive_url: str) -> tuple[str, int, str | None]:
        """
        Navigate to *drive_url* and return ``(viewer_id, total_pages, filename)``.
        """
        viewer_id = await self._intercept_viewer_id(drive_url)
        if not viewer_id:
            viewer_id = self._search_viewer_id_in_source(
                await self._page.content()
            )
        filename = await self._extract_filename()
        total_pages = await self._detect_page_count()
        return viewer_id, total_pages, filename

    async def fetch_pages(
        self, viewer_id: str, total_pages: int
    ) -> AsyncIterator[PageImage]:
        """Async-generate :class:`PageImage` objects for every page."""
        print(f"  Downloading {total_pages} pages at {self._config.page_width}px...")
        for index in range(total_pages):
            image = await self._fetch_one_page(viewer_id, index, total_pages)
            if image is not None:
                yield image

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _intercept_viewer_id(self, drive_url: str) -> str | None:
        viewer_id: str | None = None
        captured = asyncio.Event()

        def on_request(request: object) -> None:
            nonlocal viewer_id
            url: str = getattr(request, "url", "")
            if self._VIEWER_IMG_HOST in url and not viewer_id:
                params = parse_qs(urlparse(url).query)
                if ids := params.get("id"):
                    viewer_id = ids[0]
                    captured.set()

        print("  Loading Drive viewer...")
        self._page.on("request", on_request)
        await self._page.goto(drive_url, wait_until="domcontentloaded")
        try:
            await asyncio.wait_for(captured.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass
        finally:
            self._page.remove_listener("request", on_request)

        return viewer_id

    @staticmethod
    def _search_viewer_id_in_source(html: str) -> str | None:
        m = re.search(r"viewer/img\?id=([A-Za-z0-9_\-]+)", html)
        return m.group(1) if m else None

    async def _extract_filename(self) -> str | None:
        try:
            title = await self._page.title()
            name = re.sub(
                r"\s*-\s*Google Drive\s*$", "", title, flags=re.IGNORECASE
            ).strip()
            return name or None
        except Exception:
            return None

    async def _detect_page_count(self) -> int:
        # 1. Sidebar thumbnails
        try:
            thumbs = await self._page.query_selector_all(
                ".ndfHFb-c4YZDc-cYSp0e-DARUcf"
            )
            if thumbs:
                return len(thumbs)
        except Exception:
            pass

        # 2. Page source patterns
        html = await self._page.content()
        for pattern in (
            r"[Pp]age\s+\d+\s*/\s*(\d+)",
            r'"numPages"\s*:\s*(\d+)',
            r'"pageCount"\s*:\s*(\d+)',
        ):
            if m := re.search(pattern, html):
                return int(m.group(1))

        print("  [WARN] Could not detect page count — defaulting to 20.")
        return 20

    def _build_page_url(self, viewer_id: str, index: int) -> str:
        return (
            f"https://drive.google.com/viewer/img"
            f"?id={viewer_id}"
            f"&authuser=1"
            f"&dsmi=texmex"
            f"&page={index}"
            f"&skiphighlight=true"
            f"&w={self._config.page_width}"
        )

    async def _fetch_one_page(
        self, viewer_id: str, index: int, total: int
    ) -> PageImage | None:
        import base64

        url = self._build_page_url(viewer_id, index)
        result: dict = await self._page.evaluate(
            """async (url) => {
                const resp = await fetch(url, {credentials: "include"});
                const buf  = await resp.arrayBuffer();
                const u8   = new Uint8Array(buf);
                let bin = "";
                for (const b of u8) bin += String.fromCharCode(b);
                return {status: resp.status, data: btoa(bin), size: u8.length};
            }""",
            url,
        )

        if result["status"] != 200:
            print(f"  [SKIP] Page {index + 1}: HTTP {result['status']}")
            return None

        png_bytes = base64.b64decode(result["data"])
        jpeg_bytes = self._png_to_jpeg(png_bytes)
        page = PageImage(index=index, data=jpeg_bytes)
        print(f"  [OK]   Page {index + 1}/{total} ({page.size_kb} KB)")
        return page

    def _png_to_jpeg(self, png_bytes: bytes) -> bytes:
        buf = io.BytesIO()
        Image.open(io.BytesIO(png_bytes)).convert("RGB").save(
            buf, format="JPEG", quality=self._config.jpeg_quality
        )
        return buf.getvalue()


# ---------------------------------------------------------------------------
# PDFWriter
# ---------------------------------------------------------------------------

class PDFWriter:
    """Stitches a list of JPEG page images into a single PDF file."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def save(self, pages: list[PageImage], filename: str) -> Path:
        output_dir = self._config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        path = self._unique_path(output_dir, self._sanitize(filename))
        print(f"\n  Building PDF: {path}")
        path.write_bytes(img2pdf.convert([p.data for p in pages]))

        size_mb = path.stat().st_size / 1024 / 1024
        print(f"  Saved: {path} ({size_mb:.1f} MB, {len(pages)} pages)")
        return path

    @staticmethod
    def _sanitize(name: str) -> str:
        clean = re.sub(r'[<>:"/\\|?*]', "_", name).strip(". ")
        return clean if clean.lower().endswith(".pdf") else f"{clean}.pdf"

    @staticmethod
    def _unique_path(directory: Path, filename: str) -> Path:
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        path = directory / filename
        counter = 1
        while path.exists():
            path = directory / f"{stem} ({counter}){suffix}"
            counter += 1
        return path


# ---------------------------------------------------------------------------
# PDFExporter — top-level orchestrator
# ---------------------------------------------------------------------------

class PDFExporter:
    """Orchestrates the full export pipeline for a given URL."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._writer = PDFWriter(config)

    async def export(self, url: str) -> Path | None:
        async with managed_browser(self._config) as page:
            drive_url, filename = await self._resolve_drive_url(url, page)
            if not drive_url:
                print("\n[ERROR] Could not locate a Drive file URL.")
                return None

            session = DriveViewerSession(page, self._config)
            viewer_id, total_pages, drive_filename = await session.open(drive_url)

            if not viewer_id:
                print(
                    "\n[ERROR] Could not retrieve viewer ID.\n"
                    "  - Confirm you are logged in to Google.\n"
                    "  - Confirm you have access to this file."
                )
                return None

            filename = drive_filename or filename or "document"
            print(f"  Filename  : {filename}")
            print(f"  Viewer ID : {viewer_id[:40]}...")
            print(f"  Pages     : {total_pages}")

            pages: list[PageImage] = []
            async for page_image in session.fetch_pages(viewer_id, total_pages):
                pages.append(page_image)

            if not pages:
                print("\n[ERROR] No pages were downloaded.")
                return None

            return self._writer.save(pages, filename)

    async def _resolve_drive_url(
        self, url: str, page: Page
    ) -> tuple[str | None, str | None]:
        """
        If *url* already points to Drive, return it directly.
        Otherwise treat it as a Classroom URL and scrape the attachment link.
        """
        if "drive.google.com" in url:
            return url, None

        print("[2/4] Locating PDF attachment on Classroom page...")
        scraper = ClassroomScraper(page, self._config.login_timeout)
        drive_url = await scraper.get_drive_url(url)
        return drive_url, None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="gdrive-pdf-exporter — export download-restricted PDFs from Google Drive"
    )
    parser.add_argument(
        "url",
        help="Google Classroom material URL or direct Google Drive file URL",
    )
    parser.add_argument(
        "--output", "-o",
        default=str(Path(__file__).parent / "output"),
        metavar="DIR",
        help="Output folder (default: ./output)",
    )
    parser.add_argument(
        "--width", "-w",
        type=int,
        default=1920,
        metavar="PX",
        help="Page image width in pixels — higher is sharper (default: 1920)",
    )
    return parser


async def _main_async(url: str, config: Config) -> None:
    exporter = PDFExporter(config)
    step = "[3/4]" if "classroom.google.com" not in url else "[2/4]"
    print(f"{step} Opening Drive viewer...")
    await exporter.export(url)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    config = Config(
        output_dir=Path(args.output),
        page_width=args.width,
    )

    print("=" * 60)
    print("  gdrive-pdf-exporter")
    print("=" * 60)
    print(f"  URL    : {args.url[:70]}...")
    print(f"  Output : {config.output_dir}")
    print(f"  Width  : {config.page_width}px")
    print("=" * 60)
    print("[1/4] Starting browser...")

    asyncio.run(_main_async(args.url, config))


if __name__ == "__main__":
    main()
