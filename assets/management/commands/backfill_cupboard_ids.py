"""
Management command: backfill_cupboard_ids

Inside Cupboard rows imported into "Other Asset" have Asset Tag / Name /
Item Type filled, but the Other Asset builder fields ID / Device Name /
Device Type were left empty. This fills them for rows ALREADY in the database:

    ID           <- Asset Tag
    Device Name  <- Name
    Device Type  <- Item Type

Only blank values are filled; nothing is overwritten. Only Other Asset rows
that have an Item Type value (i.e. the Inside Cupboard ones) are touched.

    python manage.py backfill_cupboard_ids --dry-run
    python manage.py backfill_cupboard_ids
"""
import re

from django.core.management.base import BaseCommand

from assets.models import Asset, AssetCategory, AssetField


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


class Command(BaseCommand):
    help = "Fill ID / Device Name / Device Type on Inside Cupboard rows in Other Asset."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        cat = AssetCategory.objects.filter(name__iexact="Other Asset").first()
        if cat is None:
            self.stdout.write(self.style.ERROR("No 'Other Asset' category found."))
            return
        by_label = {_norm(f.label): f for f in AssetField.objects.filter(category=cat, is_active=True)}
        id_f, name_f = by_label.get("id"), by_label.get("device name")
        type_f, item_f = by_label.get("device type"), by_label.get("item type")
        if item_f is None:
            self.stdout.write(self.style.ERROR("Other Asset has no 'Item Type' field."))
            return

        changed = 0
        for asset in Asset.objects.filter(category=cat).iterator():
            extra = dict(asset.extra_details or {})
            item_type = str(extra.get(item_f.key, "") or "").strip()
            if not item_type:
                continue
            wanted = (
                (id_f, asset.asset_tag),
                (name_f, asset.name),
                (type_f, item_type),
            )
            touched = False
            for f, val in wanted:
                if f is None or not str(val or "").strip():
                    continue
                if str(extra.get(f.key, "") or "").strip():
                    continue
                extra[f.key] = str(val).strip()
                touched = True
            if touched:
                changed += 1
                if not dry:
                    Asset.objects.filter(pk=asset.pk).update(extra_details=extra)
        verb = "would update" if dry else "updated"
        self.stdout.write(self.style.SUCCESS(f"Done. {verb} {changed} asset(s)."))
