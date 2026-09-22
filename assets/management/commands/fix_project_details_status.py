"""
One-off fix for Project Details records that were bulk-imported with no
value in the legacy sheet's Status column. Asset.status defaults to
"working" when nothing is provided, so every project that had a blank
Status cell (i.e. everything except the ones that already said "Stopped")
ended up showing "Working" instead of a real project status.

This finds every Project Details record still sitting on the "working"
default and flips it to "active" (an ongoing project). Records already on
any other status (e.g. "stopped") are left untouched.

Usage:
    python manage.py fix_project_details_status            # dry run, shows what would change
    python manage.py fix_project_details_status --apply    # actually saves the changes
"""
from django.core.management.base import BaseCommand

from assets.models import Asset, AssetCategory


class Command(BaseCommand):
    help = "Flip Project Details records stuck on the 'working' default to 'active'."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually save the changes. Without this flag, only prints what would happen.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]

        try:
            category = AssetCategory.objects.get(name__iexact="Project Details")
        except AssetCategory.DoesNotExist:
            self.stdout.write(self.style.ERROR("No 'Project Details' category found — nothing to do."))
            return

        candidates = Asset.objects.filter(category=category, status="working").order_by("asset_tag")
        count = candidates.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No Project Details records on 'working' — nothing to fix."))
            return

        for asset in candidates:
            self.stdout.write(
                f"{'Updating' if apply_changes else 'Would update'} {asset.asset_tag}: "
                f"working -> active"
            )
            if apply_changes:
                asset.status = "active"
                # updated_by left as whatever it already was; this is a
                # scripted housekeeping fix, not a per-row admin edit.
                asset.save()

        self.stdout.write(self.style.SUCCESS(
            f"\n{'Updated' if apply_changes else 'Would update'} {count} record(s)."
        ))
        if not apply_changes:
            self.stdout.write(self.style.WARNING("Dry run only — re-run with --apply to save these changes."))
