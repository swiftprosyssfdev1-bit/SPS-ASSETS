"""
One-off fix for Air Conditioner records imported from the "Others" sheet.
That sheet's raw Status column actually held each unit's capacity +
install location (e.g. "1.5 TON, ADMIN ROOM"), not a real working/not
working status. The importer couldn't recognize that text as a status, so
it fell back to:

    status = "other"
    notes  = "Status note: 1.5 TON, ADMIN ROOM"

This command finds every Air Conditioner record still holding that
fallback shape, and for each one:
  - pulls the capacity/location text back out of the "Status note: ..."
    prefix in Notes
  - saves it into extra_details["capacity_location"] (the clean field the
    Add/Edit form and detail/list pages now use)
  - resets status to "working" (the only real status these units had
    recorded — none of them said broken/idle/etc.)
  - removes the "Status note: ..." text from Notes, leaving whatever
    other notes (if any) were there

Only rows that still look untouched (status == "other" AND notes starts
with "Status note: ") are changed — anything already edited by hand is
left alone.

Usage:
    python manage.py fix_air_conditioner_status            # dry run, shows what would change
    python manage.py fix_air_conditioner_status --apply    # actually saves the changes
"""
from django.core.management.base import BaseCommand

from assets.models import Asset, AssetCategory

NOTE_PREFIX = "Status note: "


class Command(BaseCommand):
    help = "Move Air Conditioner's mis-imported Status/Notes text into a clean capacity_location field."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually save the changes. Without this flag, only prints what would happen.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]

        try:
            category = AssetCategory.objects.get(name__iexact="Air Conditioner")
        except AssetCategory.DoesNotExist:
            self.stdout.write(self.style.ERROR("No 'Air Conditioner' category found — nothing to do."))
            return

        candidates = Asset.objects.filter(category=category, status="other").order_by("asset_tag")
        count = 0

        for asset in candidates:
            notes = asset.notes or ""
            if NOTE_PREFIX not in notes:
                continue  # status="other" for some other reason — leave it alone

            # Pull the "Status note: ..." chunk out, keep anything else in
            # Notes untouched (there could be more than one note appended).
            # In practice every imported row here is exactly one plain
            # note, so the text after the prefix is the whole capacity/
            # location value.
            before, _, rest = notes.partition(NOTE_PREFIX)
            capacity_location = rest.strip()
            remaining_notes = before.strip()

            count += 1
            self.stdout.write(
                f"{'Updating' if apply_changes else 'Would update'} {asset.asset_tag}: "
                f"status other -> working, capacity_location = '{capacity_location}'"
                + (f", notes -> '{remaining_notes}'" if remaining_notes else ", notes cleared")
            )

            if apply_changes:
                asset.status = "working"
                asset.notes = remaining_notes
                extra = dict(asset.extra_details or {})
                extra["capacity_location"] = capacity_location
                asset.extra_details = extra
                asset.save()

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No matching Air Conditioner records found — nothing to fix."))
            return

        self.stdout.write(self.style.SUCCESS(
            f"\n{'Updated' if apply_changes else 'Would update'} {count} record(s)."
        ))
        if not apply_changes:
            self.stdout.write(self.style.WARNING("Dry run only — re-run with --apply to save these changes."))
