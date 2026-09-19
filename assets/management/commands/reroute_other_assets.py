"""
One-off fix for data imported BEFORE the 'others' sheet started routing rows
by Device Type (see DEVICE_TYPE_CATEGORY_ALIASES in import_utils.py).

Finds every Asset currently sitting in the 'Other Asset' catch-all category
whose extra_details['Device Type'] matches a real category (e.g. 'A/C' ->
Air Conditioner, 'Biometrics' -> Biometric Device) and moves it there.

Usage:
    python manage.py reroute_other_assets           # dry run, shows what would move
    python manage.py reroute_other_assets --apply    # actually moves them
"""
from django.core.management.base import BaseCommand

from assets.models import Asset, AssetCategory
from assets.import_utils import (
    DEVICE_TYPE_CATEGORY_ALIASES,
    FALLBACK_CATEGORY_NAME,
    _normalize_header,
)


class Command(BaseCommand):
    help = "Move already-imported 'Other Asset' rows into their real category based on stored Device Type."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually save the changes. Without this flag, only prints what would happen.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]

        try:
            other_cat = AssetCategory.objects.get(name=FALLBACK_CATEGORY_NAME)
        except AssetCategory.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"No '{FALLBACK_CATEGORY_NAME}' category found — nothing to do."))
            return

        categories_by_name = {c.name.strip().lower(): c for c in AssetCategory.objects.all()}

        candidates = Asset.objects.filter(category=other_cat)
        moved = 0
        skipped_no_match = 0
        skipped_target_missing = 0

        for asset in candidates:
            device_type_text = str((asset.extra_details or {}).get("Device Type", "")).strip()
            if not device_type_text:
                continue
            target_name = DEVICE_TYPE_CATEGORY_ALIASES.get(_normalize_header(device_type_text))
            if not target_name:
                skipped_no_match += 1
                continue
            target_cat = categories_by_name.get(target_name.strip().lower())
            if not target_cat:
                skipped_target_missing += 1
                self.stdout.write(self.style.WARNING(
                    f"  {asset.asset_tag}: Device Type '{device_type_text}' maps to "
                    f"'{target_name}' but that category doesn't exist — skipped."
                ))
                continue

            self.stdout.write(
                f"{'Moving' if apply_changes else 'Would move'} {asset.asset_tag} "
                f"({device_type_text!r}): '{other_cat.name}' -> '{target_cat.name}'"
            )
            if apply_changes:
                asset.category = target_cat
                # updated_by left as whatever it already was; this is a
                # scripted housekeeping move, not a per-row admin edit.
                asset.save()
            moved += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n{'Moved' if apply_changes else 'Would move'} {moved} asset(s). "
            f"{skipped_no_match} row(s) had no matching category (left in '{other_cat.name}'). "
            f"{skipped_target_missing} row(s) matched a category name that doesn't exist in the DB."
        ))
        if not apply_changes and moved:
            self.stdout.write(self.style.WARNING("Dry run only — re-run with --apply to save these changes."))
