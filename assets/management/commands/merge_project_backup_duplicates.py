"""
One-time cleanup: merge Project Backup records that got split into
"CL002", "CL002-2", "CL002-3" ... back into a single "CL002" record.

Why this happened: the Project Backup sheet is a running log — the same
physical hard disk's "Hard disk Name" (e.g. "CL002") gets a new row every
time it's reused for a different project/date. That's used as the asset
tag, so importing the log created a new "-2"/"-3" tag every time the same
drive name repeated, instead of updating the one record for that drive.
The importer now merges same-tag rows within a single import file, but it
can't retroactively fix records that were already saved from earlier,
separate import sessions — that's what this command does, once.

Usage:
    python manage.py merge_project_backup_duplicates --dry-run
    python manage.py merge_project_backup_duplicates
"""

import re

from django.core.management.base import BaseCommand
from django.db import transaction

from assets.models import Asset, AssetCategory

# Matches "CL002-2", "CL002-3", ... "CL002-10" — NOT "CRC006 8TB" (no dash+
# digits suffix) or "CL002" itself (no suffix at all).
SUFFIX_RE = re.compile(r"^(.*)-(\d+)$")


class Command(BaseCommand):
    help = "Merge Project Backup '<tag>-2', '<tag>-3', ... records back into the base '<tag>' record."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would be merged without changing the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        try:
            category = AssetCategory.objects.get(name__iexact="Project Backup")
        except AssetCategory.DoesNotExist:
            self.stderr.write("No 'Project Backup' category found — nothing to do.")
            return

        assets = list(Asset.objects.filter(category=category, is_active=True).order_by("asset_tag"))
        by_tag = {a.asset_tag.lower(): a for a in assets}

        # Group every "<base>-N" asset under its base tag, but only when the
        # base tag actually exists as its own record too (so "CRC006 8TB",
        # which has no dash-number suffix, and any tag whose "-N" is a real
        # part of the name with no matching base row, are left untouched).
        groups = {}  # base_tag_lower -> [duplicate assets with -N suffix]
        for a in assets:
            m = SUFFIX_RE.match(a.asset_tag)
            if not m:
                continue
            base_tag = m.group(1)
            base_key = base_tag.lower()
            if base_key in by_tag and by_tag[base_key] is not a:
                groups.setdefault(base_key, []).append(a)

        if not groups:
            self.stdout.write("No merge-able duplicates found.")
            return

        merged_count = 0
        with transaction.atomic():
            for base_key, dupes in groups.items():
                base_asset = by_tag[base_key]
                # Merge oldest-first so the pipe-joined history reads in
                # chronological order (matches created_at, i.e. import order).
                for dupe in sorted(dupes, key=lambda d: d.created_at):
                    self.stdout.write(
                        f"Merging {dupe.asset_tag!r} into {base_asset.asset_tag!r}"
                        + (" (dry run)" if dry_run else "")
                    )
                    for key, value in (dupe.extra_details or {}).items():
                        if not str(value or "").strip():
                            continue
                        existing = str(base_asset.extra_details.get(key, "") or "")
                        if value not in existing:
                            base_asset.extra_details[key] = (
                                f"{existing} | {value}".strip(" |") if existing else value
                            )
                    if dupe.notes and dupe.notes not in (base_asset.notes or ""):
                        base_asset.notes = f"{base_asset.notes} | {dupe.notes}".strip(" |")
                    merged_count += 1
                    if not dry_run:
                        dupe.is_active = False
                        dupe.notes = (dupe.notes + " | " if dupe.notes else "") + f"Merged into {base_asset.asset_tag}"
                        dupe.save(update_fields=["is_active", "notes"])
                if not dry_run:
                    base_asset.save(update_fields=["extra_details", "notes"])

        action = "Would merge" if dry_run else "Merged"
        self.stdout.write(self.style.SUCCESS(
            f"{action} {merged_count} duplicate record(s) across {len(groups)} base tag(s)."
        ))
        if dry_run:
            self.stdout.write("Re-run without --dry-run to actually apply these changes.")
