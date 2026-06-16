#!/bin/zsh
set -e

echo "==> Creating virtual environment..."
python3 -m venv .venv

echo "==> Activating venv and installing dependencies..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt

echo ""
echo "✓ Setup selesai!"
echo ""
echo "Untuk menjalankan bot:"
echo "  source .venv/bin/activate"
echo "  python main.py"
