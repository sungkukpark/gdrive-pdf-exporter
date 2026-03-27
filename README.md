# gdrive-pdf-exporter

<p align="center">
  <img src="logo.png" alt="gdrive-pdf-exporter logo" width="200"/>
</p>

Export download-restricted PDFs from Google Drive and Google Classroom — no extensions, no workarounds, just Python.

When a Google Drive file has downloading and printing disabled, the Drive viewer still renders each page as an image. This tool captures those images at full resolution and stitches them into a proper PDF file.

---

## Use cases

- PDFs shared via **Google Classroom** with download disabled
- Google Drive files with "Viewers can download, print, and copy" turned off
- Any file viewable at `drive.google.com/file/d/<id>/view` that won't let you save it

---

## How it works

1. Opens a Chromium browser via Playwright (headless-off, persistent session)
2. Navigates to the Google Classroom or Drive URL
3. Intercepts the internal `drive.google.com/viewer/img` thumbnail requests to extract a session-scoped viewer ID
4. Uses that viewer ID to fetch every page as a high-resolution PNG (default: 1920px wide), authenticated via session cookies
5. Converts each page to JPEG and stitches them into a single PDF using `img2pdf`
6. Saves the result to `output/` with the original filename from Drive

---

## Requirements

- Python 3.8+
  - **Windows**: install from [python.org](https://www.python.org/downloads/) — avoid MSYS2/Conda builds (no pip)
  - **macOS**: `brew install python` or [python.org](https://www.python.org/downloads/)

Python packages (auto-installed on first run):
- `playwright`
- `Pillow`
- `img2pdf`

---

## Quick start

### Windows

```bat
:: First-time setup
setup.bat

:: Run
run.bat "https://classroom.google.com/u/1/c/.../m/.../details"
```

Or directly:

```bat
py export_pdf.py "https://classroom.google.com/u/1/c/.../m/.../details"
py export_pdf.py "https://drive.google.com/file/d/<file-id>/view"
```

### macOS / Linux

```bash
# First-time setup
chmod +x setup.sh run.sh
./setup.sh

# Run
./run.sh "https://classroom.google.com/u/1/c/.../m/.../details"
```

Or directly:

```bash
python3 export_pdf.py "https://classroom.google.com/u/1/c/.../m/.../details"
python3 export_pdf.py "https://drive.google.com/file/d/<file-id>/view"
```

---

On first run a browser window opens — log in to your Google account. Your session is saved to `~/.pdf-exporter/browser-profile/` and reused on subsequent runs.

---

### Options

```
usage: export_pdf.py [-h] [--output OUTPUT] [--width WIDTH] url

positional arguments:
  url                   Google Classroom or Google Drive file URL

options:
  -h, --help            show this help message and exit
  --output, -o OUTPUT   Output folder (default: ./output)
  --width, -w WIDTH     Page image width in pixels, higher = sharper (default: 1920)
```

### Examples

```bash
# Save to default ./output folder
python3 export_pdf.py "https://drive.google.com/file/d/1abc.../view"

# Save to a custom folder
python3 export_pdf.py "https://classroom.google.com/..." --output "~/Downloads"

# Higher resolution (slower but sharper)
python3 export_pdf.py "https://drive.google.com/..." --width 2560
```

---

## Project structure

```
export_pdf.py      # All logic — single file, no framework
requirements.txt   # pip dependencies
setup.bat          # One-time setup (Windows)
setup.sh           # One-time setup (macOS / Linux)
run.bat            # Convenience launcher (Windows)
run.sh             # Convenience launcher (macOS / Linux)
output/            # Output folder — created automatically, not committed
```

---

## Configuration

Edit the constants at the top of `export_pdf.py`:

| Constant | Default | Description |
|---|---|---|
| `DEFAULT_OUTPUT_DIR` | `./output` | Where exported PDFs are saved |
| `PAGE_WIDTH` | `1920` | Viewer image fetch width in pixels |
| `JPEG_QUALITY` | `92` | JPEG compression quality (1–95) |
| `LOGIN_TIMEOUT` | `120` | Seconds to wait for Google login |

---

## Notes

- The browser session is stored in `~/.pdf-exporter/browser-profile/`. Delete this folder to reset your login.
- Output filenames come from the Drive file title. Duplicate filenames get a `(1)`, `(2)` suffix automatically.
- If page count detection fails the script defaults to 20 pages. Pages that don't exist are silently skipped (HTTP 4xx).

---

## License

[MIT](LICENSE)
