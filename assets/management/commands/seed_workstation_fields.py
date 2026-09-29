"""
Seeds WorkstationField rows matching the 11 columns the legacy Workstation
sheet/extra_details already use, so:
  - the new Workstation Field Builder isn't empty on day one
  - migrate_workstation_data.py (run after this) has real WorkstationField.key
    values to write resolved links into

Safe to re-run: skips any field whose key already exists.

Usage:
    Copy to assets/management/commands/seed_workstation_fields.py
    python manage.py seed_workstation_fields
"""
import re

from django.core.management.base import BaseCommand
from assets.models import WorkstationField


def _auto_key(label, existing_keys):
    base = re.sub(r"[^a-z0-9]+", "_", str(label or "").lower()).strip("_")[:90] or "field"
    key = base
    n = 2
    while key in existing_keys:
        key = f"{base}_{n}"
        n += 1
    return key


# (label, field_type, lookup_category, lookup_multi, display_order)
SEED_FIELDS = [
    ("Employee",          "lookup", "Employee",            False, 10),
    ("CPU",               "lookup", "CPU / System Unit",   False, 20),
    ("Monitor",           "lookup", "Monitor",             True,  30),
    ("Keyboard",          "lookup", "Keyboard",             False, 40),
    ("Mouse",             "lookup", "Mouse",                False, 50),
    ("UPS",               "lookup", "UPS",                  True,  60),
    ("Product Id",        "text",   "",                     False, 70),
    ("Cd Key",            "text",   "",                     False, 80),
    ("Operating System",  "text",   "",                     False, 90),
    ("Purposes",          "text",   "",                     False, 100),
    ("User Id",           "text",   "",                     False, 110),
]


class Command(BaseCommand):
    help = "Seed WorkstationField rows matching the legacy Workstation columns. Safe to re-run."

    def handle(self, *args, **options):
        existing_keys = set(WorkstationField.objects.values_list("key", flat=True))
        created = 0
        for label, field_type, lookup_category, lookup_multi, order in SEED_FIELDS:
            if WorkstationField.objects.filter(label=label).exists():
                self.stdout.write(f"  skip (already exists): {label}")
                continue
            key = _auto_key(label, existing_keys)
            existing_keys.add(key)
            WorkstationField.objects.create(
                label=label, key=key, field_type=field_type,
                lookup_category=lookup_category, lookup_multi=lookup_multi,
                display_order=order,
            )
            created += 1
            self.stdout.write(self.style.SUCCESS(f"  created: {label} (key={key})"))

        self.stdout.write(self.style.SUCCESS(f"\nDone. {created} field(s) created."))
