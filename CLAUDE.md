# gdrive-pdf-exporter

A CLI tool that extracts download-restricted PDFs from Google Drive (including those shared via Google Classroom) by capturing page images from the Drive viewer and stitching them into a PDF.

## How it works

Google Drive can restrict download and print on files. However, the viewer still renders each page as a PNG image via the internal `drive.google.com/viewer/img` endpoint. This tool:

1. Opens a Chromium browser (Playwright, persistent session so login is preserved)
2. Navigates to the given URL — either a Google Classroom material page or a direct Drive file URL
3. If Classroom URL: scrapes the page to find the attached Drive file URL
4. Opens the Drive viewer and intercepts the `viewer/img` thumbnail requests to extract the opaque viewer ID (different from the Drive file ID)
5. Uses that viewer ID to fetch all pages at high resolution (default 1920px wide) via `fetch()` with session cookies
6. Converts each PNG to JPEG and stitches them into a PDF using `img2pdf`
7. Saves the PDF to `output/` inside the project directory, using the Drive file's actual filename

## Project structure

```
export_pdf.py      # Single-file implementation — all logic lives here
requirements.txt   # pip dependencies
setup.bat          # One-time setup: pip install + playwright install chromium
run.bat            # Convenience wrapper: resolves correct Python and runs the script
output/               # Output directory (auto-created, gitignored)
```

## Key implementation details

- **Python resolver**: `find_python_with_pip()` tries `py` (Windows launcher), `sys.executable`, then `python`/`python3` to avoid picking up MSYS2 or other pip-less Pythons.
- **Auto-install**: `ensure_dependencies()` runs at import time. Missing packages trigger pip install and `sys.exit(0)` so the user relaunches with correct imports. Missing Playwright browser triggers `playwright install chromium`.
- **Persistent browser profile**: stored at `~/.pdf-exporter/browser-profile/` so Google login survives across runs.
- **Viewer ID extraction**: intercepted from the first `viewer/img` network request on page load. Fallback: regex scan of page source.
- **Page count detection**: sidebar thumbnail count → page counter regex in page source → JSON fields `numPages`/`pageCount` → default 20.
- **Filename**: extracted from the Drive page title (`"foo.pdf - Google Drive"` → `foo.pdf`), falling back to the Classroom page heading.
- **Output deduplication**: appends `(1)`, `(2)`, … if a file with the same name already exists.

## Configuration constants (top of export_pdf.py)

| Constant | Default | Description |
|---|---|---|
| `DEFAULT_OUTPUT_DIR` | `<script dir>/pdf` | Where PDFs are saved |
| `PAGE_WIDTH` | `1920` | Viewer image width in pixels |
| `JPEG_QUALITY` | `92` | JPEG compression quality (1–95) |
| `LOGIN_TIMEOUT` | `120` | Seconds to wait for manual Google login |

## Running locally

```bash
# First-time setup (Windows)
setup.bat

# Run
py export_pdf.py "https://classroom.google.com/u/1/c/.../m/.../details"
py export_pdf.py "https://drive.google.com/file/d/<id>/view"
py export_pdf.py "<url>" --output "D:/MyPDFs"
py export_pdf.py "<url>" --width 2560
```

Or via the wrapper:
```bash
run.bat "https://classroom.google.com/..."
```

## Dependencies

- `playwright` — browser automation (Chromium)
- `Pillow` — PNG → JPEG conversion
- `img2pdf` — lossless JPEG-to-PDF stitching

## Known limitations / areas to improve

- `authuser=1` is hardcoded in the viewer URL; accounts using index 0 or 2+ may fail
- Page count falls back to 20 if detection fails — fetching non-existent pages returns HTTP 4xx and are silently skipped, but it wastes requests
- No retry logic on failed page fetches
- Classroom URL parsing only handles single PDF attachments per material page
- Direct Drive URLs (`/file/d/<id>/view`) can be passed directly, skipping the Classroom step entirely
