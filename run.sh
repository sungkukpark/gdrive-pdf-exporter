#!/bin/bash
echo "============================================================"
echo "  Google Classroom PDF Exporter"
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
    echo "[ERROR] Python 3 not found. Please run setup.sh first."
    exit 1
fi

if [ -z "$1" ]; then
    echo "Usage: ./run.sh \"Google Classroom URL\" [output folder]"
    echo
    echo "Examples:"
    echo "  ./run.sh \"https://classroom.google.com/u/1/c/Njg3.../m/Njg3.../details\""
    echo "  ./run.sh \"https://classroom.google.com/...\" \"/Users/me/PDFs\""
    echo
    read -r -p "Enter Google Classroom URL: " URL
    if [ -z "$URL" ]; then
        echo "No URL entered. Exiting."
        exit 1
    fi
    "$PYTHON" export_pdf.py "$URL"
elif [ -z "$2" ]; then
    "$PYTHON" export_pdf.py "$1"
else
    "$PYTHON" export_pdf.py "$1" --output "$2"
fi

if [ $? -ne 0 ]; then
    echo
    echo "[ERROR] Something went wrong. Check the output above for details."
    exit 1
fi

echo
echo "Done!"
