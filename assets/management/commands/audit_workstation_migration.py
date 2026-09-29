"""
Read-only audit for the Workstation -> dedicated model migration.

Makes NO writes to the database. Safe to run anytime, as many times as needed.

Usage:
    Copy this file to assets/management/commands/audit_workstation_migration.py
    Then run:
        python manage.py audit_workstation_migration
        python manage.py audit_workstation_migration --csv workstation_audit.csv

For every legacy Workstation Asset row, this checks each relationship field
(CPU Number, Monitor Number, Keyboard Number, Mouse Number, UPS No.,
Employee ID / Employee Name) against the SAME resolution logic relations.py
already uses (resolve_asset_reference), and classifies each token as:

    MATCHED    - resolves to exactly one existing, active Asset
    UNMATCHED  - blank, a placeholder ("idle", "-", "none", ...), or no
                 Asset found with that tag/name
    AMBIGUOUS  - the token is capable of matching more than one Asset
                 (this mainly hits Employee Name, which is matched by
                 icontains — e.g. "Vino" could match "Vinoth" AND
                 "Vinodhini")

This is the concrete data the migration plan's "how many can be matched
automatically / which have missing links / which are ambiguous" section
needs — run it and paste the summary + CSV back for the actual counts on
your data, since this can't be produced by reading code alone.
"""
import csv
import sys

from django.core.management.base import BaseCommand
from assets.models import Asset, AssetCategory
from assets.relations import resolve_asset_reference, _clean_ref_tokens

# (label used in the report, extra_details key(s) to try, target category, is_multi)
WORKSTATION_LINK_FIELDS = [
    ("CPU",      ["CPU Number"],                 "CPU / System Unit", True),
    ("Monitor",  ["Monitor Number"],              "Monitor",           True),
    ("Keyboard", ["Keyboard Number"],             "Keyboard",          False),
    ("Mouse",    ["Mouse Number"],                "Mouse",             False),
    ("UPS",      ["UPS No.", "UPS No"],           "UPS",               True),
    ("Employee", ["Employee ID", "Employee Name"], "Employee",         False),
]


def _is_ambiguous(token, target_category_name):
    """True if this token, if matched by name, would hit >1 active Asset."""
    if not token:
        return False
    qs = Asset.objects.filter(is_active=True)
    cat = AssetCategory.objects.filter(name__iexact=target_category_name).first()
    if cat:
        qs = qs.filter(category=cat)
    if qs.filter(asset_tag__iexact=token).count() >= 1:
        return False  # exact tag match short-circuits before name matching
    if target_category_name == "Employee":
        return qs.filter(name__icontains=token).count() > 1
    return False


class Command(BaseCommand):
    help = "Read-only match/ambiguity report for the 45 legacy Workstation records."

    def add_arguments(self, parser):
        parser.add_argument("--csv", dest="csv_path", default=None,
                             help="Optional path to also write a per-row CSV report.")

    def handle(self, *args, **options):
        category = AssetCategory.objects.filter(name__iexact="workstation").first()
        if not category:
            self.stdout.write(self.style.SUCCESS("No 'Workstation' category found."))
            return

        rows = Asset.objects.filter(category=category).order_by("asset_tag")
        total = rows.count()
        self.stdout.write(self.style.WARNING(f"\nAuditing {total} Workstation records...\n"))

        csv_rows = []
        totals = {label: {"matched": 0, "unmatched": 0, "ambiguous": 0} for label, *_ in WORKSTATION_LINK_FIELDS}
        clean_records = 0
        records_with_issues = []

        for ws in rows:
            extra = ws.extra_details or {}
            extra_lower = {str(k).strip().lower(): v for k, v in extra.items()}
            row_report = {"Workstation": ws.asset_tag, "Active": ws.is_active, "Branch": ws.branch_id}
            row_has_issue = False

            for label, keys, target_cat, is_multi in WORKSTATION_LINK_FIELDS:
                raw_val = None
                for k in keys:
                    raw_val = extra_lower.get(k.strip().lower())
                    if raw_val:
                        break
                raw_str = str(raw_val or "").strip()
                tokens = _clean_ref_tokens(raw_str) if is_multi else ([raw_str] if raw_str else [])

                if not tokens:
                    row_report[label] = "UNMATCHED (blank)"
                    totals[label]["unmatched"] += 1
                    row_has_issue = True
                    continue

                statuses = []
                for tok in tokens:
                    if _is_ambiguous(tok, target_cat):
                        statuses.append(f"AMBIGUOUS:{tok}")
                        totals[label]["ambiguous"] += 1
                        row_has_issue = True
                    elif resolve_asset_reference(tok, target_cat):
                        statuses.append(f"OK:{tok}")
                        totals[label]["matched"] += 1
                    else:
                        statuses.append(f"UNMATCHED:{tok}")
                        totals[label]["unmatched"] += 1
                        row_has_issue = True
                row_report[label] = "; ".join(statuses)

            csv_rows.append(row_report)
            if row_has_issue:
                records_with_issues.append(ws.asset_tag)
            else:
                clean_records += 1

        self.stdout.write(f"Fully clean records (every link matches, nothing blank/ambiguous): "
                           f"{clean_records} / {total}\n")
        self.stdout.write(f"Records needing manual review: {len(records_with_issues)} / {total}")
        if records_with_issues:
            self.stdout.write("  -> " + ", ".join(records_with_issues))

        self.stdout.write("\nPer-field breakdown:")
        for label, *_ in WORKSTATION_LINK_FIELDS:
            t = totals[label]
            self.stdout.write(f"  {label:10s}  matched={t['matched']:<4} "
                               f"unmatched={t['unmatched']:<4} ambiguous={t['ambiguous']:<4}")

        if options["csv_path"]:
            fieldnames = ["Workstation", "Active", "Branch"] + [l for l, *_ in WORKSTATION_LINK_FIELDS]
            with open(options["csv_path"], "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(csv_rows)
            self.stdout.write(self.style.SUCCESS(f"\nWrote per-row CSV to {options['csv_path']}"))
