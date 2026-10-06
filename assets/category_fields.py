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
    # The project's name IS its unique tag (the sheet's "Project Name"
    # column is imported as the asset tag), so there is one Project Name
    # box — the separate Name field is hidden and kept equal to it.
    "project details": {
        "tag_label": "Project Name",
        "name_label": "Project Name",
        "show_name": False,
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


# Categories whose own sheet has a column that IS the record's status (the
# importer feeds it into Asset.status). On the Add/Edit form that column is
# shown as the real Status dropdown (limited to statuses that suit the
# category) instead of a free-text box, so what's picked here is what the
# category list page's status badge shows. Value = normalized header text.
STATUS_DRIVER_COLUMN = {
    "keyboard": "status",
    "mouse": "status",
    "hard disk": "status",
    "employee": "status",
    "employee list": "status",
    "project details": "status",
    "monitor": "condition",
    "ups": "condition",
    "cpu / system unit": "condition",
    "cpu - system unit": "condition",
}

# Categories with no status column in their sheet that still track a status.
STATUS_ONLY_CATEGORIES = {"incident register"}


def status_driver_column(category):
    """Normalized header of the sheet column that drives Asset.status for this
    category (e.g. "status", "condition"), or None."""
    cat_name = category.name if hasattr(category, "name") else str(category or "")
    return STATUS_DRIVER_COLUMN.get(cat_name.strip().lower())


def get_common_fields(category):
    """Which of the toggleable common Asset fields to show on the
    Add/Edit form for this category. A category with its own field set
    (see get_category_fields) gets none of these — its own fields are
    the whole form. Only a category with nothing of its own configured
    falls back to the full generic set, so its Add Asset form isn't
    empty.

    Air Conditioner is the one exception: it has its own curated
    "Capacity / Location" field (see CATEGORY_FIELDS above) but should
    still show the real Status/Brand/Current Location/Last Service Date
    fields alongside it, rather than losing them the way a fully
    self-contained category (Keyboard, Inside Cupboard, ...) does.
    """
    if category is None:
        return DEFAULT_COMMON_FIELDS
    cat_name = category.name if hasattr(category, "name") else str(category or "")
    if status_driver_column(category) or cat_name.strip().lower() in STATUS_ONLY_CATEGORIES:
        return ["status"]
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


def is_category_db_configured(category):
    """
    Returns True if this category has been intentionally configured via the
    Category Builder (i.e. ANY AssetField row exists for it, even inactive).
    Once True, the hard-coded sheet_templates/CATEGORY_FIELDS fallbacks are
    never used for this category — the DB is the sole source of truth.
    """
    if not category or not hasattr(category, "pk") or not category.pk:
        return False
    from .models import AssetField  # local import: avoids app-loading order issue
    return AssetField.objects.filter(category=category).exists()


def get_category_fields(category):
    """category: AssetCategory instance or category name string or None. Returns list of field dicts.

    Priority order:
      1. DB AssetField rows — if ANY row exists for this category (even inactive),
         the category is 'intentionally configured' and we only return ACTIVE DB
         rows. No fallback to sheet_templates or CATEGORY_FIELDS is ever done
         once a category enters the builder.
      2. This category's own sheet template (sheet_templates.py), if one is
         registered — the EXACT original field names, in the EXACT original
         order, from "system details updated 4.xlsx".
      3. The REAL columns that exist in this category's data (so a bulk
         import into a category with no registered template still shows
         whatever columns its source data really had).
      4. The curated CATEGORY_FIELDS guesses below, only as a starting point
         for a brand-new category with no assets yet and no template.
    """
    if not category:
        return []

    cat_name = category.name if hasattr(category, "name") else str(category or "")

    # Priority 1: DB-configured fields (category builder)
    if hasattr(category, "pk") and category.pk:
        from .models import AssetField  # local import
        if AssetField.objects.filter(category=category).exists():
            # Category is intentionally configured — only active fields, in order
            active_fields = (
                AssetField.objects.filter(category=category, is_active=True)
                    .order_by("display_order", "id")
            )
            return [f.as_field_dict() for f in active_fields]

    # Priority 2: sheet template (existing behaviour, unchanged)
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

    # Priority 3: curated CATEGORY_FIELDS
    curated = CATEGORY_FIELDS.get(cat_name.strip().lower(), [])
    if curated:
        return [f for f in curated if not _is_sensitive_field(f["name"])]

    # Priority 4: discover from existing data
    if hasattr(category, "pk"):
        discovered = _discover_fields_from_data(category)
        if discovered:
            return discovered
    return []


def get_workstation_fields():
    """Active WorkstationField rows as export/sample/form-ready field dicts,
    in display order — the single source of truth for the Workstation
    module's own columns (mirrors get_category_fields()'s DB-priority
    branch, but Workstation has no AssetCategory-style fallback chain:
    it is always and only configured through WorkstationField)."""
    from .models import WorkstationField  # local import: avoids app-loading order issue

    return [
        f.as_field_dict()
        for f in WorkstationField.objects.filter(is_active=True).order_by("display_order", "id")
    ]


def get_list_display_fields(category):
    """The subset of get_category_fields(category) that should actually be
    drawn as columns on the category's list page:

    - Respects each field's show_in_list flag (DB-configured fields only;
      legacy dicts from sheet templates / curated CATEGORY_FIELDS / data
      discovery have no such flag, so they default to shown, matching
      today's behaviour for them).
    - Drops whichever field feeds Asset.status for this category (see
      STATUS_DRIVER_COLUMN) since that value is already rendered as the
      Status badge column — showing it again as a plain text column would
      just duplicate it.
    """
    driver = status_driver_column(category)
    fields = []
    for f in get_category_fields(category):
        if f.get("show_in_list", True) is False:
            continue
        if driver and _normalize_field_name(f.get("label", "")) == driver:
            continue
        fields.append(f)
    return fields


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


# ---------------------------------------------------------------------------
# Reading a category field's value off an asset
# ---------------------------------------------------------------------------
def _norm_key(text):
    """'S.No' / 's_no' / 'S No' -> 'sno' (letters+digits only, lower-case)."""
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def resolve_extra_value(extra, field):
    """Value of `field` inside an extra_details dict, tolerating old key
    spellings: the exact key, the field's label, or any key that only differs
    in case / spacing / punctuation ("S.No" vs "s_no"). Older rows were
    imported under the sheet's header text before the Category Builder
    re-keyed the fields, so their values sit under the old key."""
    extra = extra or {}
    name, label = field.get("name"), field.get("label")
    for k in (name, label):
        if k is not None and str(extra.get(k, "")).strip():
            return extra[k]
    targets = {_norm_key(name), _norm_key(label)} - {""}
    for k, v in extra.items():
        if _norm_key(k) in targets and str(v or "").strip():
            return v
    return ""


def rekey_extra(category, extra):
    """Copy of `extra` with old-keyed values moved onto the category's current
    field keys, so Edit forms show them and saving doesn't leave a stale
    duplicate behind. Never overwrites a value already on the current key."""
    out = dict(extra or {})
    fields = get_category_fields(category)
    current_keys = {f.get("name") for f in fields}
    for f in fields:
        name = f.get("name")
        if not name or str(out.get(name, "")).strip():
            continue
        targets = {_norm_key(name), _norm_key(f.get("label"))} - {""}
        for k in list(out.keys()):
            if k == name or k in current_keys or _norm_key(k) not in targets:
                continue
            if str(out[k] or "").strip():
                out[name] = out.pop(k)
                break
    return out


def resolve_field_value(asset, field):
    """Value to show for `field` (a get_category_fields() dict) on `asset`:
    extra_details (any old-key spelling) first, then the asset's own columns
    for fields the category maps onto them (ID / name / brand / model /
    serial / location / assigned-to), because rows created via the Add form
    keep those on the asset itself."""
    value = resolve_extra_value(getattr(asset, "extra_details", None), field)
    if str(value or "").strip():
        return value
    # Workstation rows keep their own data on Workstation.extra_details
    # (keyed by WorkstationField.key), not on Asset.extra_details — so the
    # list page must look there too, or Employee ID / CPU etc. show as "—".
    profile = getattr(asset, "workstation_profile", None)
    if profile is not None:
        wvalue = resolve_extra_value(getattr(profile, "extra_details", None), field)
        if isinstance(wvalue, (list, tuple)):
            wvalue = ", ".join(str(v) for v in wvalue if str(v or "").strip())
        if str(wvalue or "").strip():
            return wvalue
    label = field.get("label")

    lbl = _norm_key(label)
    if not lbl:
        return ""
    # Other Asset: the list page treats the Asset Tag as "ID" and the Name as
    # "Device Name"; Inside Cupboard rows keep their type under "Item Type".
    # Read those real values here when the field has nothing stored itself.
    if str(getattr(asset.category, "name", "") or "").strip().lower() == "other asset":
        if lbl == "id" and asset.asset_tag:
            return asset.asset_tag
        if lbl == "devicename" and asset.name:
            return asset.name
        if lbl == "devicetype":
            _extra = getattr(asset, "extra_details", None) or {}
            for _k, _v in _extra.items():
                if _norm_key(_k) == "itemtype" and str(_v or "").strip():
                    return _v
    bound = _builder_bound_value(asset, field)
    if bound is not None:
        return bound
    cfg = get_category_field_labels(asset.category)

    def is_(key):
        return bool(cfg.get(key)) and _norm_key(cfg[key]) == lbl

    if is_("tag_label"):
        return asset.asset_tag or ""
    if is_("name_label"):
        # a "Brand" column is the asset's brand; the auto-generated name
        # ("Mouse APPLE-2") is only a fallback
        if lbl == "brand":
            return asset.brand or asset.name or ""
        return asset.name or ""
    if is_("model_label"):
        return asset.model_number or ""
    if is_("serial_label"):
        return asset.serial_number or ""
    if is_("location_label"):
        return asset.current_location or ""
    if is_("assigned_label"):
        return asset.current_assigned_to or ""

    # Software / OS License has no label config: its unique ID column is
    # "System No" (see import_utils.SHEET_TAG_COLUMN_HINTS).
    cat_name = str(getattr(asset.category, "name", "") or "").strip().lower()
    if cat_name.startswith("software") and lbl == "systemno":
        return asset.asset_tag or ""
    # Columns that are simply the asset's own date / notes fields.
    if lbl == "lastservicedate" and asset.last_service_date:
        return asset.last_service_date
    if lbl == "notes":
        return asset.notes or ""
    return ""


# ---------------------------------------------------------------------------
# Builder-driven Add/Edit layout
# ---------------------------------------------------------------------------
# For a category configured in the Category Builder (AssetField rows), the
# form shows ONLY what the builder defines. A builder field whose key/label
# is a real Asset column (brand, model, serial, status, location ...) is
# bound to that column; everything else goes to Asset.extra_details.
# Nothing generic (Name, Status, Location, Notes ...) is shown unless the
# builder has a field for it.
_BUILDER_COLUMN_MAP = {
    "tag": "asset_tag", "asset tag": "asset_tag", "asset id": "asset_tag", "asset tag id": "asset_tag", "id": "asset_tag",
    # Employee category: camelCase key "employeename" normalises without a space separator,
    # and "Employee Id" / key "employee_id" normalises to "employee id" → asset_tag.
    "employeename": "name", "employee id": "asset_tag",
    "name": "name", "device name": "name",
    "brand": "brand",
    "model": "model_number", "model no": "model_number", "model number": "model_number",
    "serial": "serial_number", "serial no": "serial_number", "serial number": "serial_number",
    "status": "status", "current status": "status", "condition": "status",
    "assigned to": "current_assigned_to", "current assigned to": "current_assigned_to",
    "user name": "current_assigned_to", "employee name": "current_assigned_to",
    "location": "current_location", "current location": "current_location",
    "linked workstation": "linked_workstation", "workstation": "linked_workstation",
    "purchase date": "purchase_date",
    "last service date": "last_service_date",
    "notes": "notes", "remark": "notes", "remarks": "notes",
}
_LABEL_KEY_FOR_COLUMN = {
    "asset_tag": "tag_label", "name": "name_label", "model_number": "model_label",
    "serial_number": "serial_label", "current_location": "location_label",
    "current_assigned_to": "assigned_label", "status": "status_label",
}


def builder_field_column(field):
    """The real Asset column a Category-Builder field is bound to
    ('asset_tag', 'name', 'status', 'brand', ...), or None when it is a plain
    extra_details field. Same rule get_builder_layout() uses."""
    import re
    for cand in (field.get("name"), field.get("label")):
        norm = re.sub(r"[^a-z0-9]+", " ", str(cand or "").lower()).strip()
        if norm in _BUILDER_COLUMN_MAP:
            return _BUILDER_COLUMN_MAP[norm]
    return None

def get_builder_layout(category):
    """None unless `category` (an AssetCategory instance) is configured in the
    Category Builder. Otherwise a dict describing exactly which Add/Edit form
    controls to show, driven only by the category's ACTIVE AssetField rows."""
    import re
    if not is_category_db_configured(category):
        return None
    common, labels, consumed, required, list_skip = [], {}, set(), [], set()
    core_meta = {}   # builder type / width / label / options for core-column fields
    show_name = show_notes = False
    for f in get_category_fields(category):
        col = None
        for cand in (f.get("name"), f.get("label")):
            norm = re.sub(r"[^a-z0-9]+", " ", str(cand or "").lower()).strip()
            if norm in _BUILDER_COLUMN_MAP:
                col = _BUILDER_COLUMN_MAP[norm]
                break
        if col is None:
            continue
        consumed.add(f["name"])
        core_meta[col] = {
            "type": f.get("type") or "text",
            "width": 12 if f.get("width") == 12 else 6,
            "label": f.get("label") or "",
            "options": list(f.get("options") or []),
        }
        if col in ("asset_tag", "name", "status"):
            list_skip.add(f["name"])   # drawn as the fixed Tag / Name / Status columns
        if col == "name":
            show_name = True
        elif col == "notes":
            show_notes = True
        elif col != "asset_tag" and col not in common:
            common.append(col)
        if col in _LABEL_KEY_FOR_COLUMN:
            labels[_LABEL_KEY_FOR_COLUMN[col]] = f["label"]
        if f.get("required") and col not in ("asset_tag", "name", "notes"):
            required.append([col, f["label"]])
    return {
        "common_fields": common, "labels": labels, "consumed": consumed,
        "show_name": show_name, "show_notes": show_notes, "required_common": required,
        "list_skip": list_skip, "core_meta": core_meta,
    }


def _builder_bound_value(asset, field):
    """For a Category-Builder category, a field that maps onto a real Asset
    column (Brand, Model, Serial ...) reads that column. None = not bound."""
    import re
    if not is_category_db_configured(getattr(asset, "category", None)):
        return None
    for cand in (field.get("name"), field.get("label")):
        norm = re.sub(r"[^a-z0-9]+", " ", str(cand or "").lower()).strip()
        col = _BUILDER_COLUMN_MAP.get(norm)
        if col and col != "status":
            val = getattr(asset, col, None)
            return "" if val is None else val
    return None


def _norm_label(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _status_lookup():
    from .models import Asset  # local import: avoids app-loading order issue
    lookup = {}
    for value, label in Asset.STATUS_CHOICES:
        lookup[_norm_label(value)] = value
        lookup[_norm_label(label)] = value
        for part in label.split("/"):
            if _norm_label(part):
                lookup.setdefault(_norm_label(part), value)
    return lookup


def builder_status_choices(category):
    """Status dropdown options defined in the Category Builder.

    Returns ([(code, label), ...], []). The Status column now supports custom 
    statuses, so any option text is accepted directly as a status code. For 
    compatibility, if an option text matches an old built-in status (e.g. 
    "Not Working"), it will map to the legacy code (e.g. "not_working").
    ([], []) when the category has no builder Status dropdown."""
    layout = get_builder_layout(category)
    if layout is None:
        return [], []
    meta = layout.get("core_meta", {}).get("status")
    if not meta or meta["type"] not in ("select", "searchable_select") or not meta["options"]:
        return [], []
    lookup = _status_lookup()
    choices, unmatched, seen = [], [], set()
    for opt in meta["options"]:
        # If it matches an old legacy code, use it to preserve compatibility,
        # otherwise just use the option label directly as the custom status!
        code = lookup.get(_norm_label(opt))
        if code is None:
            code = opt  # Custom status
        
        if code not in seen:
            seen.add(code)
            choices.append((code, opt))
    return choices, unmatched


def builder_core_options(category):
    """{core_column: [option labels]} for Category-Builder dropdown fields that
    are bound to a real Asset column (Brand, Model ...). Status is handled by
    builder_status_choices()."""
    layout = get_builder_layout(category)
    if layout is None:
        return {}
    out = {}
    for col, meta in layout.get("core_meta", {}).items():
        if col in ("status", "asset_tag", "name"):
            continue
        if meta["type"] in ("select", "searchable_select") and meta["options"]:
            out[col] = {"label": meta["label"], "options": list(meta["options"])}
    return out
