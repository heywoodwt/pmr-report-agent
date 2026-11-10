"""Automated Performance Measurement Report (PMR) generation."""

import pymupdf

# PyMuPDF turns on its neural page-layout analysis (pymupdf.layout, run through onnxruntime) whenever
# that module imports, which it does wherever numpy is installed. The pipeline reads PDFs by text and
# word positions and never uses the layout result: the sample report's facts, manifest and draft PDF
# are byte-identical with it on or off, but a run takes about 10 times as long with it on. Switch it off so
# every install reads PDFs the same way. PyMuPDF has no public switch; this is the hook
# pymupdf.layout.activate() sets, and tests/test_reproducibility.py fails if it changes.
pymupdf._get_layout = None
