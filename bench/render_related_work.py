"""Render the framework comparison table (task H1).

    python -m bench.render_related_work            # the table, plus what is missing
    python -m bench.render_related_work --strict   # exit 1 if any row is unverified

Reads docs/related_work.yaml and prints the table with every unverified row
flagged. Exists so the table cannot quietly drift into looking finished: a row
whose cells were guessed from a title renders with a marker beside it until
somebody has opened the paper and set verified: true.

    WHY THIS IS ENFORCED RATHER THAN TRUSTED

A wrong cell in a comparison table is the first thing a reviewer checks, and a
fabricated citation loses their trust in everything else in the report. The
handbook's rule is that every citation gets a DOI you have personally opened.
This makes the difference between "checked" and "assumed" visible in the output
instead of living in somebody's memory.
"""

import argparse
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "docs" / "related_work.yaml"

HEADINGS = {
    "targets": "Targets",
    "metrics": "Metrics",
    "finality_aware": "Finality-aware",
    "emulation": "Emulation",
    "cost": "Cost",
    "reproducible": "Reproducible",
    "code_public": "Code public",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 while any row is unverified")
    args = ap.parse_args()

    config = yaml.safe_load(SOURCE.read_text()) or {}
    rows = config.get("rows") or []
    columns = config.get("columns") or []

    print(f"\nFramework comparison — {len(rows)} rows\n")

    unverified, missing_doi = [], []
    for row in rows:
        mark = " " if row.get("verified") else "!"
        print(f"{mark} {row['name']}")
        if not row.get("verified"):
            unverified.append(row["name"])
        doi = str(row.get("doi", "TODO"))
        if doi.upper().startswith("TODO"):
            missing_doi.append(row["name"])
        cells = row.get("cells") or {}
        for key in columns:
            value = cells.get(key, "unknown")
            flag = "  <- unchecked" if str(value) == "unknown" else ""
            print(f"      {HEADINGS.get(key, key):<16} {value}{flag}")
        print()

    print("-" * 66)
    if missing_doi:
        print(f"No DOI yet ({len(missing_doi)}): {', '.join(missing_doi)}")
    if unverified:
        print(f"Unverified ({len(unverified)}): {', '.join(unverified)}")
        print("\nA row stays unverified until its paper has been opened at the "
              "DOI recorded here and every cell confirmed against it. Cells "
              "marked 'unknown' were never checked - which is not the same "
              "claim as 'no', and must not be printed as one.")
    else:
        print("Every row verified against its source.")

    print()
    return 1 if (args.strict and unverified) else 0


if __name__ == "__main__":
    raise SystemExit(main())
