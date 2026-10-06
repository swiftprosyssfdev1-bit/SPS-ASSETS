"""
Choose which fields show as columns on a category's List page.
All other fields stay available in the detail (view) page.

Copy to assets/management/commands/trim_list_columns.py

Preview (changes nothing):
    python manage.py trim_list_columns
Apply:
    python manage.py trim_list_columns --apply
Other category / own column list:
    python manage.py trim_list_columns --category "Monitor" --keep "Name,Brand" --apply
"""
from django.core.management.base import BaseCommand, CommandError
from assets.models import AssetCategory, AssetField

DEFAULT_CATEGORY = "CPU / System Unit"
DEFAULT_KEEP = [
    "System No", "System Name", "OS Type", "Processor",
    "HardDisk Size", "RAM Size",
]


def norm(s):
    return " ".join(str(s or "").lower().replace("_", " ").split())


class Command(BaseCommand):
    help = "Set show_in_list on/off for a category's fields (detail page unchanged)."

    def add_arguments(self, parser):
        parser.add_argument("--category", default=DEFAULT_CATEGORY)
        parser.add_argument("--keep", default=",".join(DEFAULT_KEEP),
                            help="Comma-separated field labels to show in the List")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **opts):
        cat = AssetCategory.objects.filter(name__iexact=opts["category"]).first()
        if not cat:
            raise CommandError(f"Category '{opts['category']}' not found")
        keep = {norm(x) for x in opts["keep"].split(",") if x.strip()}

        fields = AssetField.objects.filter(category=cat, is_active=True)
        found = set()
        for f in fields.order_by("display_order", "id"):
            show = norm(f.label) in keep and "password" not in norm(f.label)
            if show:
                found.add(norm(f.label))
            self.stdout.write(f"{'SHOW' if show else 'hide'}  {f.label}")
            if opts["apply"] and f.show_in_list != show:
                f.show_in_list = show
                f.save(update_fields=["show_in_list"])

        missing = keep - found
        if missing:
            self.stdout.write(self.style.WARNING(
                "Not found (check spelling): " + ", ".join(sorted(missing))))
        self.stdout.write(self.style.SUCCESS(
            "Applied." if opts["apply"] else "Preview only. Add --apply to save."))
