"""
Google Classroom PDF Exporter
==============================
Automatically extracts download-restricted PDFs from Google Classroom.

Usage:
    python export_pdf.py <Google Classroom URL> [--output OUTPUT_DIR]

Examples:
    python export_pdf.py "https://classroom.google.com/u/1/c/.../m/.../details"
    python export_pdf.py "https://classroom.google.com/u/1/c/.../m/.../details" --output "D:/PDFs"

On first run, you will be prompted to log in to Google in the browser window.
Your session will be saved automatically for future runs.
"""

import asyncio
import argparse
import sys
import os
import io
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------------------
# Configuration — edit as needed
# ---------------------------------------------------------------------------
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "output"
PAGE_WIDTH = 1920       # Image resolution in pixels (higher = better quality, slower)
JPEG_QUALITY = 92       # JPEG quality (1-95)
LOGIN_TIMEOUT = 120     # Seconds to wait for user login
# ---------------------------------------------------------------------------


def find_python_with_pip():
    """Find a Python executable that has pip available (prefers Windows py launcher)."""
    candidates = ["py", sys.executable, "python", "python3"]
    for candidate in candidates:
        try:
            result = subprocess.run(
                [candidate, "-m", "pip", "--version"],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return None


def ensure_dependencies():
    """Auto-install required packages if they are missing."""
    packages = {"playwright": "playwright", "PIL": "Pillow", "img2pdf": "img2pdf"}
    missing = []
    for module, package in packages.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)

    if missing:
        python = find_python_with_pip()
        if not python:
            print("[ERROR] Could not find a Python installation with pip.")
            print("        Please install Python from https://www.python.org/downloads/")
            print("        and make sure to check 'Add Python to PATH' during install.")
            sys.exit(1)

        print(f"[SETUP] Installing missing packages: {', '.join(missing)}")
        print(f"[SETUP] Using Python: {python}")
        subprocess.check_call([python, "-m", "pip", "install"] + missing)
        print("[SETUP] Packages installed. Relaunch the script to continue.")
        sys.exit(0)  # Exit so user relaunches with correct Python

    # Check if Playwright browser is installed
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser_path = p.chromium.executable_path
            if not Path(browser_path).exists():
                raise FileNotFoundError
    except Exception:
        python = find_python_with_pip() or sys.executable
        print("[SETUP] Installing Playwright browser (one-time download)...")
        subprocess.check_call([python, "-m", "playwright", "install", "chromium"])
        print("[SETUP] Browser installed.")


ensure_dependencies()

from playwright.async_api import async_playwright
from PIL import Image
import img2pdf


PROFILE_DIR = Path.home() / ".pdf-exporter" / "browser-profile"


async def wait_for_login(page):
    """Wait for the user to complete Google login if required."""
    if "accounts.google.com" in page.url or "google.com/signin" in page.url:
        print("  [AUTH] Google login required. Please log in via the browser window...")
        try:
            await page.wait_for_url("**/classroom.google.com/**", timeout=LOGIN_TIMEOUT * 1000)
            print("  [AUTH] Login successful!")
        except Exception:
            print("  [ERROR] Login timed out.")
            return False
    return True


async def get_drive_url_from_classroom(page, classroom_url):
    """Navigate to a Google Classroom material page and extract the Drive file URL."""
    print("  Loading Classroom page...")
    await page.goto(classroom_url, wait_until="networkidle")

    if not await wait_for_login(page):
        return None, None

    # Try various selectors to find the PDF attachment link
    selectors = [
        "a[href*='drive.google.com/file']",
        "a[href*='drive.google.com/open']",
        ".MRjpXd a",
        "[data-drive-id] a",
        "a[href*='id=']",
    ]

    drive_url = None
    filename = "document"

    for selector in selectors:
        try:
            el = await page.query_selector(selector)
            if el:
                href = await el.get_attribute("href")
                if href and "drive.google.com" in href:
                    drive_url = href
                    break
        except Exception:
            continue

    # Fall back to scanning all links on the page
    if not drive_url:
        links = await page.query_selector_all("a")
        for link in links:
            href = await link.get_attribute("href") or ""
            if "drive.google.com" in href and ("file/d/" in href or "open?id=" in href):
                drive_url = href
                break

    if not drive_url:
        print("  [ERROR] Could not find a PDF attachment on this page.")
        print("  Page title:", await page.title())
        return None, None

    # Try to extract a filename from the page title
    try:
        title_el = await page.query_selector("h1, .YVvGBb, .asQXV")
        if title_el:
            filename = (await title_el.text_content() or "document").strip()
    except Exception:
        pass

    print(f"  Found Drive URL: {drive_url[:80]}...")
    return drive_url, filename


async def get_viewer_img_id(page, drive_url):
    """
    Open the Drive viewer and intercept thumbnail requests to extract
    the viewer/img ID needed to fetch full-resolution page images.
    """
    viewer_id = None
    total_pages = None
    captured = asyncio.Event()

    def on_request(request):
        nonlocal viewer_id
        if "drive.google.com/viewer/img" in request.url and not viewer_id:
            parsed = urlparse(request.url)
            params = parse_qs(parsed.query)
            if "id" in params:
                viewer_id = params["id"][0]
                captured.set()

    page.on("request", on_request)

    print("  Loading Drive viewer...")
    await page.goto(drive_url, wait_until="domcontentloaded")

    # Wait up to 15 seconds for the first thumbnail request
    try:
        await asyncio.wait_for(captured.wait(), timeout=15)
    except asyncio.TimeoutError:
        pass

    page.remove_listener("request", on_request)

    # Fallback: search the page source for the ID
    if not viewer_id:
        content = await page.content()
        match = re.search(r'viewer/img\?id=([A-Za-z0-9_\-]+)', content)
        if match:
            viewer_id = match.group(1)

    # Extract the actual filename from the Drive page title
    # Title format: "filename.pdf - Google Drive"
    filename = None
    try:
        title = await page.title()
        # Strip " - Google Drive" suffix and any leading/trailing whitespace
        filename = re.sub(r'\s*-\s*Google Drive\s*$', '', title, flags=re.IGNORECASE).strip()
        if not filename:
            filename = None
    except Exception:
        pass

    # Attempt to extract page count
    try:
        thumbs = await page.query_selector_all(".ndfHFb-c4YZDc-cYSp0e-DARUcf")
        if thumbs:
            total_pages = len(thumbs)
    except Exception:
        pass

    if not total_pages:
        total_pages = await _guess_page_count(page)

    return viewer_id, total_pages, filename


async def _guess_page_count(page):
    """Try various methods to determine the total number of pages."""
    try:
        content = await page.content()
        m = re.search(r'[Pp]age\s+\d+\s*/\s*(\d+)', content)
        if m:
            return int(m.group(1))
        m = re.search(r'"numPages"\s*:\s*(\d+)', content)
        if m:
            return int(m.group(1))
        m = re.search(r'"pageCount"\s*:\s*(\d+)', content)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    print("  [WARN] Could not determine page count automatically. Defaulting to 20.")
    return 20


async def fetch_pages_as_images(page, viewer_id, total_pages, width=PAGE_WIDTH):
    """
    Fetch each page as a high-resolution PNG from the viewer/img endpoint,
    using the browser's authenticated session cookies.
    """
    print(f"  Downloading {total_pages} pages at {width}px width...")
    images = []

    for i in range(total_pages):
        url = (
            f"https://drive.google.com/viewer/img"
            f"?id={viewer_id}"
            f"&authuser=1"
            f"&dsmi=texmex"
            f"&page={i}"
            f"&skiphighlight=true"
            f"&w={width}"
        )

        result = await page.evaluate(f"""
        async () => {{
            const resp = await fetch("{url}", {{credentials: "include"}});
            const buffer = await resp.arrayBuffer();
            const bytes = new Uint8Array(buffer);
            let binary = '';
            for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
            return {{
                status: resp.status,
                type: resp.headers.get('content-type'),
                data: btoa(binary),
                size: bytes.length
            }};
        }}
        """)

        if result["status"] != 200:
            print(f"  [SKIP] Page {i+1}: HTTP {result['status']}")
            continue

        import base64
        img_bytes = base64.b64decode(result["data"])

        # Convert PNG -> JPEG to reduce file size
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        images.append(buf.getvalue())

        print(f"  [OK] Page {i+1}/{total_pages} ({result['size'] // 1024} KB)")

    return images


def sanitize_filename(name):
    """Remove characters that are invalid in file names."""
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip(". ")


def save_pdf(images, filename, output_dir):
    """Combine page images into a PDF and write it to disk."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = sanitize_filename(filename)
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"

    output_path = output_dir / safe_name

    # Handle duplicate filenames
    counter = 1
    while output_path.exists():
        stem = sanitize_filename(filename)
        output_path = output_dir / f"{stem} ({counter}).pdf"
        counter += 1

    print(f"\n  Building PDF: {output_path}")
    with open(output_path, "wb") as f:
        f.write(img2pdf.convert(images))

    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"  Saved: {output_path} ({size_mb:.1f} MB, {len(images)} pages)")
    return output_path


async def run(classroom_url, output_dir):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        print("[1/4] Starting browser...")
        browser = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
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
            # Step 1: Extract Drive URL from Classroom page
            print("[2/4] Locating PDF attachment on Classroom page...")
            drive_url, filename = await get_drive_url_from_classroom(page, classroom_url)
            if not drive_url:
                print("\n[ERROR] Could not find a Drive URL. Please check the link.")
                return

            # Step 2: Get viewer ID, page count, and actual filename from Drive viewer
            print("[3/4] Opening Drive viewer to get image IDs...")
            viewer_id, total_pages, drive_filename = await get_viewer_img_id(page, drive_url)
            if not viewer_id:
                print("\n[ERROR] Could not retrieve viewer ID.")
                print("  - Make sure you are logged in to Google.")
                print("  - Make sure you have access to this file.")
                return

            # Prefer the Drive page title (actual file name) over the Classroom page title
            if drive_filename:
                filename = drive_filename

            print(f"  Filename  : {filename}")
            print(f"  Viewer ID : {viewer_id[:40]}...")
            print(f"  Pages     : {total_pages}")

            # Step 3: Download each page as an image
            print("[4/4] Downloading pages and building PDF...")
            images = await fetch_pages_as_images(page, viewer_id, total_pages)
            if not images:
                print("\n[ERROR] No images were downloaded.")
                return

            # Step 4: Save as PDF
            save_pdf(images, filename, output_dir)

        finally:
            await browser.close()
            print("  Browser closed.")


def main():
    global PAGE_WIDTH

    parser = argparse.ArgumentParser(
        description="Google Classroom PDF Exporter — extract download-restricted PDFs"
    )
    parser.add_argument(
        "url",
        help="Google Classroom material URL (e.g. https://classroom.google.com/u/1/c/.../m/.../details)"
    )
    parser.add_argument(
        "--output", "-o",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output folder for the PDF (default: {DEFAULT_OUTPUT_DIR})"
    )
    parser.add_argument(
        "--width", "-w",
        type=int,
        default=PAGE_WIDTH,
        help=f"Page image width in pixels, higher = better quality (default: {PAGE_WIDTH})"
    )

    args = parser.parse_args()

    PAGE_WIDTH = args.width

    print("=" * 60)
    print("  Google Classroom PDF Exporter")
    print("=" * 60)
    print(f"  URL    : {args.url[:70]}...")
    print(f"  Output : {args.output}")
    print(f"  Width  : {args.width}px")
    print("=" * 60)

    asyncio.run(run(args.url, args.output))


if __name__ == "__main__":
    main()
