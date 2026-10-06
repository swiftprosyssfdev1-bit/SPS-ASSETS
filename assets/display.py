"""
Builder-driven display helpers for the category List page and the asset
Detail page.

Single source of truth: the category's ACTIVE AssetField rows (Category
Builder). For a builder-configured category these helpers decide, from the
builder alone:

  * which fields appear      (show_in_list / show_in_detail)
  * their order              (display_order)
  * their labels / types     (label, field_type, width)
  * where each value lives   (a real Asset column, or Asset.extra_details)

Categories that have NO AssetField rows are not handled here (build_* return
None) so the existing legacy rendering keeps working for them unchanged.

Nothing in this module writes to the database.
"""
import datetime

from django.utils import formats

from .category_fields import (
    _norm_label,
    _status_lookup,
    builder_field_column,
    builder_status_choices,
    get_builder_layout,
    get_category_field_labels,
    get_category_fields,
    resolve_extra_value,
    _norm_key,
)

LIST_TEXT_LIMIT = 140   # longer text is cut in the table; full text goes in the tooltip

# Canonical labels that decide which field is the PRIMARY one when a builder
# category has several fields that map onto the same real column (e.g. Hard
# Disk has both "Status" and "Current Status" - only "Status" is the real
# Status column; "Current Status" is an ordinary extra_details field).
_CANONICAL_LABELS = {
    "asset_tag": {"tag", "asset tag", "asset id", "asset tag id"},
    "name": {"name"},
    "status": {"status"},
    "brand": {"brand"},
    "model_number": {"model", "model no", "model number"},
    "serial_number": {"serial", "serial no", "serial number"},
    "current_location": {"location", "current location"},
    "current_assigned_to": {"assigned to", "current assigned to"},
    "notes": {"notes"},
}



# A status wording that means "no real status" (old Employee import).
_BLANK_STATUS_WORDS = {"not added"}


# ---------------------------------------------------------------------------
# Value formatting
# ---------------------------------------------------------------------------
def format_value(value, field=None, iso_dates=False):
    """Any stored value -> a safe display string ('' when empty).

    Handles None, 0 / False (which are real values, not 'missing'), dates,
    lists, dicts and select values stored with different case/spacing than
    the option label.

    iso_dates: write dates as YYYY-MM-DD (the Excel export uses this so an
    exported file can be re-imported; the pages use the site's date format)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (datetime.datetime, datetime.date)):
        if iso_dates:
            if isinstance(value, datetime.datetime):
                value = value.date()
            return value.isoformat()
        return formats.date_format(value, "DATE_FORMAT")
    if isinstance(value, (list, tuple, set)):
        parts = (format_value(v, None, iso_dates) for v in value)
        return ", ".join(p for p in parts if p)
    if isinstance(value, dict):
        parts = ((k, format_value(v, None, iso_dates)) for k, v in value.items())
        return ", ".join(f"{k}: {v}" for k, v in parts if v)
    text = str(value).strip()
    if text and field and field.get("type") in ("select", "searchable_select"):
        wanted = _norm_label(text)
        for opt in field.get("options") or []:
            if _norm_label(opt) == wanted:
                return str(opt)
    return text


def _shorten(text):
    if len(text) <= LIST_TEXT_LIMIT:
        return text, ""
    return text[: LIST_TEXT_LIMIT - 1].rstrip() + "…", text


# ---------------------------------------------------------------------------
# Which builder field is bound to which real Asset column
# ---------------------------------------------------------------------------
# CATEGORY_LABELS keys -> the real Asset column that label names. Used for
# fields the shared name map doesn't know (e.g. Employee's "Employee Id",
# Hard Disk's "Hard disk Number") - same rule resolve_field_value() applies.
_LABEL_CONFIG_COLUMNS = (
    ("tag_label", "asset_tag"), ("name_label", "name"), ("model_label", "model_number"),
    ("serial_label", "serial_number"), ("location_label", "current_location"),
    ("assigned_label", "current_assigned_to"),
)


def _label_config_column(category, field):
    cfg = get_category_field_labels(category)
    wanted = _norm_key(field.get("label"))
    if not wanted:
        return None
    for key, col in _LABEL_CONFIG_COLUMNS:
        if cfg.get(key) and _norm_key(cfg[key]) == wanted:
            return col
    return None


# Other Asset is the catch-all category (old "Others" and "Inside Cupboard"
# sheets). There the ID column IS the Asset Tag and the Device Name column IS
# the Name - the same rule the List page has always used - so those two
# builder fields are bound to the real columns instead of being drawn next to
# a second, duplicate Tag / Name.
_OTHER_ASSET_LABEL_COLUMNS = {"id": "asset_tag", "device name": "name"}


def _column_of(category, field):
    col = builder_field_column(field) or _label_config_column(category, field)
    if col is None and (getattr(category, "name", "") or "").strip().lower() == "other asset":
        col = _OTHER_ASSET_LABEL_COLUMNS.get(_norm_label(field.get("label")))
    return col


def _primary_bindings(category, fields):
    """(primary, duplicates):
    primary    {field key: real Asset column} - the PRIMARY field of each column
    duplicates {field key: real Asset column} - further fields mapping onto an
               already-taken column. The Add/Edit form stores those in the same
               column (it has no separate storage for them), so display falls
               back to that column when extra_details has nothing."""
    by_col = {}
    for f in fields:
        col = _column_of(category, f)
        if col:
            by_col.setdefault(col, []).append(f)
    primary, duplicates = {}, {}
    for col, group in by_col.items():
        canon = _CANONICAL_LABELS.get(col, set())
        pick = next((f for f in group if _norm_label(f.get("label")) in canon), group[0])
        primary[pick["name"]] = col
        for f in group:
            if f is not pick:
                duplicates[f["name"]] = col
    return primary, duplicates


def _is_workstation(category):
    return (getattr(category, "name", "") or "").strip().lower() == "workstation"


def _builder_fields(category):
    """(fields, primary bindings, duplicate bindings) for a builder-configured, non-Workstation category;
    None otherwise (caller falls back to the legacy rendering)."""
    if category is None or _is_workstation(category):
        return None
    if get_builder_layout(category) is None:
        return None
    fields = get_category_fields(category)
    primary, duplicates = _primary_bindings(category, fields)
    return fields, primary, duplicates


def _raw_value(asset, field, col):
    """Stored value for a builder field: extra_details first (tolerating old
    key spellings), legacy keys next, then the real column the field is bound
    to (and, last, the column the category's label config names)."""
    value = resolve_extra_value(getattr(asset, "extra_details", None), field)
    if format_value(value):
        return value
    cat = (getattr(asset.category, "name", "") or "").strip().lower()
    # Other Asset: the list page already treats the Asset Tag as "ID" and the
    # Name as "Device Name" (and Inside Cupboard rows keep their type under
    # "Item Type"), so the Detail page reads the same real columns when the
    # builder fields have no stored value of their own.
    if cat == "other asset":
        lbl = _norm_label(field.get("label"))
        if lbl == "id" and asset.display_tag:
            return asset.display_tag
        if lbl == "device name" and asset.display_name:
            return asset.display_name
        if lbl == "device type":
            for k, v in (asset.extra_details or {}).items():
                if _norm_label(k) == "item type" and format_value(v):
                    return v
    # Legacy key fallbacks: rows imported before these categories had builder
    # fields may have values under old column names. The current builder field
    # key (e.g. 'current_status') won't match, so try the historic key too.
    _LEGACY_KEY_FALLBACKS = {
        # Hard Disk: the old import stored the current status in 'Details'
        ("hard disk", "current status"): ["Details"],
        # Air Conditioner: old Others-sheet import used 'Status' for capacity
        # and 'Details' for last serviced date
        ("air conditioner", "capacity location"): ["Status"],
        ("air conditioner", "serviced"): ["Details"],
    }
    lbl_norm = _norm_label(field.get("label") or "")
    for fallback_key in _LEGACY_KEY_FALLBACKS.get((cat, lbl_norm), []):
        legacy = (asset.extra_details or {}).get(fallback_key)
        if format_value(legacy):
            return legacy
    if col is None:
        return ""
    cols = [col]
    cfg_col = _label_config_column(asset.category, field)
    if cfg_col and cfg_col not in cols:
        cols.append(cfg_col)
    for c in cols:
        if c == "asset_tag":
            return asset.display_tag
        v = getattr(asset, c, "")
        if format_value(v):
            return v
    return ""


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def status_cell(asset, field=None, choices=None):
    """{'code': badge css code, 'label': text} for the asset's status.

    Label comes from the category's own Status dropdown when it has an option
    for the stored code; a custom wording kept in extra_details (e.g.
    Employee 'Resigned') is shown when it isn't one of the built-in statuses
    or agrees with the stored code. A stale wording that contradicts the
    stored status is ignored."""
    code = asset.status or ""
    label = asset.get_status_display()
    cat = (getattr(asset.category, "name", "") or "").strip().lower()
    if cat == "air conditioner" and code == "other":
        # Legacy AC import: rows imported from the old Others sheet before AC
        # got its own Status column land with status='other'. Display 'Working'
        # so the badge isn't misleading. Once the row is edited through the
        # Category Builder form it will get a real status code instead.
        code, label = "working", "Working"
    if choices and code in choices:
        label = choices[code]
    if field is not None:
        wording = format_value(resolve_extra_value(asset.extra_details, field))
        if wording:
            mapped = _status_lookup().get(_norm_label(wording))
            if mapped is None or (mapped == code and not (choices and code in choices)):
                label = wording
    if _norm_label(label) in _BLANK_STATUS_WORDS:
        label = ""
    badge = "resigned" if _norm_label(label) == "resigned" else code
    return {"code": badge, "label": label}



def _field_text(asset, field, col, dup_col, status_choices, iso_dates=False):
    """Display text of an ordinary builder field."""
    text = format_value(_raw_value(asset, field, col), field, iso_dates)
    if not text and dup_col:
        if dup_col == "status":
            text = status_cell(asset, None, status_choices)["label"]
        elif dup_col != "asset_tag":
            text = format_value(getattr(asset, dup_col, ""), field, iso_dates)
    return text


# ---------------------------------------------------------------------------
# List page
# ---------------------------------------------------------------------------
def _is_password_field(f):
    """True for a field whose label/key is a password (List page hides these)."""
    text = f"{f.get('label') or ''} {f.get('name') or ''}".lower()
    return "password" in text or "passwd" in text


def _builder_columns(category, default_tag_label, list_only):
    """Column descriptors straight from the category's active builder fields,
    in builder order. list_only drops fields whose List visibility is off
    (the List page); the Excel export keeps every active field."""
    built = _builder_fields(category)
    if built is None:
        return None
    fields, bindings, dups = built
    status_choices = dict(builder_status_choices(category)[0])
    columns, tag_bound = [], False
    for f in fields:
        col = bindings.get(f["name"])
        visible = f.get("show_in_list", True) is not False
        kind = {"asset_tag": "tag", "name": "name", "status": "status"}.get(col, "field")
        if kind == "tag":
            tag_bound = True
        if list_only and (not visible or _is_password_field(f)):
            # passwords never appear on the List page (they stay in the detail view)
            continue
        columns.append({
            "kind": kind, "label": f.get("label") or f.get("name"), "field": f,
            "column": col, "dup_column": dups.get(f["name"]),
            "type": f.get("type") or "text", "key": f["name"],
            "status_choices": status_choices,
        })
    if not tag_bound:
        # the Tag is the record's identity and the builder has no field for it
        columns.insert(0, {"kind": "tag", "label": default_tag_label, "field": None,
                           "column": "asset_tag", "dup_column": None, "type": "text",
                           "key": "__tag__", "status_choices": {}})
    return columns


def build_list_columns(category, default_tag_label="Tag"):
    """Ordered column descriptors for the List page, or None when the
    category isn't builder-configured (legacy rendering applies)."""
    return _builder_columns(category, default_tag_label, list_only=True)


def build_list_cells(asset, columns, link_key=None, resolver=None):
    """One display cell per column for `asset`.

    link_key: field key whose value should link to the asset it names (the
    info-register 'summary' column); resolver: callable(value) -> Asset|None."""
    cells = []
    for c in columns:
        kind = c["kind"]
        if kind == "tag":
            cells.append({"kind": "tag", "text": asset.display_tag})
        elif kind == "name":
            cells.append({"kind": "name", "text": asset.display_name or ""})
        elif kind == "status":
            s = status_cell(asset, c["field"], c["status_choices"])
            cells.append({"kind": "status", "text": s["label"], "badge": s["code"]})
        else:
            text = _field_text(asset, c["field"], c["column"], c["dup_column"], c["status_choices"])
            short, full = _shorten(text)
            cell = {"kind": "field", "text": short, "title": full}
            if link_key and c["key"] == link_key and text and resolver:
                cell["link"] = resolver(text)
            cells.append(cell)
    return cells


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------
def build_export_columns(category, default_tag_label="Asset Tag"):
    """Ordered column descriptors for the Excel export, or None when the
    category isn't builder-configured (the legacy export applies).

    Same fields, order, labels and value rules as the List page, so a sheet
    always agrees with what the user sees on screen. One difference: the
    List page's "show in list" switch only trims the on-screen table, so the
    export keeps every active field (nothing is lost when the file is
    edited and imported back). The Tag column is always present, because
    import needs it to identify the record."""
    return _builder_columns(category, default_tag_label, list_only=False)


def build_export_cells(asset, columns):
    """One plain value per export column for `asset`: no shortening, dates as
    YYYY-MM-DD, the same Status wording the List page shows."""
    cells = []
    for c in columns:
        kind = c["kind"]
        if kind == "tag":
            cells.append(asset.display_tag or "")
        elif kind == "name":
            cells.append(asset.display_name or "")
        elif kind == "status":
            cells.append(status_cell(asset, c["field"], c["status_choices"])["label"])
        else:
            cells.append(_field_text(
                asset, c["field"], c["column"], c["dup_column"],
                c["status_choices"], iso_dates=True,
            ))
    return cells


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------
def build_detail_fields(asset):
    """Ordered detail entries for the asset's category, or None when the
    category isn't builder-configured (legacy rendering applies).

    Includes Tag / Name / Status fields too, at their builder position with
    their builder label, when their Detail visibility is on."""
    built = _builder_fields(asset.category)
    if built is None:
        return None
    fields, bindings, dups = built
    choices = dict(builder_status_choices(asset.category)[0])
    out = []
    for f in fields:
        if f.get("show_in_detail", True) is False:
            continue
        col = bindings.get(f["name"])
        entry = {
            "label": f.get("label") or f.get("name"),
            "width": 12 if f.get("width") == 12 or f.get("type") == "textarea" else 6,
            "type": f.get("type") or "text",
            "kind": "field", "badge": "",
        }
        if col == "asset_tag":
            entry.update(kind="tag", value=asset.display_tag)
        elif col == "name":
            entry.update(kind="name", value=asset.display_name or "")
        elif col == "status":
            s = status_cell(asset, f, choices)
            entry.update(kind="status", value=s["label"], badge=s["code"])
        else:
            entry["value"] = _field_text(asset, f, col, dups.get(f["name"]), choices)
        out.append(entry)
    return out


def detail_heading_shows_name(asset):
    """Heading reads 'TAG — Name' only when the builder has a Name field that
    is visible on the Detail page."""
    built = _builder_fields(asset.category)
    if built is None:
        return True
    fields, bindings, _dups = built
    return any(
        bindings.get(f["name"]) == "name" and f.get("show_in_detail", True) is not False
        for f in fields
    )
