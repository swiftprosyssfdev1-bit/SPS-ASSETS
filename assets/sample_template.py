"""
Sample / Import-Template workbook.

The workbook's SHAPE (which sheets exist, which columns each sheet has, in
what order) is generated live from the database — AssetCategory + active
AssetField rows for normal categories, active WorkstationField rows for the
Workstation module — using the exact same resolvers forms and export use
(assets.category_fields.get_category_fields / get_workstation_fields /
get_common_fields). Add, rename, or deactivate a field anywhere in the
Field Builder and the next download of this template reflects it
immediately, with no code change here.

SAMPLE_ROWS below is NOT the schema. It's optional cosmetic example data
only, kept from the original "system details updated 4.xlsx" workbook for
headers_only=False downloads. An example row is only ever used when its
original header row still matches this category's CURRENT live headers
exactly (case/space-insensitive) — the moment an admin changes that
category's fields, the stale example rows are silently dropped and the
sheet just ships with headers, instead of showing outdated example values.
"""

import re


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


# Optional legacy example rows, keyed by category name (lowercased). First
# row of each list is the ORIGINAL header row they were captured against —
# used only to check the example is still valid for the category's current
# live headers (see _norm comparison in build_sample_workbook below).
SAMPLE_ROWS = {
    'air conditioner': [
        ['Asset Tag', 'Name', 'Capacity / Location', 'Status', 'Serviced'],
        ['A/C001', 'Onida', '1.5 TON, ADMIN ROOM', 'Working', '2020-02-28'],
        ['A/C002', 'Onida', '1.5 TON, MIDDLE LEFT OF OFFICE', 'Working', '2020-02-28'],
    ],
    'biometric device': [
        ['Asset Tag', 'Name', 'Status', 'Device Type', 'Details'],
        ['B001', 'ESSL', 'Working', 'Biometrics', 'BPO Office'],
        ['B002', 'ESSL', 'Working', 'Biometrics', 'BPO Office'],
    ],
    'bluetooth device': [
        ['Bluetooth No', 'Devices', 'Brand', 'Remark', 'User Name'],
        ['BC001', 'Keyboard, Mouse and Bluetooth', 'logitech', 'in house', 'Bhavani'],
        ['BC002', 'Keyboard, Mouse and Bluetooth', 'logitech', 'Bluetooth connector not available', 'in Devaraj house'],
    ],
    'cpu / system unit': [
        [
            'System No', 'System Name', 'Password', 'OS Type', 'Processor',
            'System Type', 'Motherboard Type', 'HardDisk Name', 'HardDisk Serial No.',
            'Graphics Card Serial No.', 'HardDisk Size', 'RAM', 'RAM Model',
            'RAM Size', 'DVD', 'CD', 'Extra', 'Remark', 'Last Service Date',
            'Antivirus', 'Condition', 'Notes', 'others',
        ],
        [
            '002', 'SPSW010002', '<REDACTED>', 'Win 7 Ultimate x64 bit(Pirated)',
            'Intel® Core™ i3 CPU 530 @ 2.93GHz', 'Intel Server Board S3420GPV', None,
            None, None, None, 'Transcend DDR3 2. (2.00 GB)=4GB', None,
            'Display Problem(Idle)', None, None, None, None, None, None, None,
            'working', 'Idle',
        ],
    ],
    'employee': [
        ['EmployeeName', 'Employee Id', 'Status'],
        ['Subulakshmi', '1006', 'Active'],
        ['Mohan Thass Shanmugam', 'SPS002', 'Active'],
    ],
    'hard disk': [
        [
            'Hard disk  Number', 'Hard Disk Name', 'Size', 'Serial No',
            'Conditions', 'Purpose', 'Status', 'Current Status',
        ],
        ['CL001', 'Samsung', '2TB', 'E2F2JJHF305', 'problem with the port frequently disconnecting', None, 'not working & inside the cupboard', 'In Cupboard'],
    ],
    'incident register': [
        ['S.No', 'Incident Type', 'Incident Description', 'Start Date&Time', 'End Date&Time'],
        ['1', 'Natural disaster', 'Storm caused a network outage', '2016-12-12 00:00:00', '2016-12-13 00:00:00'],
    ],
    'it vendor': [
        ['S.No', 'Vendor Name', 'Contact Person', 'Mobile No', 'Types of Service', 'Details 1', 'Details 2'],
        ['1', 'Net4india', '044-2833510', '9840870874 - vijay', 'Leaseline (2mbps)', None, None],
    ],
    'keyboard': [
        ['Keyboard Id', 'Brand', 'Status', 'S.No', 'Location'],
        ['KB001', 'APPLE', 'Working', 'KB-AP-01', 'In Guindy'],
    ],
    'laptop': [
        ['Asset Tag', 'Name', 'Brand', 'Serial Number', 'Status', 'Current Location', 'Notes'],
        ['LT001', 'Dell Latitude 3420', 'Dell', 'DL-88231', 'Working', 'Office Floor', 'Assigned to Dev team'],
    ],
    'monitor': [
        ['Monitor No', 'Brand', 'Type', 'Color', 'Size', 'Model', 'Serial No', 'Condition'],
        ['M001', 'Acer', 'LCD', 'Black', '17 inch', 'AL1516W', '73503405043', 'working'],
    ],
    'mouse': [
        ['Mouse', 'Brand', 'Status', 'S.No', 'Location'],
        ['MS001', 'APPLE', 'Working', 'MS-AP-01', 'Guindy Office'],
    ],
    'networking equipment': [
        ['Asset Tag', 'Name', 'Brand', 'Serial Number', 'Status', 'Current Location', 'Notes'],
        ['NET001', 'Cisco 24 Port Switch', 'Cisco', 'CS-99120', 'Working', 'Server Room', 'Main rack switch'],
    ],
    'other asset': [
        ['Asset Tag', 'Name', 'Brand', 'Serial Number', 'Status', 'Current Location', 'Notes'],
        ['TV001', '55 inch TV', 'TCL', 'TCL-55-01', 'Working', 'Meeting Room', 'With Logitech camera'],
    ],
    'project backup': [
        ['Hard disk Name', 'Projects', 'Project Manager', 'Date', 'Space Free', 'Backup Available HDD'],
        ['EH003', 'Koln', 'Vinoth', '2016-10-01 00:00:00', '159 GB', None],
    ],
    'project details': [
        ['S.No', 'Project Name', 'Status'],
        ['16', 'Alma Books', 'Working'],
    ],
    'software - os license': [
        ['Type', 'Version', 'Product Key', 'System No', 'Details'],
        ['Microsoft OS', 'Pro', '6FYFF-T2BWF-4CQTV-MXMTT-FT4YJ', '5 users', None],
    ],
    'ups': [
        ['UPS No', 'Brand', 'Color', 'Model No', 'Size', 'Last Service Date', 'Condition', 'Details', 'Current Status'],
        ['U001', '3PE', 'Black & Red', 'Sizzle-1000', 'Big Size', '-', 'destroyed', None, None],
    ],
    'workstation': [
        [
            'Workstation ID', 'Status', 'Employee ID', 'Employee Name', 'CPU Number',
            'Monitor Number', 'Keyboard Number', 'Mouse Number', 'UPS No.',
            'Product Id', 'Cd Key', 'Operating System', 'Purposes', 'User Id',
        ],
        ['WS001', 'Working', 'TR1638', 'Nagoor Meeran', '049', 'M056', 'K029', 'R021', 'U0',
         '00426-OEM-8992662-00010', 'MHFPT-8C8M2-V9488-FGM44-2C9T3',
         'Win 7 Ultimate x64 bit (Pirated)', 'System Administrator', 'SPSW070049'],
    ],
}

# Common-field attribute name -> the label shown on a sample sheet for a
# category that has NO configured fields of its own yet (mirrors
# category_fields.DEFAULT_COMMON_FIELDS / get_common_fields).
_COMMON_FIELD_LABELS = {
    "status": "Status",
    "brand": "Brand",
    "model_number": "Model Number",
    "serial_number": "Serial Number",
    "current_assigned_to": "Current Assigned To",
    "current_location": "Current Location",
    "linked_workstation": "Linked Workstation",
    "purchase_date": "Purchase Date",
    "last_service_date": "Last Service Date",
}


def _headers_for_category(category):
    """Live header list for a normal AssetCategory, using the exact same
    priority chain (DB AssetField -> legacy sheet template -> curated ->
    discovered) that forms and export already use."""
    from .category_fields import get_category_fields, get_common_fields

    fields = get_category_fields(category)
    if fields:
        return [f["label"] for f in fields]
    # Brand-new category, nothing configured anywhere yet: same generic
    # common-field set the Add/Edit form would fall back to.
    common = get_common_fields(category)
    return ["Asset Tag", "Name"] + [_COMMON_FIELD_LABELS[a] for a in common if a in _COMMON_FIELD_LABELS] + ["Notes"]


def _headers_for_workstation():
    from .category_fields import get_workstation_fields

    return ["Workstation ID", "Status"] + [f["label"] for f in get_workstation_fields()]


def _matching_example_rows(category_name, live_headers):
    """Returns [example_row, ...] (no header row) only if this category's
    static example was captured against headers identical (case/space
    insensitive, any order-independent set match) to its CURRENT live
    headers — otherwise the example is stale (a field was added/renamed/
    removed since) and must not be shown."""
    example = SAMPLE_ROWS.get(str(category_name or "").strip().lower())
    if not example:
        return []
    example_headers, *example_data_rows = example
    if {_norm(h) for h in example_headers} != {_norm(h) for h in live_headers}:
        return []
    # Reorder each example row to match live_headers' order (example was
    # captured in example_headers' order, which may differ).
    index_in_example = {_norm(h): i for i, h in enumerate(example_headers)}
    out = []
    for row in example_data_rows:
        out.append([
            row[index_in_example[_norm(h)]] if index_in_example[_norm(h)] < len(row) else None
            for h in live_headers
        ])
    return out


def _fill_sheet(ws, headers, data_rows, header_font, header_fill, header_align):
    from openpyxl.utils import get_column_letter

    for row in [headers] + data_rows:
        ws.append(row)

    for col_idx, value in enumerate(headers, start=1):
        if value is None or str(value).strip() == "":
            continue
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_align

        longest = len(str(value))
        for row in data_rows:
            if col_idx <= len(row) and row[col_idx - 1] is not None:
                longest = max(longest, len(str(row[col_idx - 1])))
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(longest + 3, 12), 45)

    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 20


def build_sample_workbook(headers_only=True):
    """Builds the Asset_Import_Template.xlsx workbook entirely from LIVE
    database configuration and returns it as an openpyxl Workbook:

      - One sheet per AssetCategory currently in the database (excluding
        Workstation), with that category's live active AssetField labels
        as columns (or the generic common-field set for a brand-new,
        unconfigured category).
      - One "Workstation" sheet with the live active WorkstationField
        labels as columns.

    No static sheet list or header list drives this — add a category or a
    field anywhere in the app and the very next download includes it, with
    no code change here.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from .models import AssetCategory

    header_fill = PatternFill(start_color="305496", end_color="305496", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    header_align = Alignment(vertical="center", wrap_text=False)

    wb = Workbook()
    wb.remove(wb.active)

    used_names = set()

    def sheet_title(name):
        clean = re.sub(r'[\\/?*\[\]:]', "-", name).strip()[:31]
        base, n = clean, 1
        while clean.lower() in used_names:
            suffix = f" ({n})"
            clean = base[: 31 - len(suffix)] + suffix
            n += 1
        used_names.add(clean.lower())
        return clean

    categories = AssetCategory.objects.exclude(name__iexact="workstation").order_by("name")
    for category in categories:
        headers = _headers_for_category(category)
        if not headers:
            continue
        data_rows = [] if headers_only else _matching_example_rows(category.name, headers)
        ws = wb.create_sheet(sheet_title(category.name))
        _fill_sheet(ws, headers, data_rows, header_font, header_fill, header_align)

    # Workstation — its own sheet, driven by WorkstationField, never by
    # AssetCategory/AssetField (Workstation is not a normal category).
    ws_headers = _headers_for_workstation()
    data_rows = [] if headers_only else _matching_example_rows("workstation", ws_headers)
    ws_sheet = wb.create_sheet(sheet_title("Workstation"))
    _fill_sheet(ws_sheet, ws_headers, data_rows, header_font, header_fill, header_align)

    return wb


def build_sample_workbook_bytes(headers_only=True):
    """Returns the sample template workbook as raw .xlsx bytes, ready to be
    served directly in an HTTP response (no temp file needed)."""
    from io import BytesIO

    wb = build_sample_workbook(headers_only=headers_only)
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()
