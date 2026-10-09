"""Excel export of assets."""
import re
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from ..category_fields import (
    EMPLOYEE_MAIN_COLUMNS,
    get_category_fields,
    get_workstation_fields,
    is_category_db_configured,
    is_employee_category,
    resolve_extra_value,
    resolve_field_value,
    status_driver_column,
)
from ..display import (
    build_export_cells,
    build_export_columns,
)
from ..excel_security import (
    encrypt_xlsx_bytes,
    export_protection_disabled,
    get_export_password,
)
from ..import_utils import (
    _normalize_header,
)
from ..models import AssetCategory, Workstation, prime_display_tags
from ..permissions import (
    filter_assets_to_branch,
    is_super_admin,
    resolve_selected_branch,
)
from ..sheet_templates import get_template_for_category
from .common import (
    INFO_REGISTER_CATEGORIES,
    WORKSTATION_CATEGORY_NAME,
    order_branch_wise,
)


EXPORT_COLUMNS = [
    ("Asset Tag", "asset_tag"),
    ("Name", "name"),
    ("Brand", "brand"),
    ("Model Number", "model_number"),
    ("Serial Number", "serial_number"),
    ("Status", "get_status_display"),
    ("Current Assigned To", "current_assigned_to"),
    ("Current Location", "current_location"),
    ("Previous Assigned To", "previous_assigned_to"),
    ("Previous Location", "previous_location"),
    ("Linked Workstation", "linked_workstation"),
    ("Purchase Date", "purchase_date"),
    ("Last Service Date", "last_service_date"),
    ("Notes", "notes"),
]


def _extra(asset, key):
    return (asset.extra_details or {}).get(key) or ""


def _ac_capacity(asset):
    # Same fallback order as the Air Conditioner list page.
    return (
        _extra(asset, "capacity_location")
        or _extra(asset, "Status")  # legacy imports kept capacity text here
        or asset.current_location
        or ""
    )


def _ac_serviced(asset):
    return asset.last_service_date or _extra(asset, "Details") or ""


_STANDARD_7 = [
    ("Asset Tag", lambda a: a.asset_tag),
    ("Name", lambda a: a.name),
    ("Brand", lambda a: a.brand),
    ("Serial Number", lambda a: a.serial_number),
    ("Status", lambda a: a.get_status_display()),
    ("Current Location", lambda a: a.current_location),
    ("Notes", lambda a: a.notes),
]

# Categories with NO registered sheet template whose export must match the
# Import Template sheet (assets/sample_template.py) column-for-column, in the
# same order — so a downloaded export can be edited and re-uploaded as-is.
# Used only until an admin configures the category in the Field Builder;
# once it has AssetField rows, export follows those live fields instead.
TEMPLATE_EXPORT_COLUMNS = {
    "air conditioner": [
        ("Asset Tag", lambda a: a.asset_tag),
        ("Name", lambda a: a.name),
        ("Capacity / Location", _ac_capacity),
        ("Status", lambda a: a.get_status_display()),
        ("Serviced", _ac_serviced),
    ],
    "biometric device": [
        ("Asset Tag", lambda a: a.asset_tag),
        ("Name", lambda a: a.name),
        ("Status", lambda a: a.get_status_display()),
        ("Device Type", lambda a: _extra(a, "Device Type")),
        ("Details", lambda a: _extra(a, "Details") or a.current_location),
    ],
    "laptop": _STANDARD_7,
    "networking equipment": _STANDARD_7,
    # Other Asset is a catch-all bucket: the 7 standard columns come first,
    # then any extra columns that really hold data (see export_assets), so
    # nothing imported from the old Others / Inside Cupboard sheets is lost.
    "other asset": _STANDARD_7,
}


def _write_export_sheet(ws, headers, data_rows, header_font, header_fill):
    """Shared sheet-writing/styling logic, used for every category sheet
    AND the dedicated Workstation sheet, so both stay visually identical
    and only ever change in one place."""
    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill

    for row in data_rows:
        ws.append(row)

    for col_idx, header in enumerate(headers, start=1):
        max_len = max(
            [len(str(header))] + [len(str(c.value)) for c in ws[get_column_letter(col_idx)] if c.value]
        )
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 3, 150)

    ws.freeze_panes = "A2"


def _safe_sheet_name(name, used_names):
    """Excel sheet names: max 31 chars, no \\ / ? * [ ] :"""
    clean = re.sub(r'[\\/?*\[\]:]', "-", name).strip()[:31]
    base = clean
    n = 1
    while clean.lower() in used_names:
        suffix = f" ({n})"
        clean = base[: 31 - len(suffix)] + suffix
        n += 1
    used_names.add(clean.lower())
    return clean


@login_required
def export_assets(request):
    # The download is always password-protected. If no password is set up,
    # stop here rather than hand out an open file.
    export_password = get_export_password()
    # Empty password is fine only when the Super Admin removed it on purpose.
    if not export_password and not export_protection_disabled():
        messages.error(
            request,
            "Export is disabled: no Excel password has been set. "
            "The Super Admin can set one under Manage → Export Password.",
        )
        return redirect("assets:dashboard")

    selected_branch, accessible_branches = resolve_selected_branch(request)

    wb = Workbook()
    wb.remove(wb.active)

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="29ABE2", end_color="29ABE2", fill_type="solid")

    used_names = set()
    # Workstation is a separate first-class module (its own model/fields —
    # see Workstation/WorkstationField), not a normal Asset category, so it
    # never goes through this loop — it gets its own dedicated sheet below.
    categories = AssetCategory.objects.exclude(
        name__iexact=WORKSTATION_CATEGORY_NAME
    ).order_by("name")

    for category in categories:
        assets = category.assets.filter(is_active=True)
        assets = filter_assets_to_branch(
            assets, selected_branch, accessible_branches,
            include_unassigned=is_super_admin(request.user) and selected_branch is None,
        )
        # Branch-wise: all of the first branch's records, then the next
        # branch's, and so on (branches in the order they were created —
        # Chennai, Tindivanam, ...); records with no branch last. Inside a
        # branch, by asset tag.
        assets = prime_display_tags(order_branch_wise(assets.select_related("branch")))
        sheet_name = _safe_sheet_name(category.name, used_names)
        ws = wb.create_sheet(title=sheet_name)

        # DB configuration (Category Builder / AssetField) is the source of
        # truth the moment a category has been configured there — same
        # priority rule as get_category_fields(). The legacy sheet_templates
        # header set is only ever used for a category nobody has touched in
        # the builder yet, so a field add/rename/deactivate in the DB is
        # what export reflects, never the frozen original workbook headers.
        template = None if is_category_db_configured(category) else get_template_for_category(category.name)

        # Category Builder categories export EXACTLY what the builder defines,
        # in builder order, through the same column / value rules as the List
        # page (display.build_export_columns / build_export_cells): a Tag
        # column always (import needs it), Name / Status only when the builder
        # has that field, no generic Brand / Model / Serial / Notes columns
        # added. This includes Employee, Hard Disk, Cupboard and Air
        # Conditioner once they are configured in the builder, exactly like
        # their List pages. Only categories with no builder fields yet keep
        # the legacy export below.
        export_columns = build_export_columns(category)

        is_info_reg = category.name.strip().lower() in INFO_REGISTER_CATEGORIES

        cat_key = category.name.strip().lower()

        if export_columns is not None:
            headers = [c["label"] for c in export_columns]
            data_rows = [build_export_cells(asset, export_columns) for asset in assets]
        elif not template and cat_key in TEMPLATE_EXPORT_COLUMNS and not is_category_db_configured(category):
            # Match the Import Template sheet exactly (all its columns are
            # always written, even when empty, like the templated sheets).
            cols = TEMPLATE_EXPORT_COLUMNS[cat_key]
            headers = [h for h, _ in cols]
            asset_list = list(assets)
            data_rows = [
                [("" if (v := fn(a)) is None else v) for _, fn in cols]
                for a in asset_list
            ]
            if cat_key == "other asset":
                skip = {h.lower() for h in headers} | {l.lower() for l, _ in EXPORT_COLUMNS}
                for f in get_category_fields(category):
                    if f["label"].lower() in skip:
                        continue
                    vals = [(a, resolve_field_value(a, f)) for a in asset_list]
                    filled = [(a, v) for a, v in vals if str(v).strip() != ""]
                    if not filled:
                        continue
                    # Legacy copies of the tag / name (e.g. "ID", "Device
                    # Name") add nothing — leave them out.
                    if all(str(v).strip() in (a.asset_tag, a.name) for a, v in filled):
                        continue
                    headers.append(f["label"])
                    for row, (_a, v) in zip(data_rows, vals):
                        row.append(v)
        elif template:
            # This category came from one of the original workbook's sheets
            # — reproduce that sheet's EXACT headers, order, and blank-header
            # column positions, instead of the generic Asset Tag/Name/Status
            # columns. Values come from extra_details (stored under each
            # column's original template key at import time); a blank
            # header still gets its own column so no data/position is lost.
            headers = [c["header"] if c["header"] is not None else "" for c in template]
            data_rows = []
            for asset in assets:
                row = []
                for c in template:
                    key = c["key"]
                    header = c["header"] or ""
                    val = resolve_extra_value(asset.extra_details, {"name": key, "label": header})
                    if val is None or str(val).strip() == "":
                        norm = _normalize_header(header)
                        if norm in ("asset tag", "asset id", "tag", "id", "hard disk number", "keyboard id", "monitor no", "mouse", "ups no", "bluetooth no", "system no", "employee id"):
                            val = asset.asset_tag
                        elif norm in ("item description", "item / description", "name", "asset name", "hard disk name", "system name", "vendor name", "device name", "devices", "projects", "project name", "employeename", "employee name", "description model", "description / model"):
                            val = asset.name
                        elif norm in ("serial no", "serial no.", "serial number", "s.no", "s no", "product key"):
                            val = asset.serial_number
                        elif norm == "status" or (norm and norm == status_driver_column(category)):
                            val = asset.get_status_display()
                        elif norm in ("location storage notes", "location / storage notes", "storage location", "location", "notes"):
                            val = asset.notes or asset.current_location
                        elif norm in ("item type", "type"):
                            val = (asset.extra_details or {}).get("Item Type") or (asset.extra_details or {}).get("item_type") or asset.category.name
                        elif norm in ("capacity specs", "capacity / specs", "size", "capacity"):
                            val = (asset.extra_details or {}).get("Size") or (asset.extra_details or {}).get("Capacity") or (asset.extra_details or {}).get("capacity_size")
                    row.append(val if val is not None else "")
                data_rows.append(row)
            # Trim only genuinely blank-header columns that have no data at
            # all across every row — keep every named column regardless.
            keep_cols = []
            employee_sheet = is_employee_category(category.name)
            for i, c in enumerate(template):
                if employee_sheet and c["col_index"] >= EMPLOYEE_MAIN_COLUMNS:
                    # Employee sheet: only EmployeeName / Employee Id / Status.
                    # The repeated second block is dropped unless it has data.
                    keep_cols.append(any(str(r[i]).strip() != "" for r in data_rows))
                elif c["header"] is not None:
                    keep_cols.append(True)
                else:
                    keep_cols.append(any(str(r[i]).strip() != "" for r in data_rows))
            headers = [h for i, h in enumerate(headers) if keep_cols[i]]
            data_rows = [[v for i, v in enumerate(row) if keep_cols[i]] for row in data_rows]
        else:
            cat_fields = get_category_fields(category)
            all_cat_fields = cat_fields
            # Drop any discovered field whose label collides with one of the
            # fixed mechanical columns below (e.g. the "others" sheet's own
            # "Status" column vs. the mechanical Status field) — otherwise
            # the sheet would get two columns with the same header text.
            mechanical_labels = {label.lower() for label, _ in EXPORT_COLUMNS}
            cat_fields = [f for f in cat_fields if f["label"].lower() not in mechanical_labels]
            extra_headers = [f["label"] for f in cat_fields]

            if is_info_reg:
                # Info registers: only write their custom fields, no hardware columns —
                # except the category's own Status / status-driver column (Employee
                # Status, Project Status, CPU Condition), which is the real status and
                # must not be lost from the export.
                _driver = status_driver_column(category)

                def _is_status_field(f):
                    n = _normalize_header(f["label"])
                    return n == "status" or bool(_driver and n == _driver)

                info_fields = [f for f in all_cat_fields if _is_status_field(f) or f in cat_fields]
                headers = [f["label"] for f in info_fields]
                data_rows = []
                for asset in assets:
                    row = []
                    for f in info_fields:
                        if _is_status_field(f):
                            # keep the original wording (e.g. "Resigned") when it is stored
                            row.append(resolve_extra_value(asset.extra_details, f) or asset.get_status_display())
                        else:
                            row.append(resolve_field_value(asset, f))
                    data_rows.append(row)
            else:
                headers = [label for label, _ in EXPORT_COLUMNS] + extra_headers
                data_rows = []
                for asset in assets:
                    row = []
                    for _, attr in EXPORT_COLUMNS:
                        value = getattr(asset, attr)
                        value = value() if callable(value) else value
                        row.append(value if value is not None else "")
                    for f in cat_fields:
                        row.append(resolve_field_value(asset, f))
                    data_rows.append(row)

                # Determine which columns actually have data; always keep Asset Tag & Name
                keep_cols = [True] * len(headers)
                for c in range(2, len(headers)):
                    keep_cols[c] = any(str(r[c]).strip() != "" for r in data_rows)
                headers = [h for i, h in enumerate(headers) if keep_cols[i]]
                data_rows = [[v for i, v in enumerate(row) if keep_cols[i]] for row in data_rows]

        # Branch column (last) so it's clear which branch each row belongs
        # to when several branches are in one file. Import ignores it (the
        # branch is still chosen on the import screen).
        if "branch" not in {str(h).strip().lower() for h in headers}:
            headers = list(headers) + ["Branch"]
            data_rows = [
                list(row) + [a.branch.name if a.branch else ""]
                for row, a in zip(data_rows, assets)
            ]

        _write_export_sheet(ws, headers, data_rows, header_font, header_fill)

    # --- Workstation: its own dedicated sheet, built entirely from the
    # live Workstation model + active WorkstationField rows (never from
    # sheet_templates / Asset.extra_details) — this is the fix for the
    # sheet being missing from export altogether, since Workstation isn't
    # a normal AssetCategory and was skipped by the loop above.
    ws_category = AssetCategory.objects.filter(name__iexact=WORKSTATION_CATEGORY_NAME).first()
    if ws_category:
        ws_assets = ws_category.assets.filter(is_active=True)
        ws_assets = filter_assets_to_branch(
            ws_assets, selected_branch, accessible_branches,
            include_unassigned=is_super_admin(request.user) and selected_branch is None,
        )
        ws_assets = list(order_branch_wise(ws_assets.select_related("branch")))
        profiles = Workstation.objects.filter(asset__in=ws_assets).select_related("asset")
        profile_by_asset_id = {p.asset_id: p for p in profiles}

        ws_fields = get_workstation_fields()
        headers = ["Workstation ID", "Status"] + [f["label"] for f in ws_fields]
        data_rows = []
        for asset in ws_assets:
            profile = profile_by_asset_id.get(asset.id)
            extra = (profile.extra_details if profile else {}) or {}
            row = [asset.asset_tag, asset.get_status_display()]
            for f in ws_fields:
                val = extra.get(f["name"], "")
                if isinstance(val, list):
                    val = ", ".join(str(v) for v in val)
                row.append(val if val is not None else "")
            data_rows.append(row)

        if "branch" not in {h.strip().lower() for h in headers}:
            headers = headers + ["Branch"]
            data_rows = [
                row + [a.branch.name if a.branch else ""]
                for row, a in zip(data_rows, ws_assets)
            ]

        ws_sheet = wb.create_sheet(title=_safe_sheet_name("Workstation", used_names))
        _write_export_sheet(ws_sheet, headers, data_rows, header_font, header_fill)

    if not wb.sheetnames:
        wb.create_sheet(title="Assets")

    buffer = BytesIO()
    wb.save(buffer)
    # No password (removed by the Super Admin) -> plain .xlsx that opens directly.
    encrypted = (encrypt_xlsx_bytes(buffer.getvalue(), export_password)
                 if export_password else buffer.getvalue())

    response = HttpResponse(
        encrypted,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="Swift_ProSys_Asset_Register.xlsx"'
    return response
