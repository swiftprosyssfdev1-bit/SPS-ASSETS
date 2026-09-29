"""
Read-only report on the 'Workstation' AssetCategory row(s).

Makes NO writes to the database. Safe to run anytime.

Usage:
    Copy this file to assets/management/commands/inspect_workstation_category.py
    Then run:
        python manage.py inspect_workstation_category
"""
from django.core.management.base import BaseCommand
from assets.models import AssetCategory, Asset


class Command(BaseCommand):
    help = "Report every AssetCategory named 'Workstation' and what references it. Read-only."

    def handle(self, *args, **options):
        matches = AssetCategory.objects.filter(name__iexact="workstation")

        if not matches.exists():
            self.stdout.write(self.style.SUCCESS(
                "No AssetCategory row named 'Workstation' was found. Nothing to clean up."
            ))
            return

        for cat in matches:
            assets_qs = Asset.objects.filter(category=cat)
            active_count = assets_qs.filter(is_active=True).count()
            inactive_count = assets_qs.filter(is_active=False).count()
            total = assets_qs.count()

            self.stdout.write(self.style.WARNING(f"\n=== AssetCategory id={cat.pk} name='{cat.name}' ==="))
            self.stdout.write(f"  icon: {cat.icon!r}")
            self.stdout.write(f"  description: {cat.description!r}")
            self.stdout.write(f"  show_under_other: {cat.show_under_other}")
            self.stdout.write(f"  Asset rows referencing this category: {total} "
                               f"({active_count} active, {inactive_count} inactive)")

            if total == 0:
                self.stdout.write(self.style.SUCCESS(
                    "  -> Zero Asset references. Safe to remove this category row "
                    "(Django's on_delete=PROTECT will also refuse deletion if this is wrong)."
                ))
                continue

            self.stdout.write(self.style.ERROR(
                "  -> Has live Asset references. DO NOT DELETE. Listing them below:"
            ))
            for a in assets_qs.order_by("asset_tag")[:2000]:
                linked = {
                    k: v for k, v in (a.extra_details or {}).items()
                    if v not in (None, "", [])
                }
                self.stdout.write(
                    f"    - id={a.pk} tag={a.asset_tag} name={a.name!r} "
                    f"active={a.is_active} branch={a.branch_id} extra_details={linked}"
                )

            if total > 2000:
                self.stdout.write(f"    ... and {total - 2000} more (truncated at 2000).")

        self.stdout.write(self.style.WARNING(
            "\nNote: relations.py's 'Connected Workstations' cross-reference panels query "
            "Asset.objects.filter(category__name=\"Workstation\") directly, independent of "
            "this category appearing in menus/dropdowns. That logic is unaffected by hiding "
            "the category from UI listings, and must be migrated separately if/when these "
            "rows move to a dedicated Workstation model."
        ))
