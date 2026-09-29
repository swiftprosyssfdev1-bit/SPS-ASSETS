"""
Bulk import helpers: parse an uploaded .csv / .txt / .xlsx file into rows,
match its header row against Asset fields, and validate every row before
anything touches the database.
"""
import csv
import io
import re
from datetime import datetime, date

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from .models import Asset, AssetCategory
from .category_fields import SENSITIVE_FIELD_NAMES, _is_sensitive_field, is_category_db_configured, get_category_fields
from .sheet_templates import get_sheet_template, diff_headers as _diff_template_headers

MAX_IMPORT_ROWS = 20000

# (Column header shown to the admin, model field name, required?, kind)
IMPORT_COLUMNS = [
    ("Category", "category", True, "category"),
    ("Asset Tag", "asset_tag", True, "text"),
    ("Name", "name", True, "text"),
    ("Brand", "brand", False, "text"),
    ("Model Number", "model_number", False, "text"),
    ("Serial Number", "serial_number", False, "text"),
    ("Status", "status", False, "status"),
    ("Current Assigned To", "current_assigned_to", False, "text"),
    ("Current Location", "current_location", False, "text"),
    ("Linked Workstation", "linked_workstation", False, "text"),
    ("Purchase Date", "purchase_date", False, "date"),
    ("Last Service Date", "last_service_date", False, "date"),
    ("Notes", "notes", False, "text"),
    ("Is Active", "is_active", False, "bool"),
]

# Extra header spellings admins might type/paste in, mapped to the same field.
# Grown out of the legacy per-category sheets (CPU - System Unit, keyboard,
# Monitor, Mouse, UPS, Bluetooth, others, Software and OS, Hard disk,
# Inside Cupboard, Workstation) so each of those sheets can be uploaded as
# its own single-sheet CSV without renaming anything in Excel first.
HEADER_ALIASES = {
    "asset id": "asset_tag",
    "tag": "asset_tag",
    "id": "asset_tag",
    "system no": "asset_tag",           # CPU - System Unit, Software and OS
    "asset name": "name",
    "system name": "name",
    "vendor name": "name",              # IT Vendor
    "devices": "name",                  # others / Bluetooth
    "device name": "name",              # others
    "serial no": "serial_number",
    "serial no ": "serial_number",
    "s no": "serial_number",            # keyboard/Mouse "S.No"
    "hard disk s no": "serial_number",  # Inside Cupboard
    "product key": "serial_number",     # Software and OS
    "model": "model_number",
    "model no": "model_number",
    "condition": "status",              # CPU, Monitor, UPS, Hard disk
    "conditions": "status",             # Hard disk, Inside Cupboard (plural)
    "assigned to": "current_assigned_to",
    "current user": "current_assigned_to",
    "user name": "current_assigned_to",  # Bluetooth
    "location": "current_location",
    "workstation": "linked_workstation",
    "active": "is_active",
}

# Header->field mappings that are ONLY correct on ONE specific sheet, because
# the same header text means something different (or is just a foreign-key
# reference to a DIFFERENT asset) on other sheets. These must NOT go in the
# global HEADER_ALIASES above:
#   - "Workstation ID"/"UPS No."/"Keyboard Id" etc. are each that sheet's OWN
#     tag column — but Workstation ALSO has its own "UPS No." column that's
#     just a reference to an existing UPS asset, not this row's tag. A global
#     alias for "ups no" would wrongly claim BOTH columns for asset_tag on
#     the Workstation sheet at once (ambiguous-header error).
#   - "Employee Name" means the row's own Name on the Employee_List sheet,
#     but means "who's using this Workstation" (current_assigned_to) on the
#     Workstation sheet. A single global alias can't be correct for both.
# Keyed by normalized SHEET NAME (not the block-split label) -> the same
# {normalized_header: field} shape as HEADER_ALIASES, checked with priority
# over the global table by map_headers() when a sheet_name is given.
SHEET_HEADER_OVERRIDES = {
    "air conditioner": {
        "serviced": "last_service_date",
    },
    "workstation": {
        "workstation id": "asset_tag",
        "employee name": "current_assigned_to",
    },
    "keyboard": {"keyboard id": "asset_tag"},
    "monitor": {"monitor no": "asset_tag"},
    "mouse": {"mouse": "asset_tag"},
    "ups": {"ups no": "asset_tag"},
    "bluetooth": {"bluetooth no": "asset_tag"},
    "hard disk": {
        "hard disk number": "asset_tag",
        "hard disk name": "name",
        "conditions": None,  # avoid claiming 'status' since 'Status' column is present
    },
    "employee list": {
        "employee id": "asset_tag",
        "employeename": "name",   # block 1: "EmployeeName" (no space)
        "employee name": "name",  # block 2: "Employee Name"
    },
    "employee": {
        "employee id": "asset_tag",
        "employeename": "name",
        "employee name": "name",
    },
    "project details": {
        "project name": "asset_tag",
    },
    "project backup": {
        "projects": "name",
    },
    "inside cupboard": {
        "conditions": None,  # prevent alias to status; "Status" column handles status
    },
    "inside the cupboard": {
        "conditions": None,
    },
    "biometric device": {
        "id": None,           # prevent collision with 'Asset Tag'
        "device name": None,  # prevent collision with 'Name'
    },
    "other asset": {
        "id": None,           # prevent collision with 'Asset Tag'
        "device name": None,  # prevent collision with 'Name'
        "location": None,     # prevent collision with 'Current Location'
    },
    "bluetooth device": {
        "bluetooth no": "asset_tag",
    },
    "it vendor": {
        "vendor name": "name",
    },
}

# Sheets whose own headers already contain a literal duplicate ("Status"
# appears twice in the legacy keyboard and Mouse sheets, "Current Status"
# duplicating "Status" in UPS/Hard disk). Aliasing can't fix a genuinely
# duplicated header — map_headers correctly refuses to guess which column
# is real — so those sheets still need ONE of the duplicate columns
# renamed by hand before upload (e.g. the second "Status" -> "Notes").

DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d %b %Y",
                "%d.%m.%Y", "%d.%m.%y", "%d/%m/%y", "%d-%m-%y"]

TRUE_WORDS = {"true", "1", "yes", "y", "active"}
FALSE_WORDS = {"false", "0", "no", "n", "inactive", "retired"}


def _normalize_header(text):
    import re
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


LABEL_TO_FIELD = {_normalize_header(label): field for label, field, _req, _kind in IMPORT_COLUMNS}
COLUMN_KIND = {field: kind for _label, field, _req, kind in IMPORT_COLUMNS}
COLUMN_REQUIRED = {field for _label, field, req, _kind in IMPORT_COLUMNS if req}


def _build_status_lookup():
    lookup = {}
    for value, label in Asset.STATUS_CHOICES:
        lookup[value.lower()] = value
        lookup[_normalize_header(label)] = value
        for part in label.split("/"):
            part = _normalize_header(part)
            if part:
                lookup.setdefault(part, value)
    return lookup


STATUS_LOOKUP = _build_status_lookup()
VALID_STATUS_LABELS = ", ".join(label for _v, label in Asset.STATUS_CHOICES)


class ImportFileError(ValueError):
    """Raised when the uploaded file itself can't be read/understood."""


def read_upload_bytes(uploaded_file):
    """Reads an upload's bytes, decrypting it first if it's a password-
    protected Excel file (an exported register — see excel_security)."""
    from .excel_security import decrypt_if_encrypted, ExcelPasswordError

    raw = uploaded_file.read()
    try:
        return decrypt_if_encrypted(raw)
    except ExcelPasswordError as exc:
        raise ImportFileError(str(exc))


XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE2 compound doc — old .xls format


def _is_xls_bytes(raw_bytes):
    """Returns True if the raw file bytes look like an old-style Excel .xls file."""
    return raw_bytes[:8] == XLS_MAGIC


def _find_sheet_name(sheet_names, wanted):
    """Case/whitespace-insensitive sheet-name lookup. Returns the real name or None."""
    wanted = str(wanted or "").strip().lower()
    for name in sheet_names:
        if str(name).strip().lower() == wanted:
            return name
    return None


def _read_xls_bytes(raw_bytes, sheet_index=0, sheet_name=None):
    """Reads an old .xls file from raw bytes using xlrd.
    Returns (header_row, data_rows) from the specified sheet index, or from
    the sheet called `sheet_name` when given (falls back to `sheet_index`
    only if the workbook has a single sheet).
    """
    import xlrd
    wb = xlrd.open_workbook(file_contents=raw_bytes)
    if sheet_name:
        match = _find_sheet_name(wb.sheet_names(), sheet_name)
        if match:
            ws = wb.sheet_by_name(match)
        elif wb.nsheets > 1:
            raise ImportFileError(
                f"No sheet named '{sheet_name}' in this file. "
                f"Sheets found: {', '.join(wb.sheet_names())}."
            )
        else:
            ws = wb.sheet_by_index(sheet_index)
    else:
        ws = wb.sheet_by_index(sheet_index)
    rows = []
    for row_idx in range(ws.nrows):
        row = []
        for cell in ws.row(row_idx):
            if cell.ctype == xlrd.XL_CELL_DATE:
                import datetime
                val = xlrd.xldate_as_datetime(cell.value, wb.datemode).date()
            elif cell.ctype == xlrd.XL_CELL_EMPTY:
                val = None
            else:
                val = cell.value
                # xlrd returns floats for integers (e.g. 1.0 instead of 1)
                if isinstance(val, float) and val == int(val):
                    val = int(val)
            row.append(val)
        if any(v is not None and str(v).strip() for v in row):
            rows.append(row)
    return rows


def _read_xls_all_sheets(raw_bytes):
    """Reads all sheets from an old .xls file. Returns a dict of
    {sheet_name: [(header_row, data_rows)]} similar to openpyxl workbook.
    """
    import xlrd
    wb = xlrd.open_workbook(file_contents=raw_bytes)
    result = {}
    for sheet_name in wb.sheet_names():
        ws = wb.sheet_by_name(sheet_name)
        rows = []
        for row_idx in range(ws.nrows):
            row = []
            for cell in ws.row(row_idx):
                if cell.ctype == xlrd.XL_CELL_DATE:
                    import datetime
                    val = xlrd.xldate_as_datetime(cell.value, wb.datemode).date()
                elif cell.ctype == xlrd.XL_CELL_EMPTY:
                    val = None
                else:
                    val = cell.value
                    if isinstance(val, float) and val == int(val):
                        val = int(val)
                row.append(val)
            if any(v is not None and str(v).strip() for v in row):
                rows.append(row)
        result[sheet_name] = rows
    return result


def parse_uploaded_file(uploaded_file, preferred_sheet=None):
    """Returns (header_row: list[str], data_rows: list[list]) from a
    csv/txt/xlsx/xls file. Old .xls files are handled automatically even if
    they are misnamed as .csv or .xlsx.

    preferred_sheet: for multi-sheet Excel files, read the sheet with this
    name (case-insensitive) instead of whichever sheet happened to be active
    when the workbook was saved. If the workbook has several sheets and none
    matches, an ImportFileError lists the sheets found. Ignored for csv/txt
    and for single-sheet workbooks.
    """
    filename = (uploaded_file.name or "").lower()
    raw_bytes = read_upload_bytes(uploaded_file)

    # Detect old .xls by magic bytes regardless of the file extension —
    # many users export from Excel and the file is still .xls inside.
    if _is_xls_bytes(raw_bytes):
        try:
            rows = _read_xls_bytes(raw_bytes, sheet_index=0, sheet_name=preferred_sheet)
        except ImportFileError:
            raise
        except Exception as exc:
            raise ImportFileError(f"Couldn't open the Excel file: {exc}")
        if not rows:
            raise ImportFileError("The Excel file is empty.")
        header, data_rows = rows[0], rows[1:]

    elif filename.endswith(".xlsx"):
        try:
            wb = load_workbook(filename=io.BytesIO(raw_bytes), data_only=True, read_only=True)
        except Exception as exc:
            raise ImportFileError(f"Couldn't open the Excel file: {exc}")
        ws = wb.active
        if preferred_sheet:
            match = _find_sheet_name(wb.sheetnames, preferred_sheet)
            if match:
                ws = wb[match]
            elif len(wb.sheetnames) > 1:
                raise ImportFileError(
                    f"No sheet named '{preferred_sheet}' in this file. "
                    f"Sheets found: {', '.join(wb.sheetnames)}."
                )
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            raise ImportFileError("The Excel file is empty.")
        header, data_rows = rows[0], rows[1:]

    elif filename.endswith(".csv") or filename.endswith(".txt"):
        raw = raw_bytes.decode("utf-8-sig", errors="replace")
        sample = raw.splitlines()[0] if raw.splitlines() else ""
        delimiter = ","
        if sample.count("\t") > sample.count(","):
            delimiter = "\t"
        else:
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
            except Exception:
                pass
        # csv.reader requires universal newlines — replace \r\n and bare \r
        # with \n so embedded newlines in quoted fields don't confuse it.
        raw_normalised = raw.replace("\r\n", "\n").replace("\r", "\n")
        reader = csv.reader(io.StringIO(raw_normalised), delimiter=delimiter)
        rows = [r for r in reader if any(cell.strip() for cell in r)]
        if not rows:
            raise ImportFileError("The file is empty.")
        header, data_rows = rows[0], rows[1:]

    else:
        raise ImportFileError("Unsupported file type. Please upload a .csv, .txt, or .xlsx file.")

    if not data_rows:
        raise ImportFileError("No data rows found below the header row.")
    if len(data_rows) > MAX_IMPORT_ROWS:
        raise ImportFileError(
            f"This file has {len(data_rows)} rows — please split it into batches of {MAX_IMPORT_ROWS} or fewer."
        )

    return list(header), [list(r) for r in data_rows]



SHEET_NAME_ALIASES = {
    # sheet tab name (normalized) -> real AssetCategory name, for tabs that
    # are clearly a known category but don't spell it exactly the same way.
    "employee list": "Employee",
    "bluetooth": "Bluetooth Device",
    "software and os": "Software / OS License",
    "others": "Other Asset",
    "project details": "Project Details",
    "project backup": "Project Backup",
    "it_vendor list": "IT Vendor",
    "incident register": "Incident Register",
    # "Inside Cupboard" / "Inside the Cupboard" are NOT aliased to an
    # "Inside Cupboard" category — see FORCE_OTHER_ASSET_SHEETS below,
    # which always routes those tabs to "Other Asset" instead.
}

# Sheet tab names (normalized) that should ALWAYS be filed under the
# "Other Asset" catch-all category, even if an AssetCategory happens to
# exist with a matching name. Unlike the generic fallback further down
# (which only kicks in when nothing matches), this is a deliberate
# routing choice: these tabs don't represent their own real asset
# category, they're miscellaneous items that belong in Other Asset.
FORCE_OTHER_ASSET_SHEETS = {
    "inside cupboard",
    "inside the cupboard",
}

# sheet tab name (normalized) -> number of leading rows to drop BEFORE the
# real header row. Some legacy sheets put a one-off metadata line above
# their actual header (e.g. Incident Register's row 1 is "As On Date:
# 2015-06-03", with the real "S.No / Incident Type / ..." header on row 2).
# Without this, that metadata line gets read as the header itself — its two
# cells become the only recognized columns, every other column (Incident
# Description, Start/End Date&Time) has no header text at all on row 1 and
# is silently dropped, and the real header row ends up imported as a data
# row instead of being used to label the columns.
SHEET_LEADING_ROWS_TO_SKIP = {
    "incident register": 1,
}

FALLBACK_CATEGORY_NAME = "Other Asset"  # matches the name actually seeded by
# seed_categories.py / migration 0004 — this MUST stay in sync with that
# spelling (singular), not the plural used in some UI copy/comments.

# sheet tab name (normalized) -> header text (normalized) of the column that
# is the real unique-per-row identifier for THAT sheet. Every legacy sheet
# has its own field layout (Monitor's is 'Type/Color/Size...', UPS's is
# different again, etc.) so there is no single generic column position or
# name that works everywhere — this is checked BEFORE the generic
# first-column fallback in _resolve_tag_fallback_idx, and ONLY applies to
# the named sheet, so it never affects how other sheets pick their tag.

# For sheets that dump many unrelated device types into one tab under a
# free-text "Device Type"-style column (e.g. the "others" sheet has rows
# for A/C, Biometrics, Router, Printer, ... all under one tab). Normalized
# device-type text -> real AssetCategory name, so a row can be routed to
# its own matching category (Air Conditioner, Biometric Device, ...)
# instead of always landing in the 'Other Asset' catch-all just because
# its sheet tab is the catch-all's tab. Device types with no entry here
# (Router, Stabilizer, Fan, Printer, ...) still fall back to 'Other Asset'
# as before — there's no dedicated category for them yet.
DEVICE_TYPE_CATEGORY_ALIASES = {
    "a c": "Air Conditioner",
    "ac": "Air Conditioner",
    "air conditioner": "Air Conditioner",
    "biometrics": "Biometric Device",
    "biometric": "Biometric Device",
    "biometric device": "Biometric Device",
}

# Header spellings (normalized) that hold the free-text device type, checked
# only on sheets whose rows land in the 'Other Asset' fallback category.
DEVICE_TYPE_HEADER_NAMES = {"device type", "type", "asset type"}

SHEET_TAG_COLUMN_HINTS = {
    # Software and OS: Type/Version/Product Key repeat across rows (same
    # volume-license key issued to many machines) — System No is the one
    # column that's actually unique per row.
    "software and os": "system no",
}

# Some legacy sheets pack more than one physical item into a single row by
# repeating a whole block of columns side-by-side (e.g. "Inside Cupboard"
# has one hard disk's Internal Hard disk/Size/S.No/Conditions in columns
# A-D, then a SECOND, unrelated hard disk's same fields again in columns
# G-J). Each column-index group below is imported as its own Asset row
# instead of being crammed into one — rows where a block is entirely blank
# (there was no second item on that line) are simply skipped for that
# block, not treated as an error.
SHEET_BLOCK_SPLITS = {
    "employee list": [
        [0, 1, 2],  # EmployeeName / Employee Id / Status (only Block 1)
    ],
    "employee": [
        [0, 1, 2],  # EmployeeName / Employee Id / Status (only Block 1)
    ],
}

# Which of the SHEET_BLOCK_SPLITS sheets above have blocks that are packed,
# genuinely distinct items (a same tag across blocks is a rare coincidence —
# auto-suffix into two assets, e.g. Inside Cupboard) vs. blocks that are the
# SAME list repeated (a same tag across blocks is the same real-world thing
# — e.g. Employee_List's two blocks are one roster listed twice, just offset
# by a row, with the second block adding a Status column). For sheets in
# this set, a tag repeated across that sheet's own blocks is resolved with
# the same "later row wins" rule as an ordinary same-sheet duplicate,
# instead of being auto-suffixed into a second, spurious asset.
SHEET_BLOCK_SPLITS_SAME_ENTITY = {"employee list", "employee"}

# Sheets where a blank Name column combined with a non-ID-looking value in
# the ID column signals a data-entry mistake (the person's name got typed
# into the ID cell) rather than a real employee ID — see
# _looks_like_employee_id / _next_employee_placeholder_tag below.
EMPLOYEE_NAME_AS_ID_FALLBACK_SHEETS = {"employee list", "employee"}

# The only Employee Id formats actually confirmed in the source data:
# letters followed by digits, nothing else (e.g. "SPS002", "TR1533"). We only
# treat a value as "not an ID" when it fails THIS check — not merely because
# it contains a space — since we haven't confirmed spaces never appear in a
# real ID.
EMPLOYEE_ID_PATTERN = re.compile(r"^(?:[A-Za-z]+[\s\-_]*)?\d+[A-Za-z\d\-_]*$|^[A-Za-z]+[\-_][A-Za-z\d\-_]+$")


def _looks_like_employee_id(text):
    return bool(EMPLOYEE_ID_PATTERN.match(str(text or "").strip()))


# ---------------------------------------------------------------------------
# Duplicate detection for rows WITHOUT a real ID column
# ---------------------------------------------------------------------------
# Sheets like Inside Cupboard / IT Vendor / Incident Register have no unique
# ID column, so each row's tag is generated (first column / row number) and
# any collision is auto-suffixed (-2, -3 ...). That is what let the same file
# be imported again and again with every row showing "Valid". For those rows
# we compare the row's CONTENT against what's already in the register.
_FINGERPRINT_FIELDS = (
    "brand", "model_number", "serial_number",
    "current_assigned_to", "current_location", "linked_workstation",
)


def _fp_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _content_fingerprint(category_id, fields, extra_details):
    """Hashable identity of a row's content (tag/name/status/notes ignored:
    tag and name are auto-generated on these sheets and change per import).
    Returns None when the row has no content at all."""
    parts = [_fp_text(fields.get(f)) for f in _FINGERPRINT_FIELDS]
    # Blank-header columns ("_blank..." keys) have no field name and only exist
    # in some copies of a sheet (e.g. an unlabeled note column) — a row must not
    # look "new" just because one copy has that stray cell and the other doesn't.
    extras = sorted(
        _fp_text(v) for k, v in (extra_details or {}).items()
        if _fp_text(v) and not str(k).startswith("_blank")
    )
    if not any(parts) and not extras:
        return None
    return (category_id, tuple(parts), tuple(extras))


def _load_existing_fingerprints():
    fps = set()
    qs = Asset.objects.filter(is_active=True).values(
        "category_id", "extra_details", *_FINGERPRINT_FIELDS
    )
    for row in qs.iterator():
        fp = _content_fingerprint(row["category_id"], row, row["extra_details"])
        if fp is not None:
            fps.add(fp)
    return fps


def _next_employee_placeholder_tag(counter, existing_tags, seen_tags_in_file):
    """Generates a unique 'IMPORT-EMP-NNN' placeholder tag. `counter` is a
    shared 1-item list, mutated in place, so multiple sheet/block calls for
    the same workbook (e.g. Employee_List's two blocks) keep counting up
    instead of each restarting at 1. Skips any number already taken in the
    DB or earlier in this same import."""
    while True:
        num = counter[0]
        counter[0] += 1
        candidate = f"IMPORT-EMP-{num:03d}"
        if candidate.lower() not in existing_tags and candidate.lower() not in seen_tags_in_file:
            return candidate


def _split_sheet_into_blocks(sheet_name, header, data_rows, block_col_groups):
    """Returns a list of (sub_sheet_label, sub_header, sub_data_rows,
    original_col_indices) — one per configured column block — for a sheet
    whose rows each pack multiple items side-by-side. Rows that are
    entirely blank within a given block are dropped for that block (no
    second item on that line), not flagged as invalid.

    `original_col_indices` is the block's column list unchanged (cols) —
    it lets the caller look each sub-column back up in the FULL sheet
    template (keyed by original column position) even though the block's
    own header/data have been re-indexed to start at 0.
    """
    blocks = []
    for block_num, cols in enumerate(block_col_groups, start=1):
        sub_header = [header[c] if c < len(header) else None for c in cols]
        sub_rows = []
        for row in data_rows:
            sub_row = [row[c] if c < len(row) else None for c in cols]
            if any(_cell_text(v) for v in sub_row):
                sub_rows.append(sub_row)
        label = f"{sheet_name} (item {block_num})" if len(block_col_groups) > 1 else sheet_name
        blocks.append((label, sub_header, sub_rows, list(cols)))
    return blocks


def _parse_inside_cupboard_sheet_rows(rows, existing_tags=None, seen_tags=None):
    """Parses the complex multi-section 'Inside Cupboard' sheet into clean,
    professional asset records with:
      - Location = 'In Cupboard'
      - Meaningful descriptive names ('Seagate 500GB Internal HDD', 'DDR 128MB RAM', 'LG DVD Writer')
      - Clean sequential tags ('IC-001', 'IC-002', ...)
      - Captured serial numbers, sizes, and conditions in extra_details
    """
    items = []

    # 1. Block 1: Internal Hard Disks (cols 0-3, top rows)
    for r in rows[1:14]:
        brand = _cell_text(r[0] if len(r) > 0 else '')
        size = _cell_text(r[1] if len(r) > 1 else '')
        sno = _cell_text(r[2] if len(r) > 2 else '')
        cond = _cell_text(r[3] if len(r) > 3 else '')
        if not brand and not size and not sno:
            continue
        if brand.lower() in ('internal hard disk', 'graphics card s.no'):
            continue
        name = f"{brand} {size} Internal HDD".strip()
        items.append({
            'name': name,
            'brand': brand or 'Seagate',
            'serial_number': sno,
            'conditions': cond,
            'status': 'working' if 'work' in cond.lower() else ('maintenance' if cond else 'working'),
            'item_type': 'Hard Disk',
            'size': size,
        })

    # 2. Block 2: Internal HDDs (cols 6-9, top section up to row 15)
    for r in rows[1:]:
        if len(r) <= 6:
            continue
        brand = _cell_text(r[6] if len(r) > 6 else '')
        size = _cell_text(r[7] if len(r) > 7 else '')
        sno = _cell_text(r[8] if len(r) > 8 else '')
        cond = _cell_text(r[9] if len(r) > 9 else '')
        if not brand and not size and not sno:
            continue
        if brand.lower() in ('internal hdd', 'internal hard disk', 'updated'):
            continue
        if not re.search(r'\b\d+(?:gb|tb|mb)\b', size, re.I) and brand.lower() not in ('seagate', 'segate', 'samsung', 'toshiba', 'wd'):
            continue

        if brand.lower() == 'segate':
            brand = 'Seagate'
        name = f"{brand} {size} Internal HDD".strip()
        items.append({
            'name': name,
            'brand': brand or 'Seagate',
            'serial_number': sno,
            'conditions': cond,
            'status': 'working' if 'work' in cond.lower() or cond.lower() == 'new' else ('maintenance' if cond else 'working'),
            'item_type': 'Hard Disk',
            'size': size,
        })

    # 3. Sub-table: Cards (col 0), RAM (cols 2-4), Accessories (col 6) in bottom section
    card_type = 'Graphics Card'
    for r in rows[14:]:
        col0 = _cell_text(r[0] if len(r) > 0 else '')
        if col0:
            if 'vga' in col0.lower():
                card_type = 'VGA Card'
            elif 'sound' in col0.lower():
                card_type = 'Sound Card'
            elif 'graphics' in col0.lower():
                card_type = 'Graphics Card'
            else:
                items.append({
                    'name': f"{card_type}",
                    'brand': card_type,
                    'serial_number': col0,
                    'conditions': 'In Cupboard storage',
                    'status': 'working',
                    'item_type': 'Graphics / Sound Card',
                    'size': '',
                })

        # RAM (cols 2, 3, 4)
        ram_size = _cell_text(r[2] if len(r) > 2 else '')
        ram_model = _cell_text(r[3] if len(r) > 3 else '')
        ram_sno = _cell_text(r[4] if len(r) > 4 else '')
        if ram_size and ram_size.lower() not in ('ram', 'no identiy', 'size'):
            name = f"{ram_model} {ram_size} RAM".strip()
            items.append({
                'name': name,
                'brand': ram_model or 'RAM',
                'serial_number': ram_sno if ram_sno.lower() != 'without label' else '',
                'conditions': 'In Cupboard storage',
                'status': 'working',
                'item_type': 'RAM',
                'size': ram_size,
            })
        elif ram_size.lower() == 'no identiy':
            items.append({
                'name': f"{ram_model} RAM (No Identity)".strip(),
                'brand': ram_model or 'RAM',
                'serial_number': '',
                'conditions': 'In Cupboard storage',
                'status': 'working',
                'item_type': 'RAM',
                'size': '',
            })

        # Accessories / Boxes / Cables (col 6)
        acc = _cell_text(r[6] if len(r) > 6 else '')
        if acc and not acc.lower().startswith('segate') and acc.lower() not in ('internal hdd', 'updated'):
            if not re.search(r'\b\d+(?:gb|tb)\b', acc, re.I) or 'box' in acc.lower() or 'adapter' in acc.lower():
                items.append({
                    'name': acc,
                    'brand': 'Accessory',
                    'serial_number': '',
                    'conditions': 'In Cupboard storage',
                    'status': 'working',
                    'item_type': 'Accessory / Box',
                    'size': '',
                })

    header = ["Asset Tag", "Name", "Brand", "Serial Number", "Status", "Location", "Item Type", "Size", "Condition Details"]
    data_rows = []
    tag_counter = 1
    existing_tags = existing_tags or set()
    seen_tags = seen_tags or set()
    for itm in items:
        while f"ic-{tag_counter:03d}" in existing_tags or f"ic-{tag_counter:03d}" in seen_tags:
            tag_counter += 1
        tag = f"IC-{tag_counter:03d}"
        seen_tags.add(tag.lower())
        tag_counter += 1
        data_rows.append([
            tag,
            itm['name'],
            itm['brand'],
            itm['serial_number'],
            itm['status'],
            'In Cupboard',
            itm['item_type'],
            itm['size'],
            itm['conditions'],
        ])

    return header, data_rows


def parse_uploaded_workbook_sheets(uploaded_file, workstation_out=None):
    """Multi-sheet import: each sheet TAB is one asset category (e.g. a tab
    named 'Monitor' imports into the Monitor category), so every sheet can
    have its own column layout — no common schema required across sheets.

    Matching order for a tab name:
      1. exact/normalized match to an existing category name
      2. SHEET_NAME_ALIASES (e.g. 'Bluetooth' -> 'Bluetooth Device')
      3. fallback to the catch-all 'Other Asset' category, so a sheet that
         doesn't fit any named category still gets imported instead of
         silently dropped — matching how the dashboard already buckets
         low-importance categories (Bluetooth Device, Biometric Device,
         Networking Equipment, etc.) under "Other Asset".

    workstation_out: optional dict. When given, a tab named 'Workstation' is
      NOT skipped: its header and data rows are stored in
      workstation_out["header"] / workstation_out["rows"] so the caller can
      validate them with validate_workstation_rows() and import workstations
      in the same upload as the categories.

    Returns (sheets, skipped_sheets, rerouted_sheets):
      sheets: list of (sheet_name, header_row, data_rows, category) for every
        sheet that has data — every non-empty sheet gets a category now,
        via exact match, alias, or the Other Asset fallback.
      skipped_sheets: list of (sheet_name, reason) for sheets that were
        genuinely empty (no header or no data rows) — these are reported to
        the admin but don't block importing the sheets that DID have data.
      rerouted_sheets: list of (sheet_name, reason) for sheets that WERE
        imported, but under the 'Other Asset' fallback category because
        their tab name didn't match (or alias to) a real category — shown
        to the admin as a heads-up, not an error.
    """
    filename = (uploaded_file.name or "").lower()
    raw_bytes = read_upload_bytes(uploaded_file)

    # Support old .xls files (even if misnamed as .xlsx)
    if _is_xls_bytes(raw_bytes):
        try:
            all_sheet_rows = _read_xls_all_sheets(raw_bytes)
        except Exception as exc:
            raise ImportFileError(f"Couldn't open the Excel file: {exc}")
    elif filename.endswith(".xlsx") or filename.endswith(".xls"):
        try:
            wb_openpyxl = load_workbook(filename=io.BytesIO(raw_bytes), data_only=True, read_only=True)
        except Exception as exc:
            raise ImportFileError(f"Couldn't open the Excel file: {exc}")
        all_sheet_rows = {}
        for sname in wb_openpyxl.sheetnames:
            ws = wb_openpyxl[sname]
            all_sheet_rows[sname] = [
                list(r) for r in ws.iter_rows(values_only=True)
                if any(_cell_text(c) for c in r)
            ]
    else:
        raise ImportFileError("Multi-sheet import requires an .xlsx or .xls file.")

    categories_by_norm_name = {_normalize_header(c.name): c for c in AssetCategory.objects.all()}
    fallback_cat = categories_by_norm_name.get(_normalize_header(FALLBACK_CATEGORY_NAME))

    sheets = []
    skipped_sheets = []
    rerouted_sheets = []
    total_rows = 0
    for sheet_name, rows in all_sheet_rows.items():
        rows = [list(r) for r in rows]
        if not rows:
            skipped_sheets.append((sheet_name, "sheet is empty"))
            continue

        norm_name = _normalize_header(sheet_name)
        skip_n = SHEET_LEADING_ROWS_TO_SKIP.get(norm_name, 0)
        if skip_n and rows:
            # Only drop the metadata line if row 1 really IS a metadata line
            # (e.g. "As On Date | 03-06-2015"). If row 1 already holds the
            # sheet's real header (S.No / Incident Type / ...), skipping it
            # would turn the first DATA row into the header and silently lose
            # that record.
            _tmpl = get_sheet_template(re.sub(r"\s*\(item \d+\)$", "", sheet_name))
            _tmpl_headers = {
                _normalize_header(c["header"]) for c in (_tmpl or []) if c.get("header")
            }
            _first_row_headers = {
                _normalize_header(c) for c in rows[0] if _cell_text(c)
            }
            if len(_first_row_headers & _tmpl_headers) >= 2:
                skip_n = 0
        if skip_n:
            rows = rows[skip_n:]
            if not rows:
                skipped_sheets.append((sheet_name, "sheet is empty"))
                continue

        header, data_rows = rows[0], rows[1:]
        if not data_rows:
            skipped_sheets.append((sheet_name, "no data rows below the header row"))
            continue

        if norm_name in FORCE_OTHER_ASSET_SHEETS:
            cupboard_header, cupboard_data_rows = _parse_inside_cupboard_sheet_rows(rows)
            if cupboard_data_rows:
                total_rows += len(cupboard_data_rows)
                sheets.append((
                    sheet_name,
                    cupboard_header,
                    cupboard_data_rows,
                    fallback_cat,
                    list(range(len(cupboard_header)))
                ))
            continue

        cat = None
        if norm_name not in FORCE_OTHER_ASSET_SHEETS:
            cat = categories_by_norm_name.get(norm_name)
            if not cat:
                alias_target = SHEET_NAME_ALIASES.get(norm_name)
                if alias_target:
                    cat = categories_by_norm_name.get(_normalize_header(alias_target))
        if norm_name == "workstation" and workstation_out is not None:
            workstation_out["header"] = header
            workstation_out["rows"] = data_rows
            continue
        if (cat is not None and cat.name.strip().lower() == "workstation") or norm_name == "workstation":
            # Workstation is its own module now, not a normal Asset Category
            # — a generic multi-sheet upload must never create new Asset
            # rows under it. The 45 existing legacy Workstation records are
            # untouched; this only blocks NEW ones from sneaking in via a
            # sheet tab literally named "Workstation".
            skipped_sheets.append(
                (sheet_name, "Workstation is managed as its own module, not a normal "
                             "Asset Category — this sheet was not imported")
            )
            continue
        used_fallback = False
        if not cat:
            cat = fallback_cat
            used_fallback = True
        if not cat:
            # Only happens if even the "Other Asset" category is missing.
            skipped_sheets.append(
                (sheet_name, f"tab name '{sheet_name}' doesn't match any existing asset category")
            )
            continue

        total_rows += len(data_rows)
        block_col_groups = SHEET_BLOCK_SPLITS.get(norm_name)
        if block_col_groups:
            for label, sub_header, sub_rows, orig_cols in _split_sheet_into_blocks(
                sheet_name, list(header), [list(r) for r in data_rows], block_col_groups
            ):
                sheets.append((label, sub_header, sub_rows, cat, orig_cols))
        else:
            full_cols = list(range(len(header)))
            sheets.append((sheet_name, list(header), [list(r) for r in data_rows], cat, full_cols))
        if used_fallback:
            rerouted_sheets.append(
                (sheet_name, f"tab name '{sheet_name}' didn't match a category — filed under 'Other Asset'")
            )

    if not sheets:
        if skipped_sheets and all(
            reason.lower().startswith("workstation") for _sname, reason in skipped_sheets
        ):
            raise ImportFileError(
                "This file only contains a 'Workstation' sheet, which isn't "
                "imported here — Workstation is managed as its own module, "
                "not a normal Asset Category. Remove that sheet (or add "
                "other category sheets alongside it) and re-upload."
            )
        raise ImportFileError(
            "No sheet tab names matched an existing asset category, and the "
            "'Other Asset' catch-all category is missing. Add that category "
            "first (or rename a tab to match one exactly), then re-upload."
        )
    if total_rows > MAX_IMPORT_ROWS:
        raise ImportFileError(
            f"This file has {total_rows} data rows across all sheets — please "
            f"split it into batches of {MAX_IMPORT_ROWS} or fewer."
        )

    return sheets, skipped_sheets, rerouted_sheets


def _diff_live_fields(category, header_row, col_to_field):
    """Same purpose as sheet_templates.diff_headers (missing / unexpected
    header notices), but compares against this category's LIVE active
    AssetField labels instead of the frozen original workbook headers.
    Used once a category has been configured in the Category Builder, so a
    field that's been renamed, added, or deactivated is what an admin gets
    warned about on upload — never a stale legacy header from the original
    workbook.

    missing: labels of ACTIVE REQUIRED fields not found in the upload
      (unlike the legacy diff, optional live fields are never flagged as
      "missing" just because one particular upload happens to omit them
      — that's normal for a dynamic, admin-configurable field set).
    unexpected: uploaded columns that don't match any active field label
      (columns already claimed by the common Asset columns — Asset Tag,
      Status, Notes, etc. — are excluded via get_extra_columns/col_to_field,
      same as everywhere else this data is used).
    """
    fields = get_category_fields(category)
    field_norms = {_normalize_header(f["label"]) for f in fields}
    extra_cols = get_extra_columns(header_row, col_to_field)
    uploaded_norms = {_normalize_header(h) for h in extra_cols.values()}
    missing = [
        f["label"] for f in fields
        if f.get("required") and _normalize_header(f["label"]) not in uploaded_norms
    ]
    unexpected = [h for h in extra_cols.values() if _normalize_header(h) not in field_norms]
    return missing, unexpected


def validate_workbook_sheets(sheets):
    """Validates every sheet from parse_uploaded_workbook_sheets(), sharing
    asset-tag duplicate tracking across ALL sheets (so the same tag can't
    sneak in twice via two different tabs). Returns (results, sheet_errors, row_warnings):
      results: combined list of row-result dicts (each tagged with 'sheet').
      sheet_errors: list of (sheet_name, message) for sheets whose header row
        was missing a required column, or had ambiguous headers — that sheet
        is skipped but the rest of the workbook is still processed.
      row_warnings: list of (sheet_name, row_number, message) for individual
        rows that imported but needed a correction — currently just the
        Employee_List "name typed into the ID column" case (see
        EMPLOYEE_NAME_AS_ID_FALLBACK_SHEETS) — so the admin can review them
        instead of the swap happening silently.
    """
    categories_by_name = {c.name.strip().lower(): c for c in AssetCategory.objects.all()}
    # Lower-cased, since the DB's unique index on asset_tag is
    # case-insensitive (MySQL's default collation) — comparing with plain
    # case-sensitive Python strings here would let e.g. '1GB' and '1gb'
    # both look "new" and pass validation, then collide at save time as a
    # raw IntegrityError instead of a friendly per-row message.
    existing_tags = {t.lower() for t in Asset.objects.values_list("asset_tag", flat=True)}
    # Maps tag -> (row_number, sheet_name) so cross-sheet duplicates can be
    # auto-suffixed instead of errored (same tag on two different tabs is a
    # coincidence, not a conflict — e.g. both Keyboard and Mouse have "APPLE").
    seen_tags_in_file = {}
    # Maps tag -> the actual result dict for the most recent row saved under
    # that tag, shared across every sheet/block call in this workbook (NOT
    # local to one _validate_sheet_rows call) — so a same-sheet duplicate can
    # be superseded even when the two occurrences come from two different
    # calls, e.g. Employee_List's two blocks are validated as two separate
    # calls but must still be able to supersede each other.
    same_entity_tag_results = {}
    # Shared across every sheet/block call so Employee_List's two blocks
    # (validated in two separate calls) keep counting placeholder IDs up
    # from where the other block left off, instead of both starting at 1.
    employee_placeholder_counter = [1]
    seen_employee_names = {}
    row_warnings = []
    existing_fingerprints = _load_existing_fingerprints()
    template_notices = []  # (sheet_name, missing_headers, unexpected_headers)

    all_results = []
    sheet_errors = []
    for sheet_name, header_row, data_rows, category, orig_col_indices in sheets:
        try:
            col_to_field = map_headers(
                header_row, require_category=False, require_name_and_tag=False,
                sheet_name=sheet_name,
            )
        except ImportFileError as exc:
            sheet_errors.append((sheet_name, str(exc)))
            continue

        base_name_for_template = re.sub(r"\s*\(item \d+\)$", "", sheet_name)
        sheet_template = get_sheet_template(base_name_for_template)
        if is_category_db_configured(category):
            # DB configuration (Category Builder) is the source of truth —
            # same priority rule export/sample-template/forms already use.
            missing, unexpected = _diff_live_fields(category, header_row, col_to_field)
            if missing or unexpected:
                template_notices.append((sheet_name, missing, unexpected))
        elif sheet_template is not None and sheet_name == base_name_for_template:
            missing, unexpected = _diff_template_headers(base_name_for_template, header_row)
            if missing or unexpected:
                template_notices.append((sheet_name, missing, unexpected))
        tag_fallback_idx = None
        if "asset_tag" not in col_to_field.values():
            tag_fallback_idx = _resolve_tag_fallback_idx(sheet_name, header_row, col_to_field)

        base_name = re.sub(r"\s*\(item \d+\)$", "", sheet_name)
        collision_name = (
            base_name if _normalize_header(base_name) in SHEET_BLOCK_SPLITS_SAME_ENTITY
            else sheet_name
        )

        results = _validate_sheet_rows(
            header_row, data_rows, col_to_field, categories_by_name,
            existing_tags, seen_tags_in_file, forced_category=category,
            tag_fallback_idx=tag_fallback_idx, current_sheet_name=collision_name,
            same_entity_tag_results=same_entity_tag_results,
            employee_id_fallback=_normalize_header(base_name) in EMPLOYEE_NAME_AS_ID_FALLBACK_SHEETS,
            employee_placeholder_counter=employee_placeholder_counter,
            row_warnings=row_warnings,
            sheet_template=sheet_template, orig_col_indices=orig_col_indices,
            existing_fingerprints=existing_fingerprints,
            seen_employee_names=seen_employee_names,
        )
        for r in results:
            r["sheet"] = sheet_name
        all_results.extend(results)
    return all_results, sheet_errors, row_warnings, template_notices


def map_headers(header_row, require_category=True, require_name_and_tag=True, sheet_name=None):
    """Maps column index -> model field name, purely by matching each column's
    header TEXT against IMPORT_COLUMNS / HEADER_ALIASES — never by position.
    So 'Category' can be column A or column H, doesn't matter.

    require_category=False is used for multi-sheet imports, where the sheet's
    tab name already determines the category for every row on that sheet —
    in that mode a 'Category' header isn't required, and even if one exists
    it's ignored (falls through to extra_details) rather than overriding the
    sheet's category.

    require_name_and_tag=False is ALSO used for multi-sheet imports: a sheet
    doesn't have to spell its ID column "Asset Tag" or have a "Name" column at
    all — it can use its own real field names (e.g. 'System No', 'System
    Name'). If no column matches the Asset Tag field/aliases, the sheet's
    FIRST column (whatever it's called) is used as the unique ID instead. If
    no column matches Name, none is forced here — validate_rows builds a
    default name from the category + tag. Every other column, under its own
    original header, still goes to extra_details untouched.

    Raises ImportFileError if:
      - a required column's header is missing entirely, or
      - the same field is claimed by more than one column header (ambiguous —
        we refuse to guess which one is the real one).
    """
    label_to_field = LABEL_TO_FIELD
    column_required = COLUMN_REQUIRED
    import_columns = IMPORT_COLUMNS
    if not require_category:
        label_to_field = {k: v for k, v in label_to_field.items() if v != "category"}
        column_required = column_required - {"category"}
        import_columns = [c for c in import_columns if c[1] != "category"]
    if not require_name_and_tag:
        column_required = column_required - {"asset_tag", "name"}
        import_columns = [c for c in import_columns if c[1] not in ("asset_tag", "name")]

    col_to_field = {}
    field_to_cols = {}  # field -> list of column indexes claiming it
    sheet_overrides = None
    if sheet_name:
        import re as _re
        base_sheet_name = _re.sub(r"\s*\(item \d+\)$", "", sheet_name)
        sheet_overrides = SHEET_HEADER_OVERRIDES.get(_normalize_header(base_sheet_name))
    for idx, cell in enumerate(header_row):
        norm = _normalize_header(cell)
        if not norm:
            continue
        field = None
        if sheet_overrides is not None and norm in sheet_overrides:
            field = sheet_overrides[norm]
        else:
            field = label_to_field.get(norm) or HEADER_ALIASES.get(norm)
        if field:
            field_to_cols.setdefault(field, []).append(idx)

    ambiguous = {f: idxs for f, idxs in field_to_cols.items() if len(idxs) > 1}
    if ambiguous:
        amb_labels = []
        for field, idxs in ambiguous.items():
            label = next(l for l, fld, _r, _k in IMPORT_COLUMNS if fld == field)
            cols = ", ".join(get_column_letter(i + 1) for i in idxs)
            amb_labels.append(f"'{label}' matched by columns {cols}")
        raise ImportFileError(
            "Ambiguous header(s) — more than one column matches the same field: "
            + "; ".join(amb_labels) +
            ". Please keep only one column per field and re-upload."
        )

    for field, idxs in field_to_cols.items():
        col_to_field[idxs[0]] = field

    matched_fields = set(col_to_field.values())
    missing = column_required - matched_fields
    if missing:
        missing_labels = [label for label, field, _r, _k in import_columns if field in missing]
        expected = ", ".join(label for label, _f, _r, _k in import_columns)
        raise ImportFileError(
            "Missing required column(s): " + ", ".join(missing_labels) +
            ". Expected headers (first row): " + expected
        )
    return col_to_field


def _resolve_tag_fallback_idx(sheet_name, header_row, col_to_field):
    """Only used in multi-sheet mode when no column's header matched Asset
    Tag. Picks which column's value to use as the row's unique ID:
      1. SHEET_TAG_COLUMN_HINTS[sheet_name], if this specific sheet has a
         known real identifier column (e.g. Software and OS -> 'System No')
         — every sheet has its own field layout, so this is checked by
         sheet name, never applied to any other sheet;
      2. a column already recognized as Serial Number (a real unique ID in
         most legacy sheets — e.g. Hard Disk's first column is 'Size',
         which is NOT unique, but its 'Serial No' column is);
      3. otherwise the sheet's first non-empty header column, whatever it's
         called (e.g. 'System No', 'Monitor No').
    Returns a column index, or None if the sheet has no headers at all.
    Does NOT remove that column from col_to_field — it keeps doing its
    normal job (e.g. still populating serial_number) in addition to being
    used as the tag.
    """
    hint = SHEET_TAG_COLUMN_HINTS.get(_normalize_header(sheet_name))
    if hint:
        for idx, cell in enumerate(header_row):
            if _normalize_header(cell) == hint:
                return idx
    for idx, field in col_to_field.items():
        if field == "serial_number":
            return idx
    for idx, cell in enumerate(header_row):
        if _normalize_header(cell):
            return idx
    return None


def get_extra_columns(header_row, col_to_field):
    """Returns {column_index: header_text} for every column that ISN'T one of
    the fixed IMPORT_COLUMNS/aliases. These are the category-specific columns
    (Monitor's Type/Color/Size, UPS's Color/Model, Bluetooth's Devices/Remark,
    etc). They are never forced into the common schema — each row keeps them,
    under their own original header name, in the `extra_details` JSON field.

    If the same header text appears more than once in a sheet (e.g. a sheet
    that repeats a whole block of columns side-by-side, like two "Conditions"
    columns for two different items packed into one row), later occurrences
    get " (2)", " (3)"... appended so they don't silently overwrite the
    earlier one's value when saved into extra_details.
    """
    extra_cols = {}
    seen_counts = {}
    for idx, cell in enumerate(header_row):
        if idx in col_to_field:
            continue
        header_text = _cell_text(cell)
        if not header_text:
            continue
        if _normalize_header(header_text) == "branch":
            # Exports carry a Branch column for reading; the branch itself is
            # chosen on the import screen, so it must not be saved as data.
            continue
        seen_counts[header_text] = seen_counts.get(header_text, 0) + 1
        if seen_counts[header_text] > 1:
            header_text = f"{header_text} ({seen_counts[header_text]})"
        extra_cols[idx] = header_text
    return extra_cols


def build_mapping_preview(header_row, data_rows, col_to_field):
    """For the preview screen: shows, for every column actually present in the
    uploaded file, which field (if any) it was matched to and a sample value
    from the first data row — so the admin can visually confirm the mapping
    is correct (e.g. catch a 'Purchase Date' column that actually holds
    license keys) BEFORE clicking Confirm & Import.
    """
    field_to_label = {field: label for label, field, _r, _k in IMPORT_COLUMNS}
    sample_row = data_rows[0] if data_rows else []
    preview = []
    for idx, cell in enumerate(header_row):
        header_text = _cell_text(cell)
        if not header_text:
            continue
        field = col_to_field.get(idx)
        sample = sample_row[idx] if idx < len(sample_row) else ""
        preview.append({
            "excel_column": get_column_letter(idx + 1),
            "header_text": header_text,
            "detected_field": field_to_label.get(field, "Extra Details (kept as-is, category-specific)"),
            "matched": field is not None,
            "sample_value": _cell_text(sample),
        })
    return preview


BLANK_DATE_PLACEHOLDERS = {"-", "--", "n/a", "na", "none", "nil"}


def _parse_date_cell(value):
    import re
    if value in (None, ""):
        return None, None
    if isinstance(value, datetime):
        return value.date(), None
    if isinstance(value, date):
        return value, None
    text = str(value).strip()
    if not text or text.lower() in BLANK_DATE_PLACEHOLDERS:
        return None, None
    # Try to parse the full text first
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date(), None
        except ValueError:
            continue
    # If the full text didn't parse (e.g. "Battery changed on (18-05-2015)"),
    # extract the first date-like substring and try again — the rest of the
    # text (free notes, extra context) is silently ignored.
    date_pattern = re.compile(
        r"\b(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4}|\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})\b"
    )
    match = date_pattern.search(text)
    if match:
        candidate = match.group(1)
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date(), None
            except ValueError:
                continue
    return None, f"Unrecognized date '{text}' (use YYYY-MM-DD)"



def _parse_bool_cell(value):
    if value in (None, ""):
        return True, None
    text = str(value).strip().lower()
    if not text:
        return True, None
    if text in TRUE_WORDS:
        return True, None
    if text in FALSE_WORDS:
        return False, None
    return None, f"Unrecognized value '{value}' for Is Active (use yes/no)"


def _cell_text(value):
    if value is None:
        return ""
    return str(value).strip()


def validate_rows(header_row, data_rows, col_to_field=None):
    """
    Single-sheet entry point (csv/txt, or a plain single-sheet xlsx). Returns a
    list of row-result dicts:
    {row_number, errors: [...], is_valid: bool, fields: {...cleaned...}, raw: {label: value}}
    `fields['category']` holds the AssetCategory id when valid.
    """
    if col_to_field is None:
        col_to_field = map_headers(header_row)  # may raise ImportFileError

    categories_by_name = {c.name.strip().lower(): c for c in AssetCategory.objects.all()}
    # See the matching comment in validate_workbook_sheets(): lower-cased to
    # match the DB's case-insensitive unique index on asset_tag.
    existing_tags = {t.lower() for t in Asset.objects.values_list("asset_tag", flat=True)}
    seen_tags_in_file = {}

    return _validate_sheet_rows(
        header_row, data_rows, col_to_field, categories_by_name,
        existing_tags, seen_tags_in_file, forced_category=None,
    )


def _validate_sheet_rows(header_row, data_rows, col_to_field, categories_by_name,
                          existing_tags, seen_tags_in_file, forced_category=None,
                          tag_fallback_idx=None, current_sheet_name=None,
                          same_entity_tag_results=None, employee_id_fallback=False,
                          employee_placeholder_counter=None, row_warnings=None,
                          sheet_template=None, orig_col_indices=None,
                          existing_fingerprints=None, seen_employee_names=None):
    """Validates the rows of ONE sheet. `existing_tags` and `seen_tags_in_file`
    are shared across sheets by the caller so asset-tag duplicates are caught
    across the whole workbook, not just within one sheet.
    `forced_category` (an AssetCategory instance) is set for multi-sheet
    imports, where the sheet's tab name already determined the category —
    in that case there's no 'Category' column/value to validate per row.
    `tag_fallback_idx` (multi-sheet mode only): when no column's header
    matched 'Asset Tag', this column's raw value is used as the row's unique
    ID instead — on top of, not instead of, whatever field that column
    normally maps to (e.g. it can still populate Serial Number too).
    `current_sheet_name`: when set, cross-sheet tag collisions (same tag seen
    on a different tab) are auto-suffixed instead of errored, because the same
    short value appearing in two unrelated sheets (e.g. 'APPLE' in Keyboard
    AND Mouse) is a coincidence, not a data conflict.
    `same_entity_tag_results`: tag -> the actual result dict most recently
    saved under that tag, shared with the caller (validate_workbook_sheets)
    across ALL its calls to this function — not just this one sheet's rows —
    so a same-sheet duplicate (current_sheet_name matches the earlier
    occurrence's) can be superseded even when the earlier occurrence came
    from a previous call, e.g. a different block of a block-split sheet.
    `employee_id_fallback`: when True (Employee_List sheets), a row with a
    blank Name and an ID value that doesn't match a real Employee Id format
    (see _looks_like_employee_id) has that value used as the Name instead,
    with a placeholder ID generated — see _next_employee_placeholder_tag.
    `employee_placeholder_counter`/`row_warnings`: shared mutable state for
    that swap, passed in by validate_workbook_sheets (see its docstring)."""
    extra_cols = get_extra_columns(header_row, col_to_field)  # category-specific columns

    field_max_len = {
        "asset_tag": 50, "name": 150, "brand": 100, "model_number": 100,
        "serial_number": 150, "current_assigned_to": 150, "current_location": 150,
        "linked_workstation": 50,
    }

    if same_entity_tag_results is None:
        same_entity_tag_results = {}
    if employee_placeholder_counter is None:
        employee_placeholder_counter = [1]

    category_db_fields = {}

    sheet_tag_aliases = {}
    results = []
    for i, raw_row in enumerate(data_rows, start=1):
        pending_tag_key = None  # set below when this row claims a tag, so it
        merge_into_prev = None
        # can be recorded into same_entity_tag_results once its result exists
        row_map = {}
        for idx, field in col_to_field.items():
            row_map[field] = raw_row[idx] if idx < len(raw_row) else ""
        if "asset_tag" not in row_map and tag_fallback_idx is not None:
            row_map["asset_tag"] = raw_row[tag_fallback_idx] if tag_fallback_idx < len(raw_row) else ""

        errors = []
        fields = {}
        raw_display = {}
        raw_name_value = _cell_text(row_map.get("name"))

        # Category
        category_display_name = None  # used below to build a default Name
        if forced_category is not None:
            row_category = forced_category
            # This sheet's rows all land in the 'Other Asset' catch-all by
            # default (e.g. the 'others' tab) — but if this particular row
            # has a free-text device-type value that matches a REAL category
            # (Air Conditioner, Biometric Device, ...), route it there
            # instead, so those categories aren't stuck at 0 just because
            # their rows happened to be on the catch-all tab.
            if forced_category.name == FALLBACK_CATEGORY_NAME:
                device_type_text = ""
                for idx, cell in enumerate(header_row):
                    if _normalize_header(cell) in DEVICE_TYPE_HEADER_NAMES:
                        device_type_text = _cell_text(raw_row[idx] if idx < len(raw_row) else "")
                        break
                target_name = DEVICE_TYPE_CATEGORY_ALIASES.get(_normalize_header(device_type_text))
                if target_name:
                    matched_cat = categories_by_name.get(target_name.strip().lower())
                    if matched_cat:
                        row_category = matched_cat
            raw_display["Category"] = row_category.name
            fields["category"] = row_category.id
            category_display_name = row_category.name
        else:
            category_name = _cell_text(row_map.get("category"))
            raw_display["Category"] = category_name
            if not category_name:
                errors.append("Category is required")
            else:
                cat = categories_by_name.get(category_name.lower())
                if cat is not None and cat.name.strip().lower() == "workstation":
                    # Workstation is its own module, not a normal Asset
                    # Category — block new rows from being created under it
                    # via this generic single-sheet/CSV import path too.
                    errors.append(
                        "Category 'Workstation' can't be used here — Workstation "
                        "is managed as its own module, not a normal Asset Category"
                    )
                elif not cat:
                    errors.append(f"Unknown category '{category_name}'")
                else:
                    fields["category"] = cat.id
                    category_display_name = cat.name

        # Asset tag
        tag = _cell_text(row_map.get("asset_tag"))
        used_fallback_tag = tag_fallback_idx is not None and "asset_tag" not in col_to_field.values()

        # A row whose ONLY value is the generated-ID column (e.g. a trailing
        # "4" in the S.No column with nothing else on the row) is a blank
        # spreadsheet row, not a record.
        if used_fallback_tag and tag_fallback_idx is not None and tag:
            if not any(
                _cell_text(c) for j, c in enumerate(raw_row) if j != tag_fallback_idx
            ):
                continue

        # In these legacy sheets, a row with just an S.No but no actual Project Name
        # or Hard disk Name is a blank spreadsheet row, not a valid record.
        if not tag and not raw_name_value and category_display_name in ("Project Details", "Project Backup"):
            continue

        # Project Backup: generate independent PB-xxx tags so it never collides
        # with or duplicates the physical Hard Disk's asset tag.
        if category_display_name == "Project Backup":
            if not tag or used_fallback_tag or not tag.upper().startswith("PB-"):
                pb_num = 1
                while f"pb-{pb_num:03d}" in existing_tags or f"pb-{pb_num:03d}" in seen_tags_in_file:
                    pb_num += 1
                tag = f"PB-{pb_num:03d}"
                used_fallback_tag = True

        # Incident Register: generate independent INC-xxx tags
        if category_display_name == "Incident Register":
            if not tag or used_fallback_tag or not tag.upper().startswith("INC-"):
                inc_num = 1
                while f"inc-{inc_num:03d}" in existing_tags or f"inc-{inc_num:03d}" in seen_tags_in_file:
                    inc_num += 1
                tag = f"INC-{inc_num:03d}"
                used_fallback_tag = True
                row_map["status"] = "resolved"

        # Employee_List-specific data-quality fix: some rows have the
        # employee's actual NAME typed into the Employee Id column, with
        # EmployeeName left blank (a data-entry mistake, not a real ID like
        # "SPS002"/"TR1533"). Importing that value as the unique Employee ID
        # would misfile a person's name as their ID and leave Name showing an
        # auto-generated "Employee <name>" placeholder instead. Detect it and
        # swap: use the value as the Name, generate a safe placeholder ID,
        # and record a warning for the admin to review. Never touches a row
        # where BOTH Name and Employee Id are already present.
        if (
            employee_id_fallback and not raw_name_value and tag
            and not _looks_like_employee_id(tag)
        ):
            swapped_name = tag
            tag = _next_employee_placeholder_tag(
                employee_placeholder_counter, existing_tags, seen_tags_in_file
            )
            used_fallback_tag = True
            row_map["name"] = swapped_name
            if row_warnings is not None:
                row_warnings.append((
                    current_sheet_name, i,
                    f'Employee Id "{swapped_name}" looked like a name, not an ID, and '
                    f'Employee Name was blank — used it as the Name and generated '
                    f'placeholder ID "{tag}" instead.'
                ))

        if used_fallback_tag and len(tag) > 50:
            # Fallback tags come from a column that was never meant to be a
            # short unique ID (e.g. a full incident description sentence) —
            # trim it instead of letting the save fail later.
            tag = tag[:50]
        if not tag:
            # Auto-generate a tag if it's missing entirely (e.g. empty cell in the fallback column)
            cat_prefix = "".join(filter(str.isalnum, (category_display_name or "ITEM")))[:15].upper()
            tag = f"{cat_prefix}-R{i}"
            used_fallback_tag = True  # treat it as a fallback so it gets the suffixing loop if needed

        raw_display["AssetTag"] = tag
        # All membership checks below are case-insensitive (tag.lower()),
        # since the DB's unique index on asset_tag is case-insensitive —
        # '1GB' and '1gb' are the SAME tag as far as MySQL is concerned, so
        # existing_tags/seen_tags_in_file are keyed/compared in lowercase
        # too. The tag actually saved/displayed keeps its original casing.
        tag_key = tag.lower()
        original_tag_key = tag_key

        if original_tag_key in sheet_tag_aliases:
            tag = sheet_tag_aliases[original_tag_key]
            tag_key = tag.lower()
            raw_display["AssetTag"] = tag
        if not tag:
            errors.append("Asset Tag is required")
        elif not used_fallback_tag and len(tag) > 50:
            errors.append("Asset Tag is too long (max 50 characters)")
        elif tag_key in existing_tags and not used_fallback_tag:
            errors.append(f"Asset tag '{tag}' already exists")
        elif (used_fallback_tag and (tag_key in existing_tags or tag_key in seen_tags_in_file)) or (
            tag_key in seen_tags_in_file and (
                # Cross-sheet collision: same tag, different sheet → auto-suffix
                current_sheet_name is not None
                and seen_tags_in_file[tag_key][1] != current_sheet_name
            )
        ):
            # Auto-suffix: either a fallback-tag sheet with any collision, or a
            # cross-sheet collision (e.g. "APPLE" in both Keyboard and Mouse).
            # These are coincidences, not data errors — make the tag unique.
            base_tag, n = tag[:45], 2
            tag_key = tag.lower()
            while tag_key in existing_tags or tag_key in seen_tags_in_file:
                tag = f"{base_tag}-{n}"[:50]
                tag_key = tag.lower()
                n += 1
            sheet_tag_aliases[original_tag_key] = tag
            raw_display["AssetTag"] = tag
            seen_tags_in_file[tag_key] = (i, current_sheet_name)
            fields["asset_tag"] = tag
            pending_tag_key = tag_key
        elif tag_key in seen_tags_in_file:
            # Same sheet, same tag as an earlier row. This happens when a
            # legacy sheet stacks two tables in one tab (an older summary
            # block, then a fuller/updated block further down reusing the
            # same IDs) — the later block is the one worth keeping. So the
            # later row WINS: the earlier row is superseded (dropped from
            # the import) rather than this row being rejected as a duplicate.
            prev_row, prev_sheet = seen_tags_in_file[tag_key]
            prev_result = same_entity_tag_results.get(tag_key)
            if prev_result is not None:
                if category_display_name == "Project Backup":
                    merge_into_prev = prev_result
                else:
                    prev_result["is_valid"] = False
                    prev_result["errors"] = [
                        f"Superseded by a later row with the same tag (row {i})"
                    ]
            if not merge_into_prev:
                seen_tags_in_file[tag_key] = (i, current_sheet_name)
                fields["asset_tag"] = tag
                pending_tag_key = tag_key
        else:
            seen_tags_in_file[tag_key] = (i, current_sheet_name)
            fields["asset_tag"] = tag
            pending_tag_key = tag_key

        # Name
        name = _cell_text(row_map.get("name"))
        raw_display["Name"] = name
        if not name:
            if category_display_name:
                # No Name-like column on this sheet (e.g. Workstation,
                # Software and OS) — build a reasonable default from the
                # category + tag instead of rejecting every row (e.g.
                # "Workstation SPS019").
                name = f"{category_display_name} {tag}".strip() if tag else category_display_name
                if category_display_name == "Project Details" and tag:
                    name = tag  # the project name is the tag
                raw_display["Name"] = name
                fields["name"] = name
            else:
                errors.append("Name is required")
        else:
            fields["name"] = name

        # Simple text fields with max-length checks
        for field in ["brand", "model_number", "serial_number", "current_assigned_to",
                      "current_location", "linked_workstation", "notes"]:
            if field not in row_map:
                continue
            value = _cell_text(row_map.get(field))
            label = next(l for l, f, _r, _k in IMPORT_COLUMNS if f == field)
            raw_display[label.replace(" ", "")] = value
            max_len = field_max_len.get(field)
            if max_len and len(value) > max_len:
                errors.append(f"{label} is too long (max {max_len} characters)")
            else:
                fields[field] = value

        # Status
        status_text = _cell_text(row_map.get("status"))
        blank_default = "active" if category_display_name == "Project Details" else "working"
        raw_display["Status"] = status_text or blank_default.title()
        if not status_text:
            fields["status"] = blank_default
        else:
            mapped = STATUS_LOOKUP.get(_normalize_header(status_text))
            if not mapped:
                if forced_category is not None:
                    # Multi-sheet legacy-style sheets often have a free-text
                    # note in the Status column instead of a real status
                    # (e.g. "went to shipment client on 20170509..."). Rather
                    # than rejecting the row, default to Other and keep the
                    # original text in Notes so nothing is lost.
                    fields["status"] = "other"
                    existing_note = fields.get("notes", "")
                    note_addition = f"Status note: {status_text}"
                    fields["notes"] = f"{existing_note} {note_addition}".strip() if existing_note else note_addition
                    raw_display["Status"] = "Other"
                else:
                    errors.append(f"Unrecognized status '{status_text}' (valid: {VALID_STATUS_LABELS})")
            else:
                fields["status"] = mapped

        # Dates
        for field in ["purchase_date", "last_service_date"]:
            label = next(l for l, f, _r, _k in IMPORT_COLUMNS if f == field)
            raw_value = row_map.get(field)
            parsed, err = _parse_date_cell(raw_value)
            raw_display[label.replace(" ", "")] = _cell_text(raw_value)
            if err:
                if forced_category is not None:
                    # Multi-sheet legacy-style sheets often have a free-text
                    # note in a date column instead of a real date (e.g.
                    # "Batt changed on 10-03-15"). Rather than rejecting the
                    # whole row, drop the date and keep the original text in
                    # Notes so nothing is lost.
                    fields[field] = None
                    existing_note = fields.get("notes", "")
                    note_addition = f"{label}: {_cell_text(raw_value)}"
                    fields["notes"] = f"{existing_note} {note_addition}".strip() if existing_note else note_addition
                    raw_display[label.replace(" ", "")] = f"{_cell_text(raw_value)} (kept as note — couldn't parse as a date)"
                else:
                    errors.append(err)
            else:
                fields[field] = parsed

        # Is active
        parsed_bool, err = _parse_bool_cell(row_map.get("is_active"))
        raw_display["IsActive"] = "Yes" if parsed_bool else ("No" if parsed_bool is False else _cell_text(row_map.get("is_active")))
        if err:
            errors.append(err)
        else:
            fields["is_active"] = parsed_bool

        # Extra Details.
        #
        # When this sheet has a registered template (sheet_templates.py),
        # EVERY column of the original sheet — including ones also used
        # above for the internal asset_tag/name/status/date bookkeeping,
        # AND blank-header columns that have data — is stored under that
        # template's exact original header text (or an internal-only key
        # for a blank/duplicate header; see sheet_templates._cols). This is
        # what lets the preview and the export reproduce that sheet's own
        # field names/order, instead of the common Asset schema.
        #
        # Sheets with no registered template keep the older behaviour:
        # only columns NOT claimed by the fixed IMPORT_COLUMNS are kept,
        # under their own original header name.
        extra_details = {}
        skipped_sensitive = []
        template_row_display = None
        template_headers_list = None

        row_cat_id = fields.get("category")
        db_fields = None
        is_db_configured = False
        
        if row_cat_id:
            if row_cat_id not in category_db_fields:
                from .models import AssetField, AssetCategory
                cat_obj = AssetCategory.objects.get(pk=row_cat_id)
                if AssetField.objects.filter(category=cat_obj).exists():
                    fields_qs = AssetField.objects.filter(category=cat_obj, is_active=True).prefetch_related('options')
                    category_db_fields[row_cat_id] = list(fields_qs)
                else:
                    category_db_fields[row_cat_id] = None
            db_fields = category_db_fields[row_cat_id]
            is_db_configured = db_fields is not None

        if is_db_configured:
            label_to_field = {_normalize_header(f.label): f for f in db_fields}
            
            for idx, header_text in extra_cols.items():
                if _is_sensitive_field(header_text):
                    skipped_sensitive.append(header_text)
                    continue
                
                value = _cell_text(raw_row[idx] if idx < len(raw_row) else "")
                norm_header = _normalize_header(header_text)
                
                if norm_header in label_to_field:
                    f = label_to_field[norm_header]
                    if value == "" and f.required:
                        errors.append(f"{f.label} is required")
                    elif value != "":
                        if f.field_type == 'select':
                            opts = {opt.value.lower(): opt.value for opt in f.options.all() if opt.is_active}
                            opt_labels = {opt.label.lower(): opt.value for opt in f.options.all() if opt.is_active}
                            val_lower = value.lower()
                            if val_lower in opts:
                                extra_details[f.key] = opts[val_lower]
                            elif val_lower in opt_labels:
                                extra_details[f.key] = opt_labels[val_lower]
                            else:
                                errors.append(f"Invalid option '{value}' for {f.label}")
                        elif f.field_type == 'number':
                            try:
                                float(value)
                                extra_details[f.key] = value
                            except ValueError:
                                errors.append(f"{f.label} must be a number")
                        elif f.field_type == 'date':
                            parsed_d, err_d = _parse_date_cell(value)
                            if err_d:
                                errors.append(f"Invalid date in {f.label}: {err_d}")
                            else:
                                extra_details[f.key] = parsed_d.isoformat() if parsed_d else value
                        else:
                            extra_details[f.key] = value
                else:
                    if value != "":
                        extra_details[header_text] = value
            
            for f in db_fields:
                if f.required and f.key not in extra_details:
                    errors.append(f"{f.label} is required")

            # Build a real per-column preview (Asset Tag / Name / Status
            # plus every active Category Builder field, in their configured
            # order) instead of leaving template_headers_list as None here.
            # Previously a DB-configured category ALWAYS fell through to the
            # generic 5-column table in the preview — even when it had a
            # perfectly good set of named fields — because this branch never
            # populated template_headers_list/template_row_display the way
            # the sheet_template branch below does. The saved data was never
            # affected, only what admins could see before confirming.
            template_headers_list = ["Asset Tag", "Name", "Status"] + [f.label for f in db_fields]
            template_row_display = [
                {"header": "Asset Tag", "value": raw_display.get("AssetTag", "")},
                {"header": "Name", "value": raw_display.get("Name", "")},
                {"header": "Status", "value": raw_display.get("Status", "")},
            ]
            for f in db_fields:
                if f.label in skipped_sensitive:
                    display_value = "•••• (hidden)"
                elif f.field_type == "select" and extra_details.get(f.key):
                    opt = next((o for o in f.options.all() if o.value == extra_details[f.key]), None)
                    display_value = opt.label if opt else extra_details[f.key]
                else:
                    display_value = extra_details.get(f.key, "")
                template_row_display.append({"header": f.label, "value": display_value})

        elif sheet_template is not None:
            col_map = orig_col_indices if orig_col_indices is not None else list(range(len(header_row)))
            tmpl_by_index = {c["col_index"]: c for c in sheet_template}
            # Only the columns THIS call actually has — for a block-split
            # sheet (e.g. Employee_List's two side-by-side blocks), that's
            # just this block's own 3-4 columns, never the other block's,
            # so the preview never shows a mismatched/empty tail of columns
            # that belong to a different item.
            template_headers_list = [
                tmpl_by_index[orig_idx]["header"] for orig_idx in col_map if orig_idx in tmpl_by_index
            ]
            template_row_display = []
            for local_idx, orig_idx in enumerate(col_map):
                col_def = tmpl_by_index.get(orig_idx)
                if col_def is None:
                    continue
                raw_val = raw_row[local_idx] if local_idx < len(raw_row) else ""
                text_val = _cell_text(raw_val)
                header_text = col_def["header"]
                if header_text and _is_sensitive_field(header_text):
                    skipped_sensitive.append(header_text)
                    template_row_display.append({"header": header_text, "value": "•••• (hidden)"})
                    continue
                if text_val:
                    extra_details[col_def["key"]] = text_val
                template_row_display.append({"header": header_text, "value": text_val})
        else:
            for idx, header_text in extra_cols.items():
                if _is_sensitive_field(header_text):
                    skipped_sensitive.append(header_text)
                    continue
                value = _cell_text(raw_row[idx] if idx < len(raw_row) else "")
                if value:
                    extra_details[header_text] = value

        # Air Conditioner: the sheet's "Capacity / Location" column is stored
        # under the clean "capacity_location" key the category page, View
        # page and Add/Edit form all read. Status and Serviced already went
        # to the real status / last_service_date fields above, so the raw
        # template copies are dropped — otherwise a leftover "Status" key
        # would show up as the capacity in the legacy-fallback display.
        if (category_display_name or "").strip().lower() == "air conditioner":
            capacity = extra_details.get("Capacity / Location", "")
            # Legacy "Others" sheet layout (ID / Device Type / Device Name /
            # Status / Details): its "Status" column holds the capacity +
            # location ("1.5 TON, ADMIN ROOM") and "Details" holds the service
            # note ("serviced on 28/2/2020"). Use them instead of dropping them.
            raw_status_text = (extra_details.get("Status") or "").strip()
            if not capacity and raw_status_text and not STATUS_LOOKUP.get(_normalize_header(raw_status_text)):
                capacity = raw_status_text
                if fields.get("status") == "other":
                    fields["status"] = "working"
                note_prefix = f"Status note: {raw_status_text}"
                if fields.get("notes"):
                    fields["notes"] = fields["notes"].replace(note_prefix, "").strip()
            details_text = (extra_details.get("Details") or "").strip()
            if details_text and not fields.get("last_service_date"):
                parsed_service, _err = _parse_date_cell(details_text)
                if parsed_service:
                    fields["last_service_date"] = parsed_service
                else:
                    existing_note = fields.get("notes", "")
                    fields["notes"] = f"{existing_note} {details_text}".strip()
            extra_details = {"capacity_location": capacity} if capacity else {}

        fields["extra_details"] = extra_details

        # Validate relationship references (e.g. Project Backup referencing Hard Disk)
        if category_display_name == "Project Backup" and extra_details:
            hdd_ref = extra_details.get("Hard disk Name")
            if hdd_ref:
                from .relations import resolve_asset_reference
                ref_clean = str(hdd_ref).strip().lower()
                stripped = re.sub(r'\s+\d+(?:tb|gb|mb)$', '', ref_clean, flags=re.I).strip()
                resolved_hdd = resolve_asset_reference(hdd_ref, "Hard Disk")
                if not resolved_hdd and ref_clean not in seen_tags_in_file and stripped not in seen_tags_in_file:
                    if row_warnings is not None:
                        row_warnings.append((
                            current_sheet_name, i,
                            f'Referenced Hard Disk "{hdd_ref}" was not found in Hard Disk records.'
                        ))
            mirror_ref = extra_details.get("Backup Available HDD")
            if mirror_ref:
                from .relations import resolve_asset_reference
                ref_clean = str(mirror_ref).strip().lower()
                stripped = re.sub(r'\s+\d+(?:tb|gb|mb)$', '', ref_clean, flags=re.I).strip()
                resolved_mirror = resolve_asset_reference(mirror_ref, "Hard Disk")
                if not resolved_mirror and ref_clean not in seen_tags_in_file and stripped not in seen_tags_in_file:
                    if row_warnings is not None:
                        row_warnings.append((
                            current_sheet_name, i,
                            f'Referenced Backup Mirror HDD "{mirror_ref}" was not found in Hard Disk records.'
                        ))

        # No real ID column on this sheet -> tag was generated, so the tag
        # check above can't tell a re-import from a new row. Compare content.
        if used_fallback_tag and existing_fingerprints and fields.get("category"):
            _fp = _content_fingerprint(fields["category"], fields, extra_details)
            if _fp is not None and _fp in existing_fingerprints:
                errors.append("Already exists — same data is already in the register")

        if skipped_sensitive:
            # Don't silently drop this — tell whoever's reviewing the import
            # preview that a column was intentionally left out, so it isn't
            # mistaken for a mapping bug.
            names = ", ".join(skipped_sensitive)
            raw_display["_SkippedSensitiveColumns"] = (
                f"Not imported (looks like a secret): {names}"
            )

        this_result = {
            "row_number": i,
            "errors": errors,
            "is_valid": not errors,
            "fields": fields,
            "raw": raw_display,
            "template_headers": template_headers_list,
            "template_row": template_row_display,
        }

        if merge_into_prev:
            # Merge extra_details
            for k, v in fields["extra_details"].items():
                existing = merge_into_prev["fields"]["extra_details"].get(k, "")
                if v and v not in existing:
                    merge_into_prev["fields"]["extra_details"][k] = f"{existing} | {v}".strip(" |")
            
            # Merge template_row display for the preview
            if template_row_display and merge_into_prev.get("template_row"):
                for col, prev_col in zip(template_row_display, merge_into_prev["template_row"]):
                    if col["value"] and col["value"] not in prev_col["value"]:
                        prev_col["value"] = f"{prev_col['value']} | {col['value']}".strip(" |")
            continue

        results.append(this_result)
        if pending_tag_key is not None:
            same_entity_tag_results[pending_tag_key] = this_result

        if category_display_name in ("Employee", "Employee List") and raw_name_value and this_result["is_valid"]:
            emp_name_key = raw_name_value.strip().lower()
            if seen_employee_names is not None:
                if emp_name_key in seen_employee_names:
                    prev_row_num, prev_res = seen_employee_names[emp_name_key]
                    if prev_res is not None and prev_res is not this_result:
                        prev_res["is_valid"] = False
                        prev_res["errors"] = [
                            f"Superseded by updated record (row {i}) with Employee ID '{tag}'"
                        ]
                seen_employee_names[emp_name_key] = (i, this_result)

    return results


def _workstation_status_lookup():
    """Normalized free-text -> WORKSTATION_STATUS_CHOICES key. Kept in one
    place so the bulk importer accepts the same spellings a human would
    reasonably type/paste from a legacy sheet."""
    return {
        "working": "working", "active": "working", "ok": "working", "good": "working",
        "not working": "not_working", "not_working": "not_working", "broken": "not_working",
        "faulty": "not_working",
        "idle": "idle", "in cupboard": "idle", "idle / in cupboard": "idle", "spare": "idle",
        "scrap": "scrap", "destroyed": "scrap", "scrap / destroyed": "scrap",
        "missing": "missing", "lost": "missing",
        "service": "service", "under service": "service", "in service": "service",
    }


def resolve_lookup_value(value, multi, target_category):
    """Resolve a lookup cell (e.g. 'M056, M063') to real asset tags.
    Returns (stored_value, unmatched_tokens): a list of tags when multi, else
    a single tag or ''. Same rules as the migrate_workstation_data command."""
    from .relations import _clean_ref_tokens, resolve_asset_reference
    tokens = _clean_ref_tokens(value) if multi else [str(value).strip()]
    matched, unmatched = [], []
    for tok in tokens:
        found = resolve_asset_reference(tok, target_category or None)
        if found:
            if found.asset_tag not in matched:
                matched.append(found.asset_tag)
        else:
            unmatched.append(tok)
    return (matched if multi else (matched[0] if matched else "")), unmatched


def resolve_pending_workstation_lookups(pending):
    """Resolve lookups that validate_workstation_rows(resolve_lookups=False)
    deferred. Call AFTER the file's other sheets have been saved, so links to
    assets created by the same upload resolve. Returns (extra_updates, unresolved)."""
    updates, unresolved = {}, {}
    for key, spec in (pending or {}).items():
        stored, unmatched = resolve_lookup_value(spec["raw"], spec["multi"], spec["category"])
        updates[key] = stored
        if unmatched:
            unresolved[key] = unmatched
    return updates, unresolved


def validate_workstation_rows(header_row, data_rows, workstation_fields,
                              resolve_lookups=True, reserved_tags=None):
    """Validates rows for the dedicated Workstation bulk-import page.

    Workstation is its own module, not a normal multi-tab Asset Category
    import (see parse_uploaded_workbook_sheets, which deliberately skips a
    sheet tab named 'Workstation'), so this is a separate, single-flat-sheet
    path: one Workstation ID per row, plus whatever Workstation Field
    Builder fields are configured. Column matching is by header TEXT, not
    position:
      - 'Workstation ID' / 'Asset Tag' / 'Tag' -> the unique tag (falls back
        to the sheet's first column if none of those headers are present)
      - 'Status' -> matched against WORKSTATION_STATUS_CHOICES, defaults to
        'working' if blank or unrecognized
      - 'Branch' -> optional per-row branch name; left unset here when
        blank or unmatched, so the caller can fall back to whichever single
        branch was chosen on the upload form
      - 'Notes' -> optional free text
      - every other column is matched (case-insensitively) against an
        active WorkstationField's label and stored in extra_details under
        that field's key, exactly like a normal Asset category's extra
        fields; an unmatched column is kept under its own original header
        text instead of being silently dropped.

    workstation_fields: active WorkstationField rows (options prefetched),
    fetched once by the caller.

    resolve_lookups=False (combined multi-sheet upload): lookup cells are NOT
    resolved now, because the CPU/Monitor/UPS... assets they point to may be
    created by the same upload. The raw values are kept in
    fields["pending_lookups"] and resolved at confirm time with
    resolve_pending_workstation_lookups(). reserved_tags: lower-cased asset
    tags used by the file's other sheets, so a clash is reported in preview.

    Returns a list of row-result dicts shaped like the Asset importer's
    (row_number, errors, is_valid, fields, template_headers, template_row),
    so the same kind of per-column preview table can be reused instead of
    falling back to a generic one.
    """
    from .models import Branch

    norm_headers = [_normalize_header(h) for h in header_row]

    def _find_col(*aliases):
        wanted = {_normalize_header(a) for a in aliases}
        for idx, h in enumerate(norm_headers):
            if h in wanted:
                return idx
        return None

    tag_idx = _find_col("workstation id", "asset tag", "workstation", "tag")
    if tag_idx is None:
        # Previously this fell back to the first column, which let any sheet
        # (e.g. UPS) be imported as workstations. Refuse instead.
        raise ImportFileError(
            "This doesn't look like a Workstation sheet: no 'Workstation ID' "
            "(or 'Asset Tag' / 'Tag') column was found. Upload a file whose "
            "first sheet is the Workstation list, or name the tab 'Workstation'."
        )
    status_idx = _find_col("status", "current status")
    branch_idx = _find_col("branch")
    notes_idx = _find_col("notes", "remark", "remarks")

    label_to_field = {_normalize_header(f.label): f for f in workstation_fields}
    consumed_idx = {i for i in (tag_idx, status_idx, branch_idx, notes_idx) if i is not None}

    status_lookup = _workstation_status_lookup()
    existing_tags = {t.lower() for t in Asset.objects.values_list("asset_tag", flat=True)}
    branches_by_norm = {_normalize_header(b.name): b for b in Branch.objects.filter(status=True)}
    seen_tags_in_file = set()

    template_headers_list = (
        ["Workstation ID", "Status", "Branch", "Notes"] + [f.label for f in workstation_fields]
    )

    def _row_tag(raw_row):
        t = _cell_text(raw_row[tag_idx]) if tag_idx is not None and tag_idx < len(raw_row) else ""
        if not t and raw_row:
            t = _cell_text(raw_row[0])
        return t

    # Same rule as the Employee_List sheet: when a Workstation ID appears more
    # than once in the file, the LAST occurrence is the updated record and the
    # earlier ones are superseded (shown as invalid, not imported).
    last_row_for_tag = {}
    for _i, _r in enumerate(data_rows, start=2):
        if any(_cell_text(c) for c in _r):
            _t = _row_tag(_r)
            if _t:
                last_row_for_tag[_t.lower()] = _i

    results = []
    for i, raw_row in enumerate(data_rows, start=2):  # row 1 is the header
        if not any(_cell_text(c) for c in raw_row):
            continue
        errors = []
        superseded_msg = None

        tag = _row_tag(raw_row)
        if not tag:
            errors.append("Workstation ID is required")
        tag_key = tag.lower()
        if tag and reserved_tags and tag_key in reserved_tags:
            errors.append(f"Asset tag '{tag}' is also used on another sheet in this file")
        elif tag and tag_key in existing_tags:
            errors.append(f"Asset tag '{tag}' already exists")
        if tag and last_row_for_tag.get(tag_key) not in (None, i):
            superseded_msg = (
                f"Superseded by updated record (row {last_row_for_tag[tag_key]}) "
                f"with Workstation ID '{tag}'"
            )

        status_text = _cell_text(raw_row[status_idx]) if status_idx is not None and status_idx < len(raw_row) else ""
        status = status_lookup.get(_normalize_header(status_text), "working") if status_text else "working"

        branch_text = _cell_text(raw_row[branch_idx]) if branch_idx is not None and branch_idx < len(raw_row) else ""
        branch_obj = branches_by_norm.get(_normalize_header(branch_text)) if branch_text else None

        notes = _cell_text(raw_row[notes_idx]) if notes_idx is not None and notes_idx < len(raw_row) else ""

        extra_details = {}
        unresolved = {}
        pending_lookups = {}
        new_options = {}
        for idx, header_text in enumerate(header_row):
            if idx in consumed_idx or not header_text:
                continue
            value = _cell_text(raw_row[idx]) if idx < len(raw_row) else ""
            _nh = _normalize_header(header_text)
            # Accept legacy headers like "CPU Number" / "UPS No." for the fields
            # labelled "CPU" / "UPS" (exact label match still wins).
            f = label_to_field.get(_nh) or label_to_field.get(re.sub(r"\s+(number|no)$", "", _nh))
            if f:
                if value == "" and f.required:
                    errors.append(f"{f.label} is required")
                elif value != "":
                    if f.field_type == "lookup":
                        # Resolve to real assets exactly like the
                        # migrate_workstation_data command, so imported and
                        # migrated workstations link the same way. Tokens
                        # that match nothing go to `unresolved`, not lost.
                        is_multi = getattr(f, "lookup_multi", False)
                        target_cat = getattr(f, "lookup_category", "") or None
                        if resolve_lookups:
                            stored, unmatched = resolve_lookup_value(value, is_multi, target_cat)
                            extra_details[f.key] = stored
                            if unmatched:
                                unresolved[f.key] = unmatched
                        else:
                            pending_lookups[f.key] = {
                                "raw": value, "multi": bool(is_multi), "category": target_cat or "",
                            }
                    elif f.field_type == "select":
                        opts_by_value = {o.value.lower(): o.value for o in f.options.all() if o.is_active}
                        opts_by_label = {o.label.lower(): o.value for o in f.options.all() if o.is_active}
                        val_lower = value.lower()
                        if val_lower in opts_by_value:
                            extra_details[f.key] = opts_by_value[val_lower]
                        elif val_lower in opts_by_label:
                            extra_details[f.key] = opts_by_label[val_lower]
                        else:
                            # New dropdown value (e.g. a new "Purposes"). Don't reject
                            # the row: keep the value and add it to the field's
                            # options when the import is confirmed.
                            extra_details[f.key] = value
                            new_options[f.key] = value
                    elif f.field_type == "number":
                        try:
                            float(value)
                            extra_details[f.key] = value
                        except ValueError:
                            errors.append(f"{f.label} must be a number")
                    else:
                        extra_details[f.key] = value
            elif value != "":
                extra_details[header_text] = value

        if superseded_msg:
            errors = [superseded_msg]

        template_row_display = [
            {"header": "Workstation ID", "value": tag},
            {"header": "Status", "value": status_text or "Working"},
            {"header": "Branch", "value": branch_text},
            {"header": "Notes", "value": notes},
        ]
        for f in workstation_fields:
            raw_val = extra_details.get(f.key, "")
            display_val = ", ".join(raw_val) if isinstance(raw_val, list) else raw_val
            # Lookup cells resolved later (combined upload) or not found in the
            # register would otherwise preview as blank; show what the sheet says.
            if f.key in new_options:
                display_val = f"{display_val} (new option)"
            if f.key in pending_lookups:
                display_val = pending_lookups[f.key]["raw"]
            elif f.key in unresolved:
                nf = ", ".join(unresolved[f.key]) + " (not found)"
                display_val = f"{display_val}, {nf}" if display_val else nf
            template_row_display.append({"header": f.label, "value": display_val})

        results.append({
            "row_number": i,
            "errors": errors,
            "is_valid": not errors,
            "fields": {
                "_kind": "workstation",
                "asset_tag": tag,
                "status": status,
                "notes": notes,
                "branch_id": branch_obj.id if branch_obj else None,
                "extra_details": extra_details,
                "unresolved": unresolved,
                "pending_lookups": pending_lookups,
                "new_options": new_options,
            },
            "template_headers": template_headers_list,
            "template_row": template_row_display,
        })
    return results


def serialize_for_session(results):
    """Turn validated rows' `fields` dicts into JSON-safe data (dates -> isoformat) for storage."""
    serializable = []
    for r in results:
        if not r["is_valid"]:
            continue
        f = dict(r["fields"])
        for key in ("purchase_date", "last_service_date"):
            if isinstance(f.get(key), date):
                f[key] = f[key].isoformat()
        serializable.append(f)
    return serializable


def deserialize_from_session(rows):
    out = []
    for f in rows:
        f = dict(f)
        for key in ("purchase_date", "last_service_date"):
            if f.get(key):
                f[key] = date.fromisoformat(f[key])
            else:
                f[key] = None
        out.append(f)
    return out
