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

The old import also threw away the sheet's "Details" column ("serviced on
28/2/2020"). Pass --sheet with the original Excel file to fill Last Service
Date from it (matched on the ID column) for rows that don't have one yet.

Usage:
    python manage.py fix_air_conditioner_status            # dry run, shows what would change
    python manage.py fix_air_conditioner_status --apply    # actually saves the changes
    python manage.py fix_air_conditioner_status --sheet "system details.xlsx" --apply
"""
import os
import re

from django.core.management.base import BaseCommand, CommandError

from assets.models import Asset, AssetCategory

NOTE_PREFIX = "Status note: "

# "1 TON, RIGHT CORNER OF OFFICE serviced on 16.03.2020" -> capacity + service text
SERVICED_RE = re.compile(r"[\s,;\-–]*\bservice[d]?\b.*$", re.IGNORECASE)


def split_serviced(text):
    """Returns (capacity_location, service_text or "")."""
    text = (text or "").strip()
    m = SERVICED_RE.search(text)
    if not m or m.start() == 0:
        return text, ""
    return text[:m.start()].strip(" ,;-–"), m.group(0).strip(" ,;-–")


class Command(BaseCommand):
    help = "Move Air Conditioner's mis-imported Status/Notes text into a clean capacity_location field."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually save the changes. Without this flag, only prints what would happen.",
        )
        parser.add_argument(
            "--sheet", type=str, default=None,
            help="Original .xlsx: fill Last Service Date from its ID / Details columns.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        if options["sheet"] and not os.path.isfile(options["sheet"]):
            raise CommandError(
                f"Sheet file not found: {options['sheet']!r}. Give the real path of your "
                "Excel file (or leave out --sheet — it is optional)."
            )

        try:
            category = AssetCategory.objects.get(name__iexact="Air Conditioner")
        except AssetCategory.DoesNotExist:
            self.stdout.write(self.style.ERROR("No 'Air Conditioner' category found — nothing to do."))
            return

        candidates = Asset.objects.filter(category=category).order_by("asset_tag")
        count = 0

        for asset in candidates:
            notes = asset.notes or ""
            if NOTE_PREFIX not in notes:
                continue  # no leftover "Status note:" text — nothing to fix here

            # Pull the "Status note: ..." chunk out, keep anything else in
            # Notes untouched (there could be more than one note appended).
            # In practice every imported row here is exactly one plain
            # note, so the text after the prefix is the whole capacity/
            # location value.
            before, _, rest = notes.partition(NOTE_PREFIX)
            capacity_location, service_text = split_serviced(rest.strip())
            remaining_notes = before.strip()

            count += 1
            self.stdout.write(
                f"{'Updating' if apply_changes else 'Would update'} {asset.asset_tag}: "
                f"status {asset.status} -> {'working' if asset.status == 'other' else asset.status}, "
                f"capacity_location = '{capacity_location}'"
                + (f", notes -> '{remaining_notes}'" if remaining_notes else ", notes cleared")
            )

            if apply_changes:
                if asset.status == "other":
                    asset.status = "working"
                asset.notes = remaining_notes
                extra = dict(asset.extra_details or {})
                extra["capacity_location"] = capacity_location
                asset.extra_details = extra
                self._apply_service_text(asset, service_text)
                asset.save()

        # Rows fixed by an earlier run of this command whose capacity text still
        # carries the "serviced on ..." part (e.g. A/C005).
        count += self._clean_saved_capacity(category, apply_changes)

        if options["sheet"]:
            self._fill_service_dates(category, options["sheet"], apply_changes)

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No matching Air Conditioner records found — nothing to fix."))
            return

        self.stdout.write(self.style.SUCCESS(
            f"\n{'Updated' if apply_changes else 'Would update'} {count} record(s)."
        ))
        if not apply_changes:
            self.stdout.write(self.style.WARNING("Dry run only — re-run with --apply to save these changes."))

    def _apply_service_text(self, asset, service_text):
        """Put a date found in "serviced on 16.03.2020" into Last Service Date."""
        if not service_text or asset.last_service_date:
            return
        from assets.import_utils import _parse_date_cell
        parsed, _err = _parse_date_cell(service_text)
        if parsed:
            asset.last_service_date = parsed

    def _clean_saved_capacity(self, category, apply_changes):
        n = 0
        for asset in Asset.objects.filter(category=category).order_by("asset_tag"):
            extra = dict(asset.extra_details or {})
            cap, service_text = split_serviced(extra.get("capacity_location", ""))
            if not service_text:
                continue
            n += 1
            self.stdout.write(
                f"{'Cleaning' if apply_changes else 'Would clean'} {asset.asset_tag}: "
                f"capacity_location -> '{cap}', service note '{service_text}'"
            )
            if apply_changes:
                extra["capacity_location"] = cap
                asset.extra_details = extra
                self._apply_service_text(asset, service_text)
                asset.save()
        return n

    def _fill_service_dates(self, category, path, apply_changes):
        """Read ID / Status / Details from the original sheet and fill, for each
        A/C, Capacity / Location (the sheet's Status column) and Last Service
        Date (the sheet's Details column) wherever they are still empty."""
        from openpyxl import load_workbook
        from assets.import_utils import _parse_date_cell, _normalize_header, STATUS_LOOKUP

        wb = load_workbook(path, read_only=True, data_only=True)
        matched = changed = 0
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            header = [str(h or "").strip().lower() for h in rows[0]]
            if "id" not in header:
                continue
            id_i = header.index("id")
            det_i = header.index("details") if "details" in header else None
            cap_i = header.index("status") if "status" in header else None
            for row in rows[1:]:
                tag = str(row[id_i] or "").strip()
                if not tag:
                    continue
                asset = Asset.objects.filter(category=category, asset_tag__iexact=tag).first()
                if asset is None:
                    continue
                matched += 1
                extra = dict(asset.extra_details or {})
                notes = asset.notes or ""
                msgs = []

                cap = str(row[cap_i] or "").strip() if cap_i is not None and cap_i < len(row) else ""
                cap, _svc = split_serviced(cap)
                if cap and not STATUS_LOOKUP.get(_normalize_header(cap)) and not str(extra.get("capacity_location", "")).strip():
                    extra["capacity_location"] = cap
                    notes = notes.replace(NOTE_PREFIX + cap, "").strip()
                    if asset.status == "other":
                        asset.status = "working"
                    msgs.append(f"capacity/location = '{cap}'")

                details = str(row[det_i] or "").strip() if det_i is not None and det_i < len(row) else ""
                if details and not asset.last_service_date:
                    parsed, _err = _parse_date_cell(details)
                    if parsed:
                        asset.last_service_date = parsed
                        msgs.append(f"last service date = {parsed}")
                    else:
                        msgs.append(f"could not read a date from '{details}' (left empty)")

                if msgs:
                    changed += 1
                    self.stdout.write(f"{'Setting' if apply_changes else 'Would set'} {tag}: " + "; ".join(msgs))
                    if apply_changes:
                        asset.extra_details = extra
                        asset.notes = notes
                        asset.save()
        self.stdout.write(self.style.SUCCESS(
            f"Sheet: matched {matched} A/C record(s), {'updated' if apply_changes else 'would update'} {changed}."
        ))
