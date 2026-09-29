"""
Populates the new Workstation table from the 45 existing legacy
Asset(category="Workstation") rows.

DRY-RUN BY DEFAULT — prints exactly what it would create/write, touches
nothing. Pass --apply to actually create the Workstation rows.

Never modifies Asset, AssetHistory, or AssetCategory. Safe to re-run:
skips any legacy Asset that already has a workstation_profile.

Prerequisites:
    python manage.py seed_workstation_fields   (run once, first)

Usage:
    python manage.py migrate_workstation_data              # dry run
    python manage.py migrate_workstation_data --apply       # actually writes
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from assets.models import Asset, AssetCategory, Workstation, WorkstationField
from assets.relations import resolve_asset_reference, _clean_ref_tokens

# Legacy extra_details key(s) -> WorkstationField label, is_multi
LEGACY_SOURCE_MAP = [
    (["Employee ID", "Employee Name"], "Employee", "Employee", False),
    (["CPU Number"],                    "CPU",      "CPU / System Unit", True),
    (["Monitor Number"],                "Monitor",  "Monitor", True),
    (["Keyboard Number"],               "Keyboard", "Keyboard", False),
    (["Mouse Number"],                  "Mouse",    "Mouse", False),
    (["UPS No.", "UPS No"],             "UPS",      "UPS", True),
]
PLAIN_FIELDS = ["Product Id", "Cd Key", "Operating System", "Purposes", "User Id"]


class Command(BaseCommand):
    help = "Populate Workstation from the legacy 45 Asset rows. Dry-run unless --apply is passed."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Actually write the Workstation rows.")

    def handle(self, *args, **options):
        apply = options["apply"]
        category = AssetCategory.objects.filter(name__iexact="workstation").first()
        if not category:
            self.stdout.write(self.style.ERROR("No 'Workstation' AssetCategory found."))
            return

        field_by_label = {f.label: f for f in WorkstationField.objects.all()}
        needed_labels = [lbl for _keys, lbl, _cat, _multi in LEGACY_SOURCE_MAP] + PLAIN_FIELDS
        missing = [lbl for lbl in needed_labels if lbl not in field_by_label]
        if missing:
            self.stdout.write(self.style.ERROR(
                f"Missing WorkstationField(s): {missing}. Run 'python manage.py "
                f"seed_workstation_fields' first."
            ))
            return

        assets = Asset.objects.filter(category=category).order_by("asset_tag")
        created, skipped, with_unresolved = 0, 0, 0

        for ws_asset in assets:
            if Workstation.objects.filter(asset=ws_asset).exists():
                self.stdout.write(f"  skip (already migrated): {ws_asset.asset_tag}")
                skipped += 1
                continue

            extra = ws_asset.extra_details or {}
            extra_lower = {str(k).strip().lower(): v for k, v in extra.items()}
            new_extra, unresolved = {}, {}

            for keys, label, target_cat, is_multi in LEGACY_SOURCE_MAP:
                raw_val = None
                for k in keys:
                    raw_val = extra_lower.get(k.strip().lower())
                    if raw_val:
                        break
                raw_str = str(raw_val or "").strip()
                tokens = _clean_ref_tokens(raw_str) if is_multi else ([raw_str] if raw_str else [])
                field_key = field_by_label[label].key

                matched, unmatched = [], []
                for tok in tokens:
                    resolved = resolve_asset_reference(tok, target_cat)
                    (matched if resolved else unmatched).append(resolved.asset_tag if resolved else tok)

                if is_multi:
                    new_extra[field_key] = matched
                else:
                    new_extra[field_key] = matched[0] if matched else ""
                if unmatched:
                    unresolved[field_key] = unmatched

            for label in PLAIN_FIELDS:
                field_key = field_by_label[label].key
                new_extra[field_key] = str(extra.get(label, "") or "").strip()

            if unresolved:
                with_unresolved += 1

            self.stdout.write(f"{'[APPLY]' if apply else '[DRY RUN]'} {ws_asset.asset_tag}: "
                               f"extra_details={new_extra}" + (f"  UNRESOLVED={unresolved}" if unresolved else ""))

            if apply:
                with transaction.atomic():
                    Workstation.objects.create(asset=ws_asset, extra_details=new_extra, unresolved=unresolved)
                created += 1

        self.stdout.write(self.style.WARNING(
            f"\n{'Created' if apply else 'Would create'}: {created if apply else assets.count() - skipped} "
            f"| Already migrated (skipped): {skipped} | With unresolved links: {with_unresolved}"
        ))
        if not apply:
            self.stdout.write(self.style.WARNING("Dry run only — nothing written. Re-run with --apply to commit."))
