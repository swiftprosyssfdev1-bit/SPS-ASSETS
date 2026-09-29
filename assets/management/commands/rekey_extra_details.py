"""
Management command: rekey_extra_details

Categories that were put under the Category Builder (seed_dynamic_fields) got
new slug-style field keys ("S.No" -> "s_no", "Employee Id" -> "employee_id"),
but the values already stored in Asset.extra_details stayed under the OLD
header-text keys. The list / view / edit pages look values up by the new key,
so those old values show as blank.

This copies each value onto its field's current key (matching by key or label,
ignoring case / spaces / punctuation) and removes the old key. Nothing is
overwritten: a field that already has a value on its current key is left alone.

    python manage.py rekey_extra_details --dry-run
    python manage.py rekey_extra_details
    python manage.py rekey_extra_details --category "Mouse"
"""
import re

from django.core.management.base import BaseCommand

from assets.models import Asset, AssetCategory, AssetField


def _norm(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


class Command(BaseCommand):
    help = "Move old-keyed extra_details values onto the Category Builder field keys."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--category", type=str, default=None)

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        cats = AssetCategory.objects.all().order_by("name")
        if opts["category"]:
            cats = cats.filter(name__iexact=opts["category"])
        total_assets = total_moves = 0
        for cat in cats:
            fields = list(AssetField.objects.filter(category=cat))
            if not fields:
                continue
            current_keys = {f.key for f in fields}
            cat_assets = cat_moves = 0
            for asset in Asset.objects.filter(category=cat).iterator():
                extra = dict(asset.extra_details or {})
                changed = False
                for f in fields:
                    if str(extra.get(f.key, "")).strip():
                        continue
                    targets = {_norm(f.key), _norm(f.label)} - {""}
                    for old_key in list(extra.keys()):
                        if old_key == f.key or old_key in current_keys or _norm(old_key) not in targets:
                            continue
                        val = extra[old_key]
                        if not str(val or "").strip():
                            continue
                        extra[f.key] = val
                        del extra[old_key]
                        changed = True
                        cat_moves += 1
                        break
                if changed:
                    cat_assets += 1
                    if not dry:
                        Asset.objects.filter(pk=asset.pk).update(extra_details=extra)
            if cat_moves:
                self.stdout.write(f"  {cat.name}: {cat_moves} value(s) on {cat_assets} asset(s)")
            total_assets += cat_assets
            total_moves += cat_moves
        verb = "would move" if dry else "moved"
        self.stdout.write(self.style.SUCCESS(f"Done. {verb} {total_moves} value(s) across {total_assets} asset(s)."))
