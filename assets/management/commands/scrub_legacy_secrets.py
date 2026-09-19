"""
Removes plain-text secrets (passwords, product/license keys, etc.) that were
imported into Asset.extra_details before the sensitive-field filter existed.

    python manage.py scrub_legacy_secrets            # dry run: only reports
    python manage.py scrub_legacy_secrets --apply    # actually deletes them

Matching uses the same rules as the import/UI filter
(assets.category_fields._is_sensitive_field), so 'Password', 'password__2',
'License_Key', 'Product Key' etc. are all caught.

NOTE: this edits the JSON directly (no Asset.save()), so it does not write
history rows or touch updated_at. Take a DB backup before using --apply.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from assets.category_fields import _is_sensitive_field
from assets.models import Asset


class Command(BaseCommand):
    help = "Find (dry run) or delete (--apply) plain-text secrets stored in Asset.extra_details."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually remove the secrets. Without this flag nothing is changed.",
        )

    def handle(self, *args, **opts):
        apply = opts["apply"]
        touched = 0
        removed = 0
        per_key = {}

        with transaction.atomic():
            for asset in Asset.objects.all().iterator():
                extra = asset.extra_details or {}
                bad_keys = [k for k in extra if _is_sensitive_field(k)]
                if not bad_keys:
                    continue
                touched += 1
                for k in bad_keys:
                    per_key[k] = per_key.get(k, 0) + 1
                    removed += 1
                if apply:
                    clean = {k: v for k, v in extra.items() if k not in bad_keys}
                    # update() bypasses signals/auto_now on purpose
                    Asset.objects.filter(pk=asset.pk).update(extra_details=clean)

        if not touched:
            self.stdout.write(self.style.SUCCESS("No sensitive values found. Nothing to do."))
            return

        for key, count in sorted(per_key.items()):
            self.stdout.write(f"  {key!r}: {count} asset(s)")
        verb = "Removed" if apply else "Would remove"
        self.stdout.write(self.style.WARNING(
            f"{verb} {removed} value(s) across {touched} asset(s)."
        ))
        if not apply:
            self.stdout.write("Dry run only. Re-run with --apply after taking a DB backup.")
