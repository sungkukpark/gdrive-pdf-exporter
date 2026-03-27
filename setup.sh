#!/bin/bash
echo "============================================================"
echo "  Google Classroom PDF Exporter - First-time Setup"
echo "============================================================"
echo

# Find Python 3
PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        VERSION=$("$candidate" -c "import sys; print(sys.version_info.major)")
        if [ "$VERSION" = "3" ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[ERROR] Python 3 is not installed."
    echo "        Install it via Homebrew:  brew install python"
    echo "        Or download from:         https://www.python.org/downloads/"
    exit 1
fi

echo "Using Python: $PYTHON ($($PYTHON --version))"
echo

echo "[1/3] Installing Python packages..."
"$PYTHON" -m pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to install packages."
    exit 1
fi

echo
echo "[2/3] Installing Playwright browser..."
"$PYTHON" -m playwright install chromium
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to install Playwright browser."
    exit 1
fi

echo
echo "[3/3] Setup complete!"
echo
echo "You can now run the exporter with:"
echo "  python3 export_pdf.py \"Google Classroom URL\""
echo "  ./run.sh \"Google Classroom URL\""
echo
