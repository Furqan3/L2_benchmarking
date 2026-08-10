"""Render the project handbook to PDF.

    python docs/build_handbook.py

Reads docs/handbook.html and writes docs/L2_Benchmark_Project_Handbook.pdf.
Kept as a script rather than a one-off command so the document can be
regenerated after the code changes, instead of drifting out of date.
"""

from pathlib import Path

from weasyprint import HTML

DOCS = Path(__file__).resolve().parent
SOURCE = DOCS / "handbook.html"
OUTPUT = DOCS / "L2_Benchmark_Project_Handbook.pdf"


def main() -> int:
    HTML(filename=str(SOURCE), base_url=str(DOCS)).write_pdf(str(OUTPUT))
    size = OUTPUT.stat().st_size
    print(f"wrote {OUTPUT}  ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
