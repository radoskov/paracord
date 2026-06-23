#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/latex"
mkdir -p build
if command -v latexmk >/dev/null 2>&1; then
  latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir=build main.tex
else
  pdflatex -interaction=nonstopmode -halt-on-error -output-directory=build main.tex
  pdflatex -interaction=nonstopmode -halt-on-error -output-directory=build main.tex
fi
echo "Built docs/latex/build/main.pdf"
