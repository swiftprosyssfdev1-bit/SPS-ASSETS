from django.core.management.base import BaseCommand
from assets.models import AssetCategory

DEFAULT_CATEGORIES = [
    ("Employee", "bi-person-badge", "Legacy 'Employee_List' sheet — employee ID / name register"),
    ("Workstation", "bi-pc-display", "Employee workstation (links CPU, monitor, keyboard, mouse, UPS)"),
    ("CPU / System Unit", "bi-cpu", "Desktop CPU / system unit"),
    ("Monitor", "bi-display", "Monitors / screens"),
    ("Keyboard", "bi-keyboard", "Keyboards"),
    ("Mouse", "bi-mouse2", "Mice"),
    ("UPS", "bi-battery-charging", "Uninterruptible power supplies"),
    ("Bluetooth Device", "bi-bluetooth", "Wireless keyboard/mouse combos"),
    ("Hard Disk", "bi-hdd", "Internal / external hard disks"),
    ("Software / OS License", "bi-cd", "Operating systems and licensed software"),
    ("Air Conditioner", "bi-snow", "A/C units"),
    ("Biometric Device", "bi-fingerprint", "Attendance biometric devices"),
    ("Networking Equipment", "bi-router", "Routers, switches, leaseline/broadband"),
    ("Other Asset", "bi-box-seam", "Anything that doesn't fit the categories above"),
    ("Project Details", "bi-kanban", "Legacy 'Project Details' sheet — active project list"),
    ("Project Backup", "bi-archive", "Legacy 'Project backup' sheet — project backup log"),
    ("Inside Cupboard", "bi-box2", "Legacy 'inside the Cupboard' sheet — spare/unlabeled internal HDDs"),
    ("IT Vendor", "bi-truck", "Legacy 'IT_Vendor List' sheet — vendor contacts"),
    ("Incident Register", "bi-exclamation-triangle", "Legacy 'Incident Register' sheet — incident log"),
]


class Command(BaseCommand):
    help = "Seed the default asset categories used by Swift ProSys"

    def handle(self, *args, **options):
        created = 0
        for name, icon, desc in DEFAULT_CATEGORIES:
            obj, was_created = AssetCategory.objects.get_or_create(
                name=name, defaults={"icon": icon, "description": desc}
            )
            if was_created:
                created += 1
        self.stdout.write(self.style.SUCCESS(
            f"Done. {created} new categories created ({AssetCategory.objects.count()} total)."
        ))
