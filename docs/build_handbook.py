"""Render the project documents to PDF.

    python docs/build_handbook.py

Reads each HTML source in docs/ and writes its PDF beside it. Kept as a script
rather than a one-off command so the documents can be regenerated after the code
changes, instead of drifting out of date.
"""

from pathlib import Path

from weasyprint import HTML

DOCS = Path(__file__).resolve().parent
DOCUMENTS = (
    ("handbook.html", "L2_Benchmark_Project_Handbook.pdf"),
    ("system_design.html", "L2_Benchmark_System_Design.pdf"),
)


def main() -> int:
    for source, output in DOCUMENTS:
        path = DOCS / source
        if not path.exists():
            continue
        target = DOCS / output
        HTML(filename=str(path), base_url=str(DOCS)).write_pdf(str(target))
        print(f"wrote {target.name}  ({target.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
