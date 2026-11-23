.DEFAULT_GOAL := nir
LATEXMK ?= latexmk
PYTHON ?= python3
LATEX_FLAGS := -cd -pdf -Werror -halt-on-error -interaction=nonstopmode -file-line-error

.PHONY: nir coursework pdf setup-codex

nir:
	$(LATEXMK) $(LATEX_FLAGS) -outdir=../build/nir docs/main.tex

coursework:
	$(LATEXMK) $(LATEX_FLAGS) -outdir=../build/coursework docs/coursework.tex

pdf: nir coursework

setup-codex:
	$(PYTHON) scripts/setup_codex.py
