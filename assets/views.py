import re
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView
from django.db import transaction, IntegrityError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .forms import AssetForm, AssetCategoryForm
from .models import Asset, AssetCategory, AssetHistory
from .category_fields import (
    get_category_fields, is_employee_category, EMPLOYEE_MAIN_COLUMNS,
)
from .sheet_templates import get_template_for_category
from .import_utils import (
    IMPORT_COLUMNS, ImportFileError, parse_uploaded_file, validate_rows,
    parse_uploaded_workbook_sheets, validate_workbook_sheets,
    serialize_for_session, deserialize_from_session, _normalize_header,
)

admin_required = user_passes_test(
    lambda u: u.is_staff or u.is_superuser, login_url="assets:dashboard"
)


class BrandedLoginView(LoginView):
    template_name = "assets/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.POST.get("remember_me"):
            # Keep the session alive for 2 weeks
            self.request.session.set_expiry(1209600)
        else:
            # Expire when the browser closes
            self.request.session.set_expiry(0)
        return response


# Fallback icon per category name (Bootstrap Icons class) — only used when a
# category doesn't have its own "icon" set via the Add Category form.
CATEGORY_ICONS = {
    "Air Conditioner": "bi-snow",
    "Biometric Device": "bi-fingerprint",
    "Bluetooth Device": "bi-bluetooth",
    "CPU / System Unit": "bi-cpu",
    "Hard Disk": "bi-device-hdd",
    "Keyboard": "bi-keyboard",
    "Laptop": "bi-laptop",
    "Monitor": "bi-display",
    "Mouse": "bi-mouse2",
    "Networking Equipment": "bi-router",
    "Other Asset": "bi-box-seam",
    "Software / OS License": "bi-file-earmark-lock",
    "UPS": "bi-battery-charging",
    "Workstation": "bi-pc-display-horizontal",
}
DEFAULT_CATEGORY_ICON = "bi-hdd-stack"


@login_required
@admin_required
def clear_all_assets(request):
    if request.method == "POST":
        confirm_text = request.POST.get("confirm_text", "").strip()
        if confirm_text != "DELETE":
            messages.error(request, "Type DELETE exactly to confirm. Nothing was deleted.")
            return redirect("assets:dashboard")
        count = Asset.objects.count()
        Asset.objects.all().delete()
        messages.success(request, f"Cleared all {count} asset record(s).")
        return redirect("assets:dashboard")
    return redirect("assets:dashboard")


# Statuses that genuinely need someone's attention. Active / Idle / Running /
# Open etc. are normal states and must NOT be counted in the red badge.
ATTENTION_STATUSES = ["not_working", "service", "missing"]


@login_required
def dashboard(request):
    categories = (
        AssetCategory.objects.annotate(
            total=Count("assets", filter=Q(assets__is_active=True)),
            not_working=Count(
                "assets",
                filter=Q(assets__is_active=True) & Q(assets__status__in=ATTENTION_STATUSES),
            ),
        ).order_by("name")
    )

    def normalized(name):
        return name.strip().lower().rstrip("s")

    main_categories = []
    grouped_categories = []
    for cat in categories:
        # Known categories always use their mapped icon (avoids bad/blank
        # values sitting in the DB from before this field existed). Only a
        # brand-new custom category falls through to whatever icon it was
        # given in the Add Category form.
        cat.icon_class = CATEGORY_ICONS.get(cat.name) or (cat.icon or "").strip() or DEFAULT_CATEGORY_ICON
        cat.sub_categories = []
        # The "Other Asset(s)" category is always the container itself, even
        # if its own "show under other" box was accidentally ticked — it can
        # never be one of the items grouped inside itself.
        if cat.show_under_other and normalized(cat.name) != "other asset":
            grouped_categories.append(cat)
        else:
            main_categories.append(cat)

    # Attach the grouped categories as a dropdown on the "Other Asset" card
    # instead of showing them under a separate synthetic "Other" card.
    other_asset_card = next(
        (c for c in main_categories if normalized(c.name) == "other asset"), None
    )
    if other_asset_card is not None:
        other_asset_card.sub_categories = grouped_categories
    else:
        # No "Other Asset" category exists yet — fall back to its own card
        # so the grouped categories are still reachable.
        for cat in grouped_categories:
            main_categories.append(cat)

    total_records = Asset.objects.filter(is_active=True).count()
    non_asset_ids = [
        c.id for c in AssetCategory.objects.all()
        if c.name.strip().lower() in NON_ASSET_CATEGORIES
    ]
    real_assets = Asset.objects.filter(is_active=True).exclude(category_id__in=non_asset_ids)
    total_assets = real_assets.count()
    status_breakdown = (
        real_assets
        .values("status")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    recent_changes = AssetHistory.objects.select_related("asset", "changed_by")[:15]

    context = {
        "main_categories": main_categories,
        "total_assets": total_assets,
        "total_records": total_records,
        "status_breakdown": status_breakdown,
        "recent_changes": recent_changes,
    }
    return render(request, "assets/dashboard.html", context)


# Categories that are plain information registers — they don't represent
# physical assets so hardware-specific columns (Status, Brand, Serial No,
# Asset Tag, Current/Previous User/Location) are hidden in the UI and export.
INFO_REGISTER_CATEGORIES = {"it vendor", "incident register", "employee", "employee list"}

# Records that live in the register but are NOT physical assets. They are left
# out of the dashboard's "Total Active Assets" and status cards.
NON_ASSET_CATEGORIES = {"it vendor", "incident register", "employee", "employee list"}

# Employees are people: their form only shows Employee Name / Employee ID /
# Status (no brand, model, serial, location, purchase date ...).
EMPLOYEE_STATUS_CHOICES = [("active", "Active"), ("inactive", "Inactive"), ("other", "Other")]


def _employee_form_setup(form, instance=None):
    """Limit the Status dropdown to people-appropriate values (keeping the
    record's current value so editing never silently changes it)."""
    choices = list(EMPLOYEE_STATUS_CHOICES)
    current = getattr(instance, "status", None)
    if current and current not in dict(choices):
        choices.append((current, dict(Asset.STATUS_CHOICES).get(current, current)))
    form.fields["status"].choices = choices


def _sync_employee_extra_details(asset, extra, old_status=None):
    """Keep the Employee sheet columns (EmployeeName / Employee Id / Status)
    in step with the form's Name / Employee ID / Status, so the table and the
    Excel export show what was just typed."""
    for f in get_category_fields(asset.category):
        label = re.sub(r"\s+", "", f["label"]).lower()
        if label == "employeename":
            extra[f["name"]] = asset.name
        elif label == "employeeid":
            extra[f["name"]] = asset.asset_tag
        elif label == "status":
            # keep original wording (e.g. "Resigned") unless the status changed
            if not extra.get(f["name"]) or old_status != asset.status:
                extra[f["name"]] = asset.get_status_display()
    return extra


@login_required
def category_detail(request, category_id):
    category = get_object_or_404(AssetCategory, pk=category_id)
    assets = category.assets.filter(is_active=True).order_by("asset_tag")
    is_info_register = category.name.strip().lower() in INFO_REGISTER_CATEGORIES
    return render(request, "assets/category_detail.html", {
        "category": category,
        "assets": assets,
        "category_fields": get_category_fields(category),
        "is_info_register": is_info_register,
    })


def _extract_extra_details(request, category_fields):
    """Pull category-specific field values out of POST into a plain dict."""
    extra = {}
    for field in category_fields:
        extra[field["name"]] = request.POST.get(field["name"], "").strip()
    return extra


@login_required
def asset_create(request):
    initial = {}
    category_id = request.GET.get("category") or request.POST.get("category")
    category = None
    if category_id:
        initial["category"] = category_id
        category = AssetCategory.objects.filter(pk=category_id).first()
    category_fields = get_category_fields(category)
    cat_key = category.name.strip().lower() if category else ""
    employee_form = cat_key in {"employee", "employee list"}
    is_info_register = cat_key in INFO_REGISTER_CATEGORIES

    if request.method == "POST":
        form = AssetForm(request.POST)
        if employee_form:
            _employee_form_setup(form)
        if form.is_valid():
            asset = form.save(commit=False)
            asset.updated_by = request.user
            asset.extra_details = _extract_extra_details(request, get_category_fields(asset.category))
            if employee_form:
                asset.extra_details = _sync_employee_extra_details(asset, asset.extra_details)
            asset.save()
            messages.success(request, f"Asset {asset.asset_tag} added.")
            return redirect("assets:category_detail", category_id=asset.category_id)
    else:
        form = AssetForm(initial=initial)
        if employee_form:
            _employee_form_setup(form)
            form.initial.setdefault("status", "active")

    return render(request, "assets/asset_form.html", {
        "form": form, "title": f"Add {category.name}" if category else "Add Asset",
        "category": category, "category_fields": category_fields,
        "employee_form": employee_form, "is_info_register": is_info_register,
    })


@login_required
def asset_update(request, asset_id):
    asset = get_object_or_404(Asset, pk=asset_id)
    category_fields = get_category_fields(asset.category)
    cat_key = asset.category.name.strip().lower()
    employee_form = cat_key in {"employee", "employee list"}
    is_info_register = cat_key in INFO_REGISTER_CATEGORIES
    old_status = asset.status

    if request.method == "POST":
        form = AssetForm(request.POST, instance=asset)
        if employee_form:
            _employee_form_setup(form, instance=Asset.objects.get(pk=asset.pk))
        if form.is_valid():
            updated = form.save(commit=False)
            updated.updated_by = request.user
            category_fields = get_category_fields(updated.category)
            updated.extra_details = _extract_extra_details(request, category_fields)
            if employee_form:
                # keep values of the (hidden) sheet columns that the form doesn't post
                merged = dict(Asset.objects.get(pk=asset.pk).extra_details or {})
                merged.update({k: v for k, v in updated.extra_details.items() if v != ""})
                updated.extra_details = _sync_employee_extra_details(updated, merged, old_status)
            updated.save()
            messages.success(request, f"Asset {updated.asset_tag} updated.")
            return redirect("assets:category_detail", category_id=updated.category_id)
    else:
        form = AssetForm(instance=asset)
        if employee_form:
            _employee_form_setup(form, instance=asset)

    return render(request, "assets/asset_form.html", {
        "form": form, "title": f"Edit Asset — {asset.asset_tag}", "asset": asset,
        "category": asset.category, "category_fields": category_fields,
        "employee_form": employee_form, "is_info_register": is_info_register,
    })


@login_required
def asset_delete(request, asset_id):
    asset = get_object_or_404(Asset, pk=asset_id)
    if request.method == "POST":
        category_id = asset.category_id
        asset.is_active = False
        asset.updated_by = request.user
        asset.save()
        messages.success(request, f"Asset {asset.asset_tag} deactivated.")
        return redirect("assets:category_detail", category_id=category_id)
    return render(request, "assets/asset_confirm_delete.html", {"asset": asset})


@login_required
def category_create(request):
    if request.method == "POST":
        form = AssetCategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Category added.")
            return redirect("assets:dashboard")
    else:
        form = AssetCategoryForm()
    return render(request, "assets/category_form.html", {"form": form, "title": "Add Category"})


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
    wb = Workbook()
    wb.remove(wb.active)

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="29ABE2", end_color="29ABE2", fill_type="solid")

    used_names = set()
    categories = AssetCategory.objects.all().order_by("name")

    for category in categories:
        assets = category.assets.filter(is_active=True).order_by("asset_tag")
        sheet_name = _safe_sheet_name(category.name, used_names)
        ws = wb.create_sheet(title=sheet_name)

        template = get_template_for_category(category.name)
        is_info_reg = category.name.strip().lower() in INFO_REGISTER_CATEGORIES

        if template:
            # This category came from one of the original workbook's sheets
            # — reproduce that sheet's EXACT headers, order, and blank-header
            # column positions, instead of the generic Asset Tag/Name/Status
            # columns. Values come from extra_details (stored under each
            # column's original template key at import time); a blank
            # header still gets its own column so no data/position is lost.
            headers = [c["header"] if c["header"] is not None else "" for c in template]
            data_rows = []
            for asset in assets:
                row = [asset.extra_details.get(c["key"], "") for c in template]
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
            # Drop any discovered field whose label collides with one of the
            # fixed mechanical columns below (e.g. the "others" sheet's own
            # "Status" column vs. the mechanical Status field) — otherwise
            # the sheet would get two columns with the same header text.
            mechanical_labels = {label.lower() for label, _ in EXPORT_COLUMNS}
            cat_fields = [f for f in cat_fields if f["label"].lower() not in mechanical_labels]
            extra_headers = [f["label"] for f in cat_fields]

            if is_info_reg:
                # Info registers: only write their custom fields, no hardware columns
                headers = extra_headers
                data_rows = []
                for asset in assets:
                    row = [asset.extra_details.get(f["name"], "") for f in cat_fields]
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
                        row.append(asset.extra_details.get(f["name"], ""))
                    data_rows.append(row)

                # Determine which columns actually have data; always keep Asset Tag & Name
                keep_cols = [True] * len(headers)
                for c in range(2, len(headers)):
                    keep_cols[c] = any(str(r[c]).strip() != "" for r in data_rows)
                headers = [h for i, h in enumerate(headers) if keep_cols[i]]
                data_rows = [[v for i, v in enumerate(row) if keep_cols[i]] for row in data_rows]

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

    if not wb.sheetnames:
        wb.create_sheet(title="Assets")

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    response = HttpResponse(
        buffer.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="Swift_ProSys_Asset_Register.xlsx"'
    return response


SESSION_IMPORT_KEY = "bulk_import_rows"


@login_required
@admin_required
def bulk_import_sample(request):
    """Generates Asset_Import_Template.xlsx in code — one tab per legacy sheet
    (Employee_List, Workstation, CPU - System Unit, keyboard, Monitor, Mouse,
    UPS, Bluetooth, others, Software and OS, Hard disk, Project Details,
    Project backup, Inside Cupboard, IT Vendor, Incident Register), matching
    the exact column layout admins already know from the old spreadsheets.

    Built from assets.sample_template.build_sample_workbook_bytes() — not read
    from a static file on disk — so the sample lives entirely in the codebase.
    If a sheet's columns ever need to change, edit assets/sample_template.py
    (SHEET_DATA / _fixed_sheets()) directly. Keep each tab passing
    map_headers cleanly: no two columns on the same tab may resolve to the
    same field (e.g. two "Status" columns — rename one, e.g. to "Remarks" or
    "Status Note").
    """
    from assets.sample_template import build_sample_workbook_bytes

    data = build_sample_workbook_bytes()

    response = HttpResponse(
        data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="Asset_Import_Template.xlsx"'
    return response


@login_required
@admin_required
def bulk_import(request):
    """GET: show the upload form. POST with a file: parse + validate and show a
    preview of every row (valid/invalid) before anything is saved."""
    results = None
    summary = None
    file_error = None
    skipped_sheets = None
    rerouted_sheets = None
    sheet_errors = None
    row_warnings = None
    template_notices = None

    if request.method == "POST":
        uploaded_file = request.FILES.get("import_file")
        if not uploaded_file:
            messages.error(request, "Please choose a file to upload.")
        else:
            filename = (uploaded_file.name or "").lower()
            is_multi_sheet = False
            if filename.endswith(".xlsx"):
                # Decide flat-template vs per-sheet-is-a-category mode by
                # looking at the header row, not just the sheet count.
                # A sheet count > 1 always means per-sheet mode (each tab
                # its own category/layout). A SINGLE sheet also goes to
                # per-sheet mode unless its header row already has a
                # "Category" column — that's the signal for the older
                # flat generic template (Category/Asset Tag/Name/...)
                # where every row names its own category. Without that
                # column, a lone tab like "Workstation" is almost always
                # a per-category export (Workstation ID, Employee Name,
                # ...) and should use that tab's own header aliases
                # instead of failing with "Missing required column:
                # Category".
                try:
                    probe_wb = load_workbook(filename=BytesIO(uploaded_file.read()), read_only=True)
                    if len(probe_wb.sheetnames) > 1:
                        is_multi_sheet = True
                    else:
                        probe_ws = probe_wb[probe_wb.sheetnames[0]]
                        probe_header = next(probe_ws.iter_rows(values_only=True), ())
                        has_category_column = any(
                            _normalize_header(cell) == "category" for cell in probe_header
                        )
                        is_multi_sheet = not has_category_column
                except Exception:
                    is_multi_sheet = False
                uploaded_file.seek(0)

            try:
                if is_multi_sheet:
                    sheets, skipped_sheets, rerouted_sheets = parse_uploaded_workbook_sheets(uploaded_file)
                    results, sheet_errors, row_warnings, template_notices = validate_workbook_sheets(sheets)
                else:
                    header_row, data_rows = parse_uploaded_file(uploaded_file)
                    results = validate_rows(header_row, data_rows)
            except ImportFileError as exc:
                # File-level problem (bad/missing headers, empty file, wrong
                # type, etc.) — show it inline with a template download right
                # next to it, instead of only a generic top-of-page message.
                file_error = str(exc)
                results = None

            if results is not None:
                valid_rows = serialize_for_session(results)
                request.session[SESSION_IMPORT_KEY] = valid_rows
                summary = {
                    "total": len(results),
                    "valid": sum(1 for r in results if r["is_valid"]),
                    "invalid": sum(1 for r in results if not r["is_valid"]),
                }

    # Group results by sheet so the preview can render each sheet with its
    # OWN template's columns, in that sheet's own order, instead of one
    # generic Category/Asset Tag/Name/Status table for everything.
    sheet_previews = None
    if results is not None:
        sheet_previews = []
        seen_sheets = {}
        for r in results:
            sheet_name = r.get("sheet") or "Assets"
            if sheet_name not in seen_sheets:
                seen_sheets[sheet_name] = {
                    "sheet_name": sheet_name,
                    "headers": r.get("template_headers"),  # None = no template, use generic columns
                    "rows": [],
                }
                sheet_previews.append(seen_sheets[sheet_name])
            seen_sheets[sheet_name]["rows"].append(r)

    return render(request, "assets/bulk_import.html", {
        "results": results,
        "sheet_previews": sheet_previews,
        "summary": summary,
        "file_error": file_error,
        "skipped_sheets": skipped_sheets,
        "rerouted_sheets": rerouted_sheets,
        "sheet_errors": sheet_errors,
        "row_warnings": row_warnings,
        "template_notices": template_notices,
        "import_columns": [label for label, _f, _r, _k in IMPORT_COLUMNS],
    })


@login_required
@admin_required
def bulk_import_confirm(request):
    if request.method != "POST":
        return redirect("assets:bulk_import")

    rows = request.session.get(SESSION_IMPORT_KEY)
    if not rows:
        messages.error(request, "Nothing to import — please upload a file again.")
        return redirect("assets:bulk_import")

    fields_list = deserialize_from_session(rows)
    created_count = 0
    failed = []

    # Validate every row first (full_clean still runs per-row, so a bad row
    # is still reported individually) but SAVE them all in one batched
    # bulk_create instead of one .save() + one commit per row. With
    # hundreds/thousands of rows, one DB round-trip per row is what made
    # large imports slow — bulk_create sends them in chunks instead.
    assets_to_create = []
    for f in fields_list:
        f = dict(f)
        f["category_id"] = f.pop("category")
        asset = Asset(**f)
        asset.updated_by = request.user
        try:
            # validate_unique=False: tag/category uniqueness was ALREADY
            # checked once, in bulk, during the upload/preview step
            # (import_utils.py's existing_tags + seen_tags_in_file sets).
            # Leaving validate_unique on here would make full_clean() run
            # its own DB query per row just to re-check the same thing —
            # exactly the per-row round-trip we're trying to get rid of.
            # clean_fields()/clean() (format, max-length, choices, etc.)
            # still run normally per row.
            asset.full_clean(exclude=["updated_by", "category"], validate_unique=False)
        except Exception as exc:
            failed.append(f"{f.get('asset_tag', '?')}: {exc}")
            continue
        assets_to_create.append(asset)

    BULK_CREATE_BATCH_SIZE = 500
    created = []
    # Each chunk gets its OWN transaction, instead of one transaction.atomic()
    # around the whole loop. With a single shared transaction, an
    # IntegrityError on any one chunk (e.g. a race on asset_tag uniqueness
    # that slipped past full_clean) rolled back every chunk already saved in
    # this request too — so a single bad row could silently discard hundreds
    # of otherwise-valid ones while the message only mentioned "1 row(s)".
    # Per-chunk transactions mean only the chunk that actually failed is
    # lost; everything before and after it is kept.
    for start in range(0, len(assets_to_create), BULK_CREATE_BATCH_SIZE):
        chunk = assets_to_create[start:start + BULK_CREATE_BATCH_SIZE]
        try:
            with transaction.atomic():
                Asset.objects.bulk_create(chunk, batch_size=BULK_CREATE_BATCH_SIZE)
                # On MySQL (unlike PostgreSQL/SQLite 3.35+/MariaDB),
                # bulk_create() does NOT populate the .pk of the objects it
                # creates — every object in `chunk` comes back with pk=None.
                # Using those pk-less objects as the `asset` FK below raises
                # "bulk_create() prohibited to prevent data loss due to
                # unsaved related object". asset_tag is unique, so re-fetch
                # the rows we just inserted by tag to get their real PKs.
                chunk_created = list(
                    Asset.objects.filter(asset_tag__in=[a.asset_tag for a in chunk])
                )
                # bulk_create() also does NOT fire pre_save/post_save signals,
                # so signals.write_history()'s normal "Asset created" row
                # would silently stop happening for every imported asset.
                # Recreate that one history entry per asset here, in its own
                # single bulk_create, so the audit trail still shows how each
                # asset was added.
                AssetHistory.objects.bulk_create(
                    [
                        AssetHistory(
                            asset=asset,
                            field_name="created",
                            old_value="",
                            new_value="Asset created",
                            changed_by=request.user,
                        )
                        for asset in chunk_created
                    ],
                    batch_size=BULK_CREATE_BATCH_SIZE,
                )
            created.extend(chunk_created)
        except IntegrityError as exc:
            # A DB-level constraint (e.g. a race, or a case-insensitive
            # asset_tag collision) slipped past full_clean — report exactly
            # which rows were in this lost chunk rather than a vague count.
            chunk_tags = ", ".join(a.asset_tag for a in chunk[:5])
            more = f" and {len(chunk) - 5} more" if len(chunk) > 5 else ""
            failed.append(
                f"Batch of {len(chunk)} row(s) starting with tag(s) {chunk_tags}{more} "
                f"could not be saved: {exc}"
            )
    created_count = len(created)

    del request.session[SESSION_IMPORT_KEY]

    if created_count:
        messages.success(request, f"Imported {created_count} asset(s) successfully.")
    if failed:
        messages.error(request, " ".join(failed[:10]))
    if not created_count and not failed:
        messages.error(request, "No rows were imported.")

    return redirect("assets:dashboard")


@login_required
def search_assets(request):
    """Quick search across assets by tag, name, brand, serial number,
    model number, assigned user or location."""
    query = request.GET.get("q", "").strip()
    results = []
    if query:
        results = (
            Asset.objects.filter(is_active=True)
            .filter(
                Q(asset_tag__icontains=query)
                | Q(name__icontains=query)
                | Q(brand__icontains=query)
                | Q(serial_number__icontains=query)
                | Q(model_number__icontains=query)
                | Q(current_assigned_to__icontains=query)
                | Q(current_location__icontains=query)
            )
            .select_related("category")
            .order_by("category__name", "asset_tag")[:100]
        )
    return render(request, "assets/search_results.html", {
        "query": query,
        "results": results,
    })


@login_required
def history_list(request):
    """Full change history ("View all" from the dashboard's Recent Changes),
    newest first, paginated, with optional search / category / field filters."""
    query = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "").strip()
    field = request.GET.get("field", "").strip()

    entries = AssetHistory.objects.select_related("asset", "asset__category", "changed_by")
    if query:
        entries = entries.filter(
            Q(asset__asset_tag__icontains=query) | Q(asset__name__icontains=query)
            | Q(old_value__icontains=query) | Q(new_value__icontains=query)
        )
    if category_id.isdigit():
        entries = entries.filter(asset__category_id=int(category_id))
    if field:
        entries = entries.filter(field_name=field)
    entries = entries.order_by("-changed_at", "-id")

    page = Paginator(entries, 50).get_page(request.GET.get("page"))

    # keep filters when moving between pages
    params = request.GET.copy()
    params.pop("page", None)

    return render(request, "assets/history.html", {
        "page": page,
        "query": query,
        "category_id": category_id,
        "field": field,
        "categories": AssetCategory.objects.order_by("name"),
        "fields": AssetHistory.objects.order_by().values_list("field_name", flat=True).distinct(),
        "querystring": params.urlencode(),
    })
