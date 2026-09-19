"""
Per-category dynamic field configuration.

Each category maps to a list of extra fields shown only when that
category is selected. These are saved into Asset.extra_details (JSON),
so no schema/migration is needed to add or change fields here.

Field dict keys:
    name     - key stored in extra_details (also the POST field name)
    label    - shown in the form / table header
    type     - "text" | "number" | "date" | "select"
    options  - required when type == "select"

Match key = category name, lowercased & stripped. Unknown/custom
categories simply get no extra fields (common fields only) until
you add an entry here.
"""

import re

CATEGORY_FIELDS = {
    "keyboard": [
        {"name": "keyboard_type", "label": "Keyboard Type", "type": "select",
         "options": ["Mechanical", "Membrane", "Wireless"]},
        {"name": "connection_type", "label": "Connection Type", "type": "select",
         "options": ["USB", "Bluetooth", "Wireless"]},
        {"name": "layout", "label": "Layout", "type": "text"},
    ],
    "mouse": [
        {"name": "mouse_type", "label": "Mouse Type", "type": "select",
         "options": ["Wired", "Wireless", "Bluetooth"]},
        {"name": "connection_type", "label": "Connection Type", "type": "select",
         "options": ["USB", "Bluetooth", "Wireless"]},
        {"name": "dpi", "label": "DPI", "type": "number"},
        {"name": "buttons", "label": "Buttons", "type": "number"},
    ],
    "monitor": [
        {"name": "screen_size", "label": "Screen Size", "type": "text"},
        {"name": "resolution", "label": "Resolution", "type": "text"},
        {"name": "refresh_rate", "label": "Refresh Rate", "type": "text"},
        {"name": "panel_type", "label": "Panel Type", "type": "select",
         "options": ["IPS", "TN", "VA", "OLED"]},
        {"name": "connection_type", "label": "Connection Type", "type": "text"},
    ],
    "hard disk": [
        {"name": "storage_capacity", "label": "Storage Capacity", "type": "text"},
        {"name": "disk_type", "label": "Disk Type", "type": "select",
         "options": ["HDD", "SSD", "NVMe"]},
        {"name": "interface", "label": "Interface", "type": "select",
         "options": ["SATA", "USB", "NVMe"]},
        {"name": "health_status", "label": "Health Status", "type": "text"},
        {"name": "rpm", "label": "RPM", "type": "text"},
    ],
    "laptop": [
        {"name": "processor", "label": "Processor", "type": "text"},
        {"name": "ram", "label": "RAM", "type": "text"},
        {"name": "storage", "label": "Storage", "type": "text"},
        {"name": "operating_system", "label": "Operating System", "type": "text"},
        {"name": "battery_health", "label": "Battery Health", "type": "text"},
    ],
    "workstation": [
        {"name": "processor", "label": "Processor", "type": "text"},
        {"name": "ram", "label": "RAM", "type": "text"},
        {"name": "storage", "label": "Storage", "type": "text"},
        {"name": "operating_system", "label": "Operating System", "type": "text"},
        {"name": "workstation_location", "label": "Workstation Location", "type": "text"},
    ],
    "cpu / system unit": [
        {"name": "processor", "label": "Processor", "type": "text"},
        {"name": "ram", "label": "RAM", "type": "text"},
        {"name": "storage", "label": "Storage", "type": "text"},
        {"name": "operating_system", "label": "Operating System", "type": "text"},
    ],
    "air conditioner": [
        {"name": "ac_type", "label": "AC Type", "type": "select",
         "options": ["Split", "Window", "Cassette"]},
        {"name": "capacity_tonnage", "label": "Capacity (Tonnage)", "type": "text"},
        {"name": "installation_location", "label": "Installation Location", "type": "text"},
    ],
    "ups": [
        {"name": "va_rating", "label": "VA Rating", "type": "text"},
        {"name": "battery_type", "label": "Battery Type", "type": "text"},
        {"name": "backup_time", "label": "Backup Time", "type": "text"},
        {"name": "battery_replacement_date", "label": "Battery Replacement Date", "type": "date"},
    ],
    "software / os license": [
        {"name": "license_type", "label": "License Type", "type": "select",
         "options": ["Perpetual", "Subscription", "OEM"]},
        {"name": "expiry_date", "label": "Expiry Date", "type": "date"},
        {"name": "number_of_users", "label": "Number of Users", "type": "number"},
    ],
    "it vendor": [
        {"name": "Mobile No", "label": "Mobile No", "type": "text"},
        {"name": "Types of Service", "label": "Types of Service", "type": "text"},
        {"name": "Details 1", "label": "Details 1", "type": "text"},
        {"name": "Details 2", "label": "Details 2", "type": "text"},
    ],
}


# Column names that must NEVER be surfaced as a visible extra_details field,
# no matter what header text the source data used. These come from legacy
# sheets that stored real secrets in plain text (e.g. the "CPU - System
# Unit" sheet's system login Password, "Software and OS" sheet's Product
# Key). Matched case-insensitively against the raw header text kept as the
# extra_details key. If a row's extra_details already contains one of these
# keys (e.g. from data imported before this fix), it's hidden from the
# dynamically-discovered field list here, but the raw value MAY still be
# present in the JSON in the DB — that data should be scrubbed separately
# (see fix_legacy_secrets management step) rather than relying on this UI
# filter alone.
SENSITIVE_FIELD_NAMES = {
    "password", "passwd", "pwd", "product key", "cd key", "license key",
    "activation key", "serial key", "api key", "secret",
}


def _normalize_field_name(name):
    """'License_Key', 'license-key', 'Password__2' -> 'license key' / 'password'.
    Underscores/hyphens/extra spaces and the internal '__N' duplicate-column
    suffix are ignored, so a secret can't slip through on spelling alone."""
    text = re.sub(r"__\d+$", "", str(name or "").strip())
    return re.sub(r"[\s_\-]+", " ", text).strip().lower()


def _is_sensitive_field(name):
    return _normalize_field_name(name) in SENSITIVE_FIELD_NAMES


# The Employee sheet template lists the roster in two side-by-side blocks:
# EmployeeName / Employee Id / Status, then Employee Name / Id / Status again.
# Everything is stored under the first three columns, so the second block is
# normally empty and must not show up as extra fields/columns.
EMPLOYEE_MAIN_COLUMNS = 3


def is_employee_category(name):
    return str(name or "").strip().lower() in {"employee", "employee list"}


def employee_repeat_keys_in_use(category):
    """extra_details keys that actually hold a value for this category."""
    from .models import Asset  # local import: avoids app-loading order issue

    used = set()
    for extra in Asset.objects.filter(category=category, is_active=True).values_list(
        "extra_details", flat=True
    ):
        for k, v in (extra or {}).items():
            if str(v or "").strip():
                used.add(k)
    return used


def get_category_fields(category):
    """category: AssetCategory instance or None. Returns list of field dicts.

    Priority order:
      1. This category's own sheet template (sheet_templates.py), if one is
         registered — the EXACT original field names, in the EXACT original
         order, from "system details updated 4.xlsx". This is what makes
         the Add/Edit form and detail table for e.g. CPU / System Unit show
         "System No, System Name, Password, OS Type, ..." rather than a
         generic/common set of fields.
      2. The REAL columns that exist in this category's data (so a bulk
         import into a category with no registered template — Air
         Conditioner, Biometric Device, etc. — still shows whatever columns
         its source data really had).
      3. The curated CATEGORY_FIELDS guesses below, only as a starting point
         for a brand-new category with no assets yet and no template, so the
         Add Asset form isn't empty.
    """
    if not category:
        return []

    from .sheet_templates import get_template_for_category
    template = get_template_for_category(category.name)
    if template:
        is_employee = is_employee_category(category.name)
        used_employee_keys = employee_repeat_keys_in_use(category) if is_employee else set()
        fields = []
        for col in template:
            if col["header"] is None:
                # Blank-header column from the original sheet: keep its
                # position/data (internal key), but there's no real field
                # name to show as a form label — skip it in the human-
                # facing Add/Edit form rather than inventing "Column D".
                continue
            if _is_sensitive_field(col["header"]):
                continue
            if is_employee and col["col_index"] >= EMPLOYEE_MAIN_COLUMNS:
                # Repeat block (Employee Name / Id / Status again) — only
                # shown if some employee really has data stored under it.
                if col["key"] not in used_employee_keys:
                    continue
            fields.append({"name": col["key"], "label": col["header"], "type": "text"})
        if fields:
            return fields

    discovered = _discover_fields_from_data(category)
    if discovered:
        return discovered
    return [
        f for f in CATEGORY_FIELDS.get(category.name.strip().lower(), [])
        if not _is_sensitive_field(f["name"])
    ]


def _discover_fields_from_data(category):
    """Collects every key actually present in extra_details across this
    category's assets, in first-seen order, as plain text fields. This is
    what makes each category's page reflect whatever columns its source
    data really had, instead of a fixed guess."""
    from .models import Asset  # local import: avoids any app-loading order issue

    keys_in_order = []
    seen = set()
    for extra in Asset.objects.filter(category=category, is_active=True).values_list(
        "extra_details", flat=True
    ):
        if not extra:
            continue
        for key in extra.keys():
            if key in seen or _is_sensitive_field(key):
                continue
            if key.startswith("_blank"):
                # Internal-only placeholder key for a blank-header column
                # from a sheet template (see sheet_templates.py) — it has
                # no real field name to show as a label here, so it's left
                # out of discovery too (the value/position is still kept
                # for export via the category's own sheet template).
                continue
            seen.add(key)
            keys_in_order.append(key)
    return [{"name": k, "label": k, "type": "text"} for k in keys_in_order]
