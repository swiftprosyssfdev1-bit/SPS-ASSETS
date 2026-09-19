"""
Sheet Template Registry
========================

Source of truth: "system details updated 4.xlsx".

Every sheet in that workbook has its OWN field names, its OWN column order,
and (on several sheets) its own blank-header columns and duplicate headers.
This module is the single place that records those exact, original
structures — nothing here is a generic/common asset schema, and nothing
here invents a field name that isn't in the workbook.

    ONE SHEET  =  ONE TEMPLATE  =  ITS OWN FIELD NAMES

How a template is used elsewhere in the app:
  - assets.import_utils validates an uploaded sheet's header row against the
    matching template (missing / unexpected columns), and stores every
    column's value under the template's exact original header text.
  - assets.category_fields exposes a template's columns, in order, as the
    fields shown on that category's Add/Edit form and detail table.
  - assets.views (export_assets) writes each category's sheet back out
    using the template's exact headers, in the template's exact order,
    including blank-header columns in their original position.

Column definition dict:
    key         - internal-only, JSON-safe dict key used to store this
                  column's value in Asset.extra_details. For a normal
                  column this is just the header text. For a blank header
                  or a header that's repeated on the same sheet, a suffix
                  is added ONLY to make the key unique — this is never
                  shown to a user in place of the real header (see `header`).
    header      - the exact original header text, preserved letter-for-
                  letter (spelling/capitalisation/spacing/punctuation), or
                  None if the source cell genuinely has no header text.
    col_index   - 0-based position in the original sheet, used to keep
                  blank/duplicate columns in their correct original slot
                  on export.
"""


def _cols(*headers):
    """Build column defs from a flat header list (as read straight from row
    1 of the sheet), disambiguating duplicate/blank headers with an
    internal-only suffix on `key` — `header` always keeps the literal
    original text (or None), untouched."""
    seen = {}
    out = []
    for idx, h in enumerate(headers):
        h = h.strip() if isinstance(h, str) and h.strip() else None
        base = h if h is not None else "_blank"
        seen[base] = seen.get(base, 0) + 1
        key = base if seen[base] == 1 else f"{base}__{seen[base]}"
        out.append({"key": key, "header": h, "col_index": idx})
    return out


# normalized sheet tab name -> ordered column defs, exactly as they appear
# in row 1 of that tab in "system details updated 4.xlsx".
SHEET_TEMPLATES = {

    "cpu - system unit": _cols(
        "System No", "System Name", "Password", "OS Type", "Processor",
        "System Type", "Motherboard Type", "HardDisk Name",
        "HardDisk Serial No.", "Graphics Card Serial No.", "HardDisk Size",
        "RAM", "RAM Model", "RAM Size", "DVD", "CD", "Extra", "Remark",
        "Last Service Date", "Antivirus", "Condition", "Notes",
        None, "others", None,
    ),

    "keyboard": _cols(
        "Keyboard Id", "Brand", "Status", "S.No", None, "Location", None,
    ),

    "monitor": _cols(
        "Monitor No", "Brand", "Type", "Color", "Size", "Model",
        "Serial No", "Condition", None, None,
    ),

    "mouse": _cols(
        "Mouse", "Brand", "Status", None, "S.No", "Location",
    ),

    "ups": _cols(
        "UPS No", "Brand", "Color", "Model No", "Size", "Last Service Date",
        "Condition", "Details", "Current Status", None, None, None,
    ),

    "bluetooth": _cols(
        "Bluetooth No", "Devices", "Brand", "Remark", "User Name",
    ),

    "others": _cols(
        "ID", "Device Type", "Device Name", "Status", "Details", None,
    ),

    "software and os": _cols(
        "Type", "Version", "Product Key", "System No", "Details",
    ),

    "hard disk": _cols(
        "Hard disk  Number", "Hard Disk Name", "Size", "Serial No",
        "Conditions", "Purpose", "Details", "Current Status",
    ),

    "workstation": _cols(
        "Workstation ID", "Employee ID", "Employee Name", "CPU Number",
        "Monitor Number", "Keyboard Number", "Mouse Number", "UPS No.",
        "Product Id", "Cd Key", "Operating System", "Purposes", "User Id",
        None,
    ),

    "employee_list": _cols(
        "EmployeeName", "Employee Id", "Status",
        "Employee Name", "Id", "Status",
    ),

    "project details": _cols(
        "S.No", "Project Name", "Status",
    ),

    "project backup": _cols(
        "Hard disk Name", "Projects", "Project Manager", "Date",
        "Space Free", "Backup Available HDD", None, None,
    ),

    "inside cupboard": _cols(
        "Internal Hard disk", "Hard Disk Size", "Hard Disk S.No",
        "Conditions", None, "Updated", "Internal HDD", "Size", "S.No",
        "Conditions",
    ),

    "it vendor": _cols(
        "S.No", "Vendor Name", "Contact Person", "Mobile No",
        "Types of Service", "Details 1", "Details 2", None,
    ),

    "incident register": _cols(
        "S.No", "Incident Type", "Incident Description",
        "Start Date&Time", "End Date&Time",
    ),
}

# Sheet tab names in the workbook that aren't spelled exactly like the
# template keys above (case/spacing variants seen in real uploads).
SHEET_NAME_TEMPLATE_ALIASES = {
    "workstation_list": "workstation",
    "inside the cupboard": "inside cupboard",
    "it_vendor list": "it vendor",
    "bluetooth device": "bluetooth",
    "employee": "employee_list",
}

# AssetCategory.name (normalized) -> template key. This is how the export
# code and category_fields.py know which template belongs to which category,
# since a few categories are named slightly differently from their sheet's
# tab (e.g. the "CPU - System Unit" tab -> "CPU / System Unit" category).
CATEGORY_TO_TEMPLATE = {
    "employee": "employee_list",
    "workstation": "workstation",
    "cpu / system unit": "cpu - system unit",
    "monitor": "monitor",
    "keyboard": "keyboard",
    "mouse": "mouse",
    "ups": "ups",
    "bluetooth device": "bluetooth",
    "hard disk": "hard disk",
    "software / os license": "software and os",
    "project details": "project details",
    "project backup": "project backup",
    "inside cupboard": "inside cupboard",
    "it vendor": "it vendor",
    "incident register": "incident register",
    # Air Conditioner, Biometric Device, Networking Equipment, Other Asset
    # intentionally have NO template: those categories are sub-slices of
    # the catch-all "others" tab, routed row-by-row by device type, not a
    # single 1:1 sheet — see DEVICE_TYPE_CATEGORY_ALIASES in import_utils.
}


def normalize_name(name):
    return str(name or "").strip().lower()


def get_sheet_template(sheet_name):
    """Returns the ordered column-def list for this sheet tab name, trying
    the sheet's own name first and then its known aliases. Returns None if
    this sheet has no registered template (falls back to reading whatever
    header row is actually in the uploaded file)."""
    norm = normalize_name(sheet_name)
    if norm in SHEET_TEMPLATES:
        return SHEET_TEMPLATES[norm]
    alias = SHEET_NAME_TEMPLATE_ALIASES.get(norm)
    if alias:
        return SHEET_TEMPLATES.get(alias)
    return None


def get_template_for_category(category_name):
    """Returns the ordered column-def list for the sheet this AssetCategory
    was originally imported from, or None if that category has no 1:1
    sheet (e.g. Other Asset)."""
    key = CATEGORY_TO_TEMPLATE.get(normalize_name(category_name))
    if not key:
        return None
    return SHEET_TEMPLATES.get(key)


def template_headers(sheet_name):
    """Convenience: just the ordered header texts (None for blank columns)."""
    tmpl = get_sheet_template(sheet_name)
    if not tmpl:
        return None
    return [c["header"] for c in tmpl]


def diff_headers(sheet_name, uploaded_header_row):
    """Compares an uploaded header row against this sheet's template.
    Returns (missing, unexpected):
      missing:    template headers (non-blank) not found anywhere in the
                  uploaded row.
      unexpected: uploaded headers (non-blank) not found in the template.
    Comparison is case/space-insensitive on the text, but the template's
    own spelling is always what's shown/used elsewhere (never renamed to
    match whatever casing the upload happened to use).
    Returns (None, None) if this sheet has no registered template.
    """
    tmpl = get_sheet_template(sheet_name)
    if not tmpl:
        return None, None

    def norm(s):
        import re
        return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()

    template_norms = {norm(c["header"]) for c in tmpl if c["header"]}
    uploaded_norms = {norm(h) for h in uploaded_header_row if norm(h)}

    missing = [c["header"] for c in tmpl if c["header"] and norm(c["header"]) not in uploaded_norms]
    unexpected = [h for h in uploaded_header_row if norm(h) and norm(h) not in template_norms]
    return missing, unexpected
