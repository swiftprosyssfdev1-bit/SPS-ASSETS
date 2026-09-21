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
from .category_fields import SENSITIVE_FIELD_NAMES, _is_sensitive_field
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
    "workstation": {
        "workstation id": "asset_tag",
        "employee name": "current_assigned_to",
    },
    "keyboard": {"keyboard id": "asset_tag"},
    "monitor": {"monitor no": "asset_tag"},
    "mouse": {"mouse": "asset_tag"},
    "ups": {"ups no": "asset_tag"},
    "bluetooth": {"bluetooth no": "asset_tag"},
    "hard disk": {"hard disk number": "asset_tag"},  # "Hard disk  Number" (double space)
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
        "hard disk name": "asset_tag",
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

DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d %b %Y"]

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


XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE2 compound doc — old .xls format


def _is_xls_bytes(raw_bytes):
    """Returns True if the raw file bytes look like an old-style Excel .xls file."""
    return raw_bytes[:8] == XLS_MAGIC


def _read_xls_bytes(raw_bytes, sheet_index=0):
    """Reads an old .xls file from raw bytes using xlrd.
    Returns (header_row, data_rows) from the specified sheet index.
    """
    import xlrd
    wb = xlrd.open_workbook(file_contents=raw_bytes)
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


def parse_uploaded_file(uploaded_file):
    """Returns (header_row: list[str], data_rows: list[list]) from a
    csv/txt/xlsx/xls file. Old .xls files are handled automatically even if
    they are misnamed as .csv or .xlsx."""
    filename = (uploaded_file.name or "").lower()
    raw_bytes = uploaded_file.read()

    # Detect old .xls by magic bytes regardless of the file extension —
    # many users export from Excel and the file is still .xls inside.
    if _is_xls_bytes(raw_bytes):
        try:
            rows = _read_xls_bytes(raw_bytes, sheet_index=0)
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
    "inside the cupboard": "Inside Cupboard",
    "it_vendor list": "IT Vendor",
    "incident register": "Incident Register",
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
    "inside cupboard": [
        [0, 1, 2, 3],  # Internal Hard disk / Hard Disk Size / Hard Disk S.No / Conditions
        [6, 7, 8, 9],  # Internal HDD / Size / S.No / Conditions
    ],
    "employee list": [
        [0, 1, 2],  # EmployeeName / Employee Id / Status (block 1)
        [3, 4, 5],  # Employee Name / Id / Status (block 2, no spacer column)
    ],
    "employee": [
        [0, 1, 2],  # EmployeeName / Employee Id / Status (block 1)
        [3, 4, 5],  # Employee Name / Id / Status (block 2, no spacer column)
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
EMPLOYEE_ID_PATTERN = re.compile(r"^[A-Za-z]+\d+$")


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


def parse_uploaded_workbook_sheets(uploaded_file):
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
    raw_bytes = uploaded_file.read()

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

        cat = categories_by_norm_name.get(norm_name)
        if not cat:
            alias_target = SHEET_NAME_ALIASES.get(norm_name)
            if alias_target:
                cat = categories_by_norm_name.get(_normalize_header(alias_target))
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
        if sheet_template is not None and sheet_name == base_name_for_template:
            # Only run the header diff for a sheet call that IS the whole
            # sheet (sheet_name has no "(item N)" suffix) — a block-split
            # sheet's block is already a known subset of the template by
            # construction (see SHEET_BLOCK_SPLITS), so diffing a block's
            # few columns against the FULL template would wrongly report
            # the other block's columns as "missing" every time.
            missing, unexpected = _diff_template_headers(base_name_for_template, header_row)
            if missing or unexpected:
                template_notices.append((sheet_name, missing, unexpected))
        tag_fallback_idx = None
        if "asset_tag" not in col_to_field.values():
            tag_fallback_idx = _resolve_tag_fallback_idx(sheet_name, header_row, col_to_field)

        # Collision-tracking name: normally the block's own label (so e.g.
        # "Inside Cupboard (item 1)" and "(item 2)" count as different
        # sheets, and a shared tag between them auto-suffixes as a
        # coincidence). But for a block-split sheet in
        # SHEET_BLOCK_SPLITS_SAME_ENTITY, both blocks describe the same
        # real-world list — so they share one collision name (the sheet's
        # base name, item-suffix stripped) and a repeated tag between them
        # is resolved as an ordinary same-sheet duplicate (later wins).
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
        if sheet_overrides:
            field = sheet_overrides.get(norm)
        if not field:
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
                          existing_fingerprints=None):
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
                if not cat:
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
        if not tag and category_display_name in ("Project Details", "Project Backup"):
            continue

        # Employee_List-specific data-quality fix: some rows have the
        # employee's actual NAME typed into the Employee Id column, with
        # EmployeeName left blank (a data-entry mistake, not a real ID like
        # "SPS002"/"TR1533"). Importing that value as the unique Employee ID
        # would misfile a person's name as their ID and leave Name showing an
        # auto-generated "Employee <name>" placeholder instead. Detect it and
        # swap: use the value as the Name, generate a safe placeholder ID,
        # and record a warning for the admin to review. Never touches a row
        # where BOTH Name and Employee Id are already present.
        raw_name_value = _cell_text(row_map.get("name"))
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
        raw_display["Status"] = status_text or "Working"
        if not status_text:
            fields["status"] = "working"
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

        if sheet_template is not None:
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

        fields["extra_details"] = extra_details

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
