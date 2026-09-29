"""
Management command: seed_dynamic_fields

Seeds AssetField rows from the existing hard-coded category_fields.py /
sheet_templates.py definitions into the database, so the Category Builder UI
can manage them going forward.

Safe to run multiple times (idempotent — skips any category that already has
DB field rows). Use --dry-run to preview what would happen without writing.

Usage:
    python manage.py seed_dynamic_fields
    python manage.py seed_dynamic_fields --dry-run
    python manage.py seed_dynamic_fields --category "Monitor"
"""
import re
from django.core.management.base import BaseCommand
from assets.models import AssetCategory, AssetField
from assets.category_fields import get_category_fields as _get_legacy_fields


def _slugify(text):
    """Convert a field label to a slug-style key, e.g. 'Serial No.' -> 'serial_no'"""
    text = str(text or "").lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text[:100] or "field"


def _unique_key(base_key, existing_keys):
    """Ensure uniqueness within a category by appending _2, _3 ... if needed."""
    key = base_key
    n = 2
    while key in existing_keys:
        key = f"{base_key}_{n}"
        n += 1
    return key


class Command(BaseCommand):
    help = "Seed AssetField rows from existing hard-coded category_fields.py definitions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be seeded without writing anything.",
        )
        parser.add_argument(
            "--category",
            type=str,
            default=None,
            help="Only seed this category name (default: all categories).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        only_cat = options["category"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no database writes."))

        categories = AssetCategory.objects.all().order_by("name")
        if only_cat:
            categories = categories.filter(name__iexact=only_cat)

        total_cats = 0
        skipped_cats = 0
        seeded_fields = 0
        skipped_fields = 0

        for cat in categories:
            # Safeguard: skip Workstation — it must remain its own separate module.
            if cat.name.strip().lower() == "workstation":
                self.stdout.write(
                    f"  SKIP   {cat.name!r} — Workstation is excluded from the field builder."
                )
                continue

            # Once a category has ANY AssetField rows it is 'intentionally configured'.
            # We skip it so we don't overwrite manual customisations.
            if AssetField.objects.filter(category=cat).exists():
                self.stdout.write(f"  SKIP   {cat.name!r} — already has DB field rows.")
                skipped_cats += 1
                continue

            # Use the LEGACY helper (reads sheet_templates / CATEGORY_FIELDS / discovery)
            # but only for the bootstrap pass — after seeding, DB rows take priority.
            legacy_fields = _get_legacy_fields(cat)
            if not legacy_fields:
                self.stdout.write(
                    f"  SKIP   {cat.name!r} — no legacy fields found (will use common fields)."
                )
                skipped_cats += 1
                continue

            self.stdout.write(f"  SEED   {cat.name!r} — {len(legacy_fields)} field(s):")
            total_cats += 1
            existing_keys = set()

            for order, f in enumerate(legacy_fields):
                raw_label = f.get("label") or f.get("name") or ""
                raw_key   = f.get("name") or _slugify(raw_label)
                ftype     = f.get("type", "text")
                options_list = f.get("options", [])

                # Map legacy types to our new type choices
                type_map = {
                    "text":     "text",
                    "number":   "number",
                    "date":     "date",
                    "select":   "select",
                    "textarea": "textarea",
                    "email":    "email",
                    "url":      "url",
                }
                db_type = type_map.get(ftype, "text")

                # Generate stable key from the existing name/key, slugified
                base_key = _slugify(raw_key)
                unique_k = _unique_key(base_key, existing_keys)
                existing_keys.add(unique_k)

                action_label = "  (skip)" if dry_run else "  CREATE"
                self.stdout.write(
                    f"    {action_label} field: {raw_label!r} key={unique_k!r} type={db_type}"
                )

                if not dry_run:
                    field_obj, created = AssetField.objects.get_or_create(
                        category=cat,
                        key=unique_k,
                        defaults={
                            "label":         raw_label or unique_k,
                            "field_type":    db_type,
                            "width":         6,
                            "required":      False,
                            "display_order": order,
                            "is_active":     True,
                        },
                    )
                    if created:
                        seeded_fields += 1
                        # Create dropdown options if any
                        for opt_idx, opt in enumerate(options_list):
                            opt_label = opt if isinstance(opt, str) else str(opt)
                            from assets.models import AssetFieldOption
                            AssetFieldOption.objects.get_or_create(
                                field=field_obj,
                                label=opt_label,
                                defaults={
                                    "value":         opt_label,
                                    "display_order": opt_idx,
                                    "is_active":     True,
                                },
                            )
                    else:
                        skipped_fields += 1
                        self.stdout.write(f"       (already existed — skipped)")
                else:
                    seeded_fields += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Done. Categories processed: {total_cats}, skipped: {skipped_cats}. "
            f"Fields {'would be ' if dry_run else ''}created: {seeded_fields}, skipped: {skipped_fields}."
        ))
