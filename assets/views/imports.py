"""Excel bulk import (assets and workstations), staging and confirm steps."""
from io import BytesIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from openpyxl import load_workbook

from ..import_utils import (
    IMPORT_COLUMNS,
    ImportFileError,
    _normalize_header,
    deserialize_from_session,
    parse_uploaded_file,
    parse_uploaded_workbook_sheets,
    read_upload_bytes,
    serialize_for_session,
    validate_rows,
    validate_workbook_sheets,
    validate_workstation_rows,
)
from ..models import Asset, AssetHistory, Branch, ImportStaging, WorkstationField
from ..permissions import (
    resolve_selected_branch,
)
from .common import (
    superuser_required,
)
from .workstations import (
    _create_workstation_rows,
)


def _upload_too_large(uploaded_file):
    """Error text if the file exceeds settings.MAX_IMPORT_UPLOAD_MB, else None."""
    limit_mb = getattr(settings, "MAX_IMPORT_UPLOAD_MB", 10)
    if uploaded_file and uploaded_file.size > limit_mb * 1024 * 1024:
        size_mb = uploaded_file.size / (1024 * 1024)
        return f"File is too large ({size_mb:.1f} MB). The maximum allowed size is {limit_mb} MB."
    return None


@login_required
@superuser_required
def workstation_bulk_import(request):
    """Dedicated bulk-import page for the Workstation module.

    Workstation is deliberately excluded from the normal multi-category
    Asset importer (see parse_uploaded_workbook_sheets — a sheet tab
    literally named 'Workstation' is always skipped there, so new
    Workstations couldn't be bulk-created at all until now). This is a
    separate, single-flat-sheet upload targeting the Workstation model
    directly: one row per Workstation, columns matched against whatever
    fields are configured in the Workstation Field Builder.
    """
    results = None
    summary = None
    file_error = None

    if request.method == "POST":
        # Only the branch chosen in the form counts when a file is uploaded.
        branch_id = (request.POST.get("branch") or "").strip()
        import_branch = (Branch.objects.filter(pk=branch_id, status=True).first()
                         if branch_id.isdigit() else None)
    else:
        # Opening the page: pre-select the branch active in the top-bar switcher
        # (this also handles ?branch=<id|all> and remembers it in the session).
        _sel, _ = resolve_selected_branch(request)
        import_branch = _sel if (_sel is not None and _sel.status) else None

    if request.method == "POST":
        uploaded_file = request.FILES.get("import_file")
        if not import_branch:
            messages.error(request, "Please select which branch this import belongs to.")
        elif not uploaded_file:
            messages.error(request, "Please choose a file to upload.")
        elif _upload_too_large(uploaded_file):
            messages.error(request, _upload_too_large(uploaded_file))
        else:
            try:
                header_row, data_rows = parse_uploaded_file(
                    uploaded_file, preferred_sheet="Workstation"
                )
                workstation_fields = list(
                    WorkstationField.objects.filter(is_active=True)
                    .order_by("display_order", "id").prefetch_related("options")
                )
                results = validate_workstation_rows(header_row, data_rows, workstation_fields)
            except ImportFileError as exc:
                file_error = str(exc)
                results = None

            if results is not None:
                # A per-row Branch column (when it matched a real branch)
                # overrides the branch picked above; every other row falls
                # back to it — same convention as the regular Asset
                # importer's single upload-time branch choice.
                for r in results:
                    if r["fields"]["branch_id"] is None:
                        r["fields"]["branch_id"] = import_branch.id

                _stage_put(request, SESSION_WORKSTATION_IMPORT_KEY, serialize_for_session(results))
                request.session[SESSION_WORKSTATION_IMPORT_BRANCH_KEY] = import_branch.id
                summary = {
                    "total": len(results),
                    "valid": sum(1 for r in results if r["is_valid"]),
                    "invalid": sum(1 for r in results if not r["is_valid"]),
                }

    return render(request, "assets/workstation_bulk_import.html", {
        "results": results,
        "summary": summary,
        "file_error": file_error,
        "branches": Branch.objects.filter(status=True).order_by("name"),
        "selected_import_branch_id": import_branch.id if import_branch else None,
    })


@login_required
@superuser_required
def workstation_bulk_import_confirm(request):
    if request.method != "POST":
        return redirect("assets:workstation_bulk_import")

    rows = _stage_get(request, SESSION_WORKSTATION_IMPORT_KEY)
    import_branch_id = request.session.get(SESSION_WORKSTATION_IMPORT_BRANCH_KEY)
    import_branch = Branch.objects.filter(pk=import_branch_id, status=True).first() if import_branch_id else None

    if not rows:
        messages.error(request, "Nothing to import — please upload a file again.")
        return redirect("assets:workstation_bulk_import")
    if not import_branch:
        messages.error(request, "No branch was selected for this import — please upload the file again.")
        return redirect("assets:workstation_bulk_import")

    fields_list = deserialize_from_session(rows)
    created_count, failed = _create_workstation_rows(request, fields_list, import_branch)

    _stage_clear(request, SESSION_WORKSTATION_IMPORT_KEY)
    request.session.pop(SESSION_WORKSTATION_IMPORT_BRANCH_KEY, None)

    if created_count:
        messages.success(request, f"Imported {created_count} workstation(s).")
    if failed:
        shown = "; ".join(failed[:10]) + (" ..." if len(failed) > 10 else "")
        messages.warning(request, f"{len(failed)} row(s) could not be saved: {shown}")
    if not created_count and not failed:
        messages.info(request, "No rows to import.")

    return redirect("assets:workstation_list")






def _stage_put(request, key, rows):
    """Store validated import rows in the DB; only the UUID goes in the session."""
    from datetime import timedelta

    from django.utils import timezone
    ImportStaging.objects.filter(created_at__lt=timezone.now() - timedelta(days=1)).delete()
    ImportStaging.objects.filter(user=request.user, kind=key).delete()
    st = ImportStaging.objects.create(user=request.user, kind=key, payload=rows)
    request.session[key] = str(st.id)


def _stage_get(request, key):
    """Rows staged by THIS user for this import kind, or None."""
    sid = request.session.get(key)
    if not sid:
        return None
    try:
        st = ImportStaging.objects.filter(pk=sid, user=request.user, kind=key).first()
    except (ValueError, TypeError):
        return None
    return st.payload if st else None


def _stage_clear(request, key):
    sid = request.session.pop(key, None)
    if sid:
        try:
            ImportStaging.objects.filter(pk=sid, user=request.user).delete()
        except (ValueError, TypeError):
            pass

SESSION_IMPORT_KEY = "bulk_import_rows"

SESSION_IMPORT_BRANCH_KEY = "bulk_import_branch_id"

# Separate session keys for the dedicated Workstation bulk-import flow below
# — kept independent of the normal Asset importer's so the two can never
# clobber each other if an admin has both tabs open.
SESSION_WORKSTATION_IMPORT_KEY = "workstation_bulk_import_rows"
SESSION_WORKSTATION_IMPORT_BRANCH_KEY = "workstation_bulk_import_branch_id"


@login_required
@superuser_required
def bulk_import_sample(request):
    """Generates Asset_Import_Template.xlsx in code — one tab per current
    AssetCategory (live from the database) plus one Workstation tab, each
    with that category's/module's live active field labels as columns. Add,
    rename, or deactivate a field anywhere in the Field Builder and the next
    download reflects it automatically — see assets.sample_template, which
    builds this straight from AssetField / WorkstationField, not from any
    static header list.
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
@superuser_required
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
        # Only the branch chosen in the form counts when a file is uploaded.
        branch_id = (request.POST.get("branch") or "").strip()
        import_branch = (Branch.objects.filter(pk=branch_id, status=True).first()
                         if branch_id.isdigit() else None)
    else:
        # Opening the page: pre-select the branch active in the top-bar switcher
        # (this also handles ?branch=<id|all> and remembers it in the session).
        _sel, _ = resolve_selected_branch(request)
        import_branch = _sel if (_sel is not None and _sel.status) else None

    if request.method == "POST":
        uploaded_file = request.FILES.get("import_file")
        if not import_branch:
            messages.error(request, "Please select which branch this import belongs to.")
        elif not uploaded_file:
            messages.error(request, "Please choose a file to upload.")
        elif _upload_too_large(uploaded_file):
            messages.error(request, _upload_too_large(uploaded_file))
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
                    probe_wb = load_workbook(filename=BytesIO(read_upload_bytes(uploaded_file)), read_only=True)
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
                    ws_sheet = {}
                    sheets, skipped_sheets, rerouted_sheets = parse_uploaded_workbook_sheets(
                        uploaded_file, workstation_out=ws_sheet
                    )
                    results, sheet_errors, row_warnings, template_notices = validate_workbook_sheets(sheets)
                    if ws_sheet.get("header"):
                        # A tab named "Workstation" is imported in the SAME upload as
                        # the category tabs. Its lookup links (CPU/Monitor/UPS...) are
                        # resolved at confirm time, after those categories are saved.
                        results = list(results)
                        sheet_errors = list(sheet_errors or [])
                        reserved = {
                            str(r["fields"].get("asset_tag", "")).lower()
                            for r in results if r.get("fields")
                        }
                        ws_fields = list(
                            WorkstationField.objects.filter(is_active=True)
                            .order_by("display_order", "id").prefetch_related("options")
                        )
                        try:
                            ws_results = validate_workstation_rows(
                                ws_sheet["header"], ws_sheet["rows"], ws_fields,
                                resolve_lookups=False, reserved_tags=reserved,
                            )
                            for r in ws_results:
                                r["sheet"] = "Workstation"
                            results.extend(ws_results)
                        except ImportFileError as exc:
                            sheet_errors.append(("Workstation", str(exc)))
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
                _stage_put(request, SESSION_IMPORT_KEY, valid_rows)
                request.session[SESSION_IMPORT_BRANCH_KEY] = import_branch.id
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
        "branches": Branch.objects.filter(status=True).order_by("name"),
        "selected_import_branch_id": import_branch.id if import_branch else None,
    })


@login_required
@superuser_required
def bulk_import_confirm(request):
    if request.method != "POST":
        return redirect("assets:bulk_import")

    rows = _stage_get(request, SESSION_IMPORT_KEY)
    import_branch_id = request.session.get(SESSION_IMPORT_BRANCH_KEY)
    import_branch = Branch.objects.filter(pk=import_branch_id, status=True).first() if import_branch_id else None
    if not rows:
        messages.error(request, "Nothing to import — please upload a file again.")
        return redirect("assets:bulk_import")
    if not import_branch:
        messages.error(request, "No branch was selected for this import — please upload the file again and choose a branch.")
        return redirect("assets:bulk_import")

    fields_list = deserialize_from_session(rows)
    # Rows from a "Workstation" tab ride in the same session list, tagged by
    # validate_workstation_rows. They are saved AFTER the asset rows below.
    ws_rows = [f for f in fields_list if f.get("_kind") == "workstation"]
    fields_list = [f for f in fields_list if f.get("_kind") != "workstation"]
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
        f["branch_id"] = import_branch.id
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
            asset.full_clean(exclude=["updated_by", "category", "branch"], validate_unique=False)
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

    # Workstations last, so their CPU/Monitor/Keyboard/Mouse/UPS links can
    # point at assets this same upload just created.
    ws_created = 0
    if ws_rows:
        ws_created, ws_failed = _create_workstation_rows(request, ws_rows, import_branch)
        failed.extend(ws_failed)

    _stage_clear(request, SESSION_IMPORT_KEY)

    if created_count or ws_created:
        parts = []
        if created_count:
            parts.append(f"{created_count} asset(s)")
        if ws_created:
            parts.append(f"{ws_created} workstation(s)")
        messages.success(request, "Imported " + " and ".join(parts) + " successfully.")
    if failed:
        messages.error(request, " ".join(failed[:10]))
    if not created_count and not ws_created and not failed:
        messages.error(request, "No rows were imported.")

    return redirect("assets:dashboard")
