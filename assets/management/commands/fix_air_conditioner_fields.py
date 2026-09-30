"""
One-off fix: give the Air Conditioner category its full set of Category
Builder fields.

WHY THIS IS NEEDED
------------------
Air Conditioner was seeded with a single AssetField row ("Capacity /
Location"). The moment a category has ANY AssetField row it is treated as
"intentionally configured": get_builder_layout() then builds the Add/Edit
form ONLY from the builder's rows, and the old hard-coded AC list
(Status, Brand, Last Service Date) in category_fields.get_common_fields()
is overridden. Result: the Field Builder page, and the Add/Edit form, show
only Capacity / Location.

This command adds the missing rows so they appear in the Field Builder and
can be renamed / reordered / hidden from the UI like any other category:

    Name               -> Asset.name              (text)
    Brand              -> Asset.brand             (text)
    Capacity / Location-> extra_details           (already exists, kept)
    Status             -> Asset.status            (bound column; the
                          allowed values for A/C stay Working / Not Working /
                          Under Service / Scrap, from views.STATUS_VALUES_BY_CATEGORY)
    Last Service Date  -> Asset.last_service_date (date)

Nothing is deleted. The existing Capacity / Location row (and the data
stored under its key) is untouched. Safe to run more than once.

Usage:
    python manage.py fix_air_conditioner_fields            # dry run
    python manage.py fix_air_conditioner_fields --apply    # save changes
"""
import json

from django.core.management.base import BaseCommand
from django.db import transaction

from assets.models import AssetCategory, AssetField, ConfigAuditLog

# (key, label, field_type, width) in the order they should appear.
# Keys/labels are chosen so category_fields._BUILDER_COLUMN_MAP binds them
# to the real Asset columns ("name", "brand", "status", "last service date").
DESIRED_FIELDS = [
    ("name",              "Name",              "text", 6),
    ("brand",             "Brand",             "text", 6),
    ("capacity_location", "Capacity / Location", "text", 6),
    ("status",            "Status",            "text", 6),
    ("last_service_date", "Last Service Date", "date", 6),
]


class Command(BaseCommand):
    help = "Add the missing Name/Brand/Status/Last Service Date builder fields to Air Conditioner."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually save the changes. Without this flag, only prints what would happen.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        apply_changes = options["apply"]

        try:
            category = AssetCategory.objects.get(name__iexact="Air Conditioner")
        except AssetCategory.DoesNotExist:
            self.stdout.write(self.style.ERROR("No 'Air Conditioner' category found — nothing to do."))
            return

        existing = {f.key: f for f in AssetField.objects.filter(category=category)}
        self.stdout.write(f"Air Conditioner currently has {len(existing)} field row(s): "
                          f"{', '.join(existing) or '(none)'}")

        created = reordered = reactivated = 0

        for order, (key, label, ftype, width) in enumerate(DESIRED_FIELDS):
            field = existing.get(key)

            if field is None:
                self.stdout.write(f"  {'CREATE' if apply_changes else 'would create'}: {label!r} (key={key}, {ftype}, order={order})")
                created += 1
                if apply_changes:
                    field = AssetField.objects.create(
                        category=category, key=key, label=label, field_type=ftype,
                        width=width, required=False, display_order=order, is_active=True,
                    )
                    ConfigAuditLog.objects.create(
                        changed_by=None, action="create", model_name="AssetField",
                        object_id=field.pk, object_repr=str(field),
                        diff=json.dumps({"source": "fix_air_conditioner_fields"}),
                    )
                continue

            # Row exists: never change its label/type (admin may have customised
            # it). Only make sure it is switched on and in the right position.
            changes = []
            if not field.is_active:
                changes.append("re-activate")
                reactivated += 1
                if apply_changes:
                    field.is_active = True
            if field.display_order != order:
                changes.append(f"order {field.display_order} -> {order}")
                reordered += 1
                if apply_changes:
                    field.display_order = order
            if changes:
                self.stdout.write(f"  {'UPDATE' if apply_changes else 'would update'}: {field.label!r} ({', '.join(changes)})")
                if apply_changes:
                    field.save()
            else:
                self.stdout.write(f"  ok: {field.label!r}")

        verb = "" if apply_changes else "would be "
        self.stdout.write(self.style.SUCCESS(
            f"\nFields {verb}created: {created}, re-activated: {reactivated}, re-ordered: {reordered}."
        ))
        if not apply_changes:
            self.stdout.write("Dry run only. Re-run with --apply to save.")
