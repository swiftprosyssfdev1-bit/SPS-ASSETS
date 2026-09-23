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
    # Inside Cupboard uses a flexible 3-field layout for Add/Edit form.
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
# tracking detail (Status, Brand, Model Number, Serial Number, Current
# Assigned To, Current Location, Linked Workstation, Purchase Date, Last
# Service Date) that a category only sees when it has NOTHING of its own
# configured — the moment a category has its own field set (sheet
# template / curated CATEGORY_FIELDS / discovered from data — see
# get_category_fields), that set already covers everything the admin
# needs to fill in, so none of these generic fields show on top of it.
# No exceptions/overrides: every category follows the same rule, whether
# it's Keyboard's "Keyboard Id, Brand, Status, S.No, Location" or
# Workstation's own "Workstation ID, Employee ID, ... User Id" block —
# only that category's own fields plus Notes/Active are shown.
# ---------------------------------------------------------------------------

DEFAULT_COMMON_FIELDS = [
    "status", "brand", "model_number", "serial_number",
    "current_assigned_to", "current_location", "linked_workstation",
    "purchase_date", "last_service_date",
]


CATEGORY_LABELS = {
    "employee": {
        "tag_label": "Employee Id",
        "name_label": "Employee Name",
        "show_name": True,
    },
    "employee list": {
        "tag_label": "Employee Id",
        "name_label": "Employee Name",
        "show_name": True,
    },
    "keyboard": {
        "tag_label": "Keyboard Id",
        "name_label": "Brand",
        "serial_label": "S.No",
        "location_label": "Location",
    },
    "mouse": {
        "tag_label": "Mouse",
        "name_label": "Brand",
        "serial_label": "S.No",
        "location_label": "Location",
    },
    "monitor": {
        "tag_label": "Monitor No",
        "name_label": "Brand",
        "model_label": "Model",
        "serial_label": "Serial No",
    },
    "cpu / system unit": {
        "tag_label": "System No",
        "name_label": "System Name",
    },
    "cpu - system unit": {
        "tag_label": "System No",
        "name_label": "System Name",
    },
    "hard disk": {
        "tag_label": "Hard disk  Number",
        "name_label": "Hard Disk Name",
        "serial_label": "Serial No",
    },
    "ups": {
        "tag_label": "UPS No",
        "name_label": "Brand",
        "model_label": "Model No",
    },
    "bluetooth device": {
        "tag_label": "Bluetooth No",
        "name_label": "Devices",
        "assigned_label": "User Name",
    },
    "workstation": {
        "tag_label": "Workstation ID",
        "name_label": "Purposes",
        "assigned_label": "Employee Name",
    },
    "it vendor": {
        "tag_label": "S.No",
        "name_label": "Vendor Name",
    },
    "incident register": {
        "tag_label": "S.No",
        "name_label": "Incident Type",
    },
    "project details": {
        "tag_label": "S.No",
        "name_label": "Project Name",
    },
    "project backup": {
        "tag_label": "Hard disk Name",
        "name_label": "Projects",
        "assigned_label": "Project Manager",
    },
}


def get_category_field_labels(category):
    cat_name = category.name if hasattr(category, "name") else str(category or "")
    norm = cat_name.strip().lower()
    return CATEGORY_LABELS.get(norm, {})


def get_common_fields(category):
    """Which of the toggleable common Asset fields to show on the
    Add/Edit form for this category. A category with its own field set
    (see get_category_fields) gets none of these — its own fields are
    the whole form. Only a category with nothing of its own configured
    falls back to the full generic set, so its Add Asset form isn't
    empty.
    """
    if category is None:
        return DEFAULT_COMMON_FIELDS
    if get_category_fields(category):
        return []
    return DEFAULT_COMMON_FIELDS


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
    """category: AssetCategory instance or category name string or None. Returns list of field dicts.

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

    cat_name = category.name if hasattr(category, "name") else str(category or "")
    from .sheet_templates import get_template_for_category
    template = get_template_for_category(cat_name)
    if template:
        is_employee = is_employee_category(cat_name)
        used_employee_keys = employee_repeat_keys_in_use(category) if (is_employee and hasattr(category, "pk")) else set()
        fields = []
        for col in template:
            if col["header"] is None:
                continue
            if _is_sensitive_field(col["header"]):
                continue
            if is_employee and col["col_index"] >= EMPLOYEE_MAIN_COLUMNS:
                if col["key"] not in used_employee_keys:
                    continue
            fields.append({"name": col["key"], "label": col["header"], "type": "text"})
        if fields:
            return fields

    curated = CATEGORY_FIELDS.get(cat_name.strip().lower(), [])
    if curated:
        return [f for f in curated if not _is_sensitive_field(f["name"])]

    if hasattr(category, "pk"):
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
