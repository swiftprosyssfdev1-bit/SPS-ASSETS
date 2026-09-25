"""
One-off fix: Project Details records were imported with the auto-generated
filler name "Project Details <project>" (shown in Recent Changes, search and
page titles). The project's real name is its asset tag, so set name = tag.

Usage:
    python manage.py fix_project_details_names            # dry run
    python manage.py fix_project_details_names --apply    # save the changes
"""
from django.core.management.base import BaseCommand

from assets.models import Asset, AssetCategory


class Command(BaseCommand):
    help = "Set Project Details names to the project name (asset tag)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Actually save the changes.")

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        try:
            category = AssetCategory.objects.get(name__iexact="Project Details")
        except AssetCategory.DoesNotExist:
            self.stdout.write(self.style.ERROR("No 'Project Details' category found."))
            return
        count = 0
        for asset in Asset.objects.filter(category=category).order_by("asset_tag"):
            if asset.name == asset.asset_tag:
                continue
            count += 1
            self.stdout.write(f"{'Updating' if apply_changes else 'Would update'}: {asset.name!r} -> {asset.asset_tag!r}")
            if apply_changes:
                asset.name = asset.asset_tag
                asset.save()
        self.stdout.write(self.style.SUCCESS(f"\n{'Updated' if apply_changes else 'Would update'} {count} record(s)."))
        if not apply_changes and count:
            self.stdout.write(self.style.WARNING("Dry run only — re-run with --apply to save."))
