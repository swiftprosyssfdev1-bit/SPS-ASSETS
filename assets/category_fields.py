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
    # Inside Cupboard uses a flexible 4-column layout (no rigid template).
    # These fields appear in the Add/Edit form; the detail table uses the
    # special is_cupboard branch in category_detail.html instead.
    "inside cupboard": [
        {"name": "item_type", "label": "Item Type", "type": "select",
         "options": ["HDD", "RAM", "Accessory / Box", "Cable", "Peripheral", "Other"]},
        {"name": "capacity_size", "label": "Capacity / Size", "type": "text"},
        {"name": "condition_notes", "label": "Condition / Notes", "type": "text"},
    ],
}


# Column names that used to be force-hidden everywhere (import, detail
# tables, discovery) because they store real secrets in plain text (e.g.
# the "CPU - System Unit" sheet's system login Password, "Software and OS"
# sheet's Product Key). That filter is intentionally OFF now — this is an
# internal register and these values are needed on-screen — so this set is
# empty and _is_sensitive_field() always returns False. Left in place (and
# still used by scrub_legacy_secrets.py) so it's a one-line change to turn
# hiding back on for some or all of these names if that's ever needed again.
SENSITIVE_FIELD_NAMES = set()


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


# Workstation fields that should render as a searchable combo box (type to
# search an existing asset, pick it, its Tag gets stored) instead of a
# free-text box — keyed by the field's normalized label so it matches
# regardless of the exact header text a sheet template gave it (e.g.
# "CPU Number" / "Cpu Number" / "CPU_Number" all normalize the same way).
# Value = the AssetCategory name to search within.
WORKSTATION_LOOKUP_FIELDS = {
    "cpu number": "CPU / System Unit",
    "monitor number": "Monitor",
    "keyboard number": "Keyboard",
    "mouse number": "Mouse",
    "ups no": "UPS",
    "ups no.": "UPS",
    "employee id": "Employee",
}


def workstation_lookup_category(field_label):
    """Returns the AssetCategory name to search for this Workstation field,
    or None if it's a plain text field."""
    return WORKSTATION_LOOKUP_FIELDS.get(_normalize_field_name(field_label))


# ---------------------------------------------------------------------------
# Per-category "common field" visibility for the Add/Edit Asset form.
#
# Category, Branch, Asset Tag, Name, Notes and Active are universal — every
# asset needs them, so they always show. Everything below is hardware/
# tracking detail that only some categories actually use; this map says
# exactly which of those to show for each category. A category not listed
# here falls back to DEFAULT_COMMON_FIELDS (the full physical-asset set),
# so a brand-new category still gets a sensible form until it's tailored
# here. Edit this dict to change what a given category's form shows.
# ---------------------------------------------------------------------------

DEFAULT_COMMON_FIELDS = [
    "status", "brand", "model_number", "serial_number",
    "current_assigned_to", "current_location", "linked_workstation",
    "purchase_date", "last_service_date",
]

COMMON_FIELDS_BY_CATEGORY = {
    # Plain information registers — no physical hardware to track.
    "employee": ["status"],
    "employee list": ["status"],
    "it vendor": ["status"],
    "incident register": ["status"],
    "project details": ["status"],
    "project backup": ["status"],
    # These have their own detail fields (processor/RAM, license type, etc.)
    # via CATEGORY_FIELDS/sheet templates, so they only need Status plus
    # who/where it's assigned — not Brand/Model/Serial/Purchase/Service.
    "workstation": ["status", "current_assigned_to", "current_location"],
    "cpu / system unit": ["status", "current_assigned_to", "current_location"],
    "software / os license": ["status", "current_assigned_to"],
}


def get_common_fields(category_name):
    """Which of the toggleable common Asset fields to show on the
    Add/Edit form for this category name."""
    key = str(category_name or "").strip().lower()
    return COMMON_FIELDS_BY_CATEGORY.get(key, DEFAULT_COMMON_FIELDS)


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

    # If this category is explicitly listed in CATEGORY_FIELDS, use that
    # definition (skipping raw extra_details discovery). This lets us give
    # Inside Cupboard — and similar categories — a curated, clean form
    # instead of surfacing whatever raw Excel column names were imported.
    curated = CATEGORY_FIELDS.get(category.name.strip().lower(), [])
    if curated:
        return [f for f in curated if not _is_sensitive_field(f["name"])]

    discovered = _discover_fields_from_data(category)
    if discovered:
        return discovered
    return []


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
