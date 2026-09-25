import re
from io import BytesIO

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.db import transaction, IntegrityError
from django.core.paginator import Paginator
from django.db.models import Count, F, Q, Prefetch
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from django.contrib.auth import update_session_auth_hash

from .forms import (
    AssetForm, AssetCategoryForm, BranchForm, BranchAdminCreateForm, BranchAdminEditForm,
    AccountSettingsForm,
)
from .models import Asset, AssetCategory, AssetHistory, Branch, UserBranchAccess
from .category_fields import (
    get_category_fields, is_employee_category, EMPLOYEE_MAIN_COLUMNS,
    workstation_lookup_category, get_common_fields, status_driver_column,
)
from .sheet_templates import get_template_for_category
from .import_utils import (
    IMPORT_COLUMNS, ImportFileError, parse_uploaded_file, validate_rows,
    parse_uploaded_workbook_sheets, validate_workbook_sheets,
    serialize_for_session, deserialize_from_session, _normalize_header,
    read_upload_bytes,
)
from .excel_security import (
    get_export_password, encrypt_xlsx_bytes, set_export_password, export_password_status,
)
from .permissions import (
    is_super_admin, is_branch_admin, super_admin_required, admin_required,
    get_accessible_branches, can_access_branch, require_branch_access,
    resolve_selected_branch, filter_assets_to_branch,
)

from django.conf import settings

User = get_user_model()

# Backwards-compatible aliases (kept so nothing else in this file has to
# change its decorator name): superuser_required === Super Admin only.
superuser_required = super_admin_required


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
@superuser_required
def clear_all_assets(request):
    if not getattr(settings, "ALLOW_CLEAR_ALL", True):
        messages.error(request, "Clear all records is disabled in this environment.")
        return redirect("assets:dashboard")

    if request.method == "POST":
        confirm_text = request.POST.get("confirm_text", "").strip()
        admin_password = request.POST.get("admin_password", "")

        if confirm_text != "DELETE ALL ASSETS":
            messages.error(request, "Confirmation text did not match 'DELETE ALL ASSETS'. Nothing was deleted.")
            return redirect("assets:dashboard")

        if not request.user.check_password(admin_password):
            messages.error(request, "Incorrect administrator password. Deletion cancelled.")
            return redirect("assets:dashboard")

        with transaction.atomic():
            count = Asset.objects.count()
            Asset.objects.all().delete()
        messages.success(request, f"Cleared all {count} asset record(s) and their audit history.")
        return redirect("assets:dashboard")
    return redirect("assets:dashboard")


# Statuses that genuinely need someone's attention. Active / Idle / Running /
# Open etc. are normal states and must NOT be counted in the red badge.
ATTENTION_STATUSES = ["not_working", "service", "missing"]


# Order of the category cards on the dashboard (normalized names). Categories
# not listed here (e.g. custom ones) go after these, A-Z, but before Other
# Asset, which is always last. Edit this list to reorder the cards.
DASHBOARD_CARD_ORDER = [
    "workstation",
    "cpu / system unit",
    "monitor",
    "keyboard",
    "mouse",
    "ups",
    "laptop",
    "hard disk",
    "software / os license",
    "software - os license",
]


def _dashboard_card_sort_key(cat):
    name = cat.name.strip().lower()
    if name.rstrip("s") == "other asset":
        return (2, 0, "")
    if name in DASHBOARD_CARD_ORDER:
        return (0, DASHBOARD_CARD_ORDER.index(name), "")
    return (1, 0, name)


@login_required
def dashboard(request):
    selected_branch, accessible_branches = resolve_selected_branch(request)
    if not is_super_admin(request.user) and not accessible_branches.exists():
        messages.warning(
            request,
            "Your account has no branches assigned yet. Contact the Super Admin.",
        )

    if selected_branch is not None:
        branch_filter = Q(assets__branch=selected_branch)
    else:
        branch_filter = Q(assets__branch__in=accessible_branches)

    categories = (
        AssetCategory.objects.annotate(
            total=Count("assets", filter=Q(assets__is_active=True) & branch_filter),
            not_working=Count(
                "assets",
                filter=Q(assets__is_active=True) & Q(assets__status__in=ATTENTION_STATUSES) & branch_filter,
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

    main_categories.sort(key=_dashboard_card_sort_key)

    branch_scoped = Asset.objects.filter(is_active=True)
    branch_scoped = filter_assets_to_branch(
        branch_scoped, selected_branch, accessible_branches,
        include_unassigned=is_super_admin(request.user) and selected_branch is None,
    )

    total_records = branch_scoped.count()
    non_asset_ids = [
        c.id for c in AssetCategory.objects.all()
        if c.name.strip().lower() in NON_ASSET_CATEGORIES
    ]
    real_assets = branch_scoped.exclude(category_id__in=non_asset_ids)
    total_assets = real_assets.count()
    status_breakdown = (
        real_assets
        .values("status")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    branch_scoped_all = filter_assets_to_branch(
        Asset.objects.all(), selected_branch, accessible_branches,
        include_unassigned=is_super_admin(request.user) and selected_branch is None,
    )
    recent_changes = (
        AssetHistory.objects
        .select_related("asset", "changed_by", "asset__branch")
        .filter(asset__in=branch_scoped_all)
        # Dashboard is a clean, at-a-glance summary — leave out entries for
        # assets whose tag was auto-suffixed to dodge a collision during
        # import (e.g. "7" and "7-2" from two stacked vendor lists in one
        # sheet). That's real data, not an error, but showing the raw "-2"
        # tag here reads as a glitch. Nothing is hidden from the full
        # history: /history has every row, unfiltered, with full detail.
        .exclude(asset__asset_tag__iregex=r"-\d+$")
        [:15]
    )

    allow_clear_all = bool(getattr(settings, "ALLOW_CLEAR_ALL", True) and request.user.is_superuser)
    context = {
        "main_categories": main_categories,
        "total_assets": total_assets,
        "total_records": total_records,
        "status_breakdown": status_breakdown,
        "recent_changes": recent_changes,
        "allow_clear_all": allow_clear_all,
        "accessible_branches": accessible_branches,
        "selected_branch": selected_branch,
        "is_super_admin": is_super_admin(request.user),
    }
    return render(request, "assets/dashboard.html", context)


# Categories that are plain information registers — they don't represent
# physical assets so hardware-specific columns (Status, Brand, Serial No,
# Asset Tag, Current/Previous User/Location) are hidden in the UI and export.
INFO_REGISTER_CATEGORIES = {
    "it vendor", "incident register", "employee", "employee list", "workstation",
    "cpu / system unit", "software / os license", "project details", "project backup",
}

# Categories whose legacy sheet has no real "Name"-like column at all (just
# a Tag/ID + Brand/Type columns) — Asset.name for these is only ever the
# auto-generated "{Category} {tag}" filler (see import_utils._cell_text /
# the "Name" fallback), so the list table shouldn't show it as if it were
# real data. The Tag + Brand/Model (or the category's own extra fields)
# already say everything there is to say about these rows.
NO_REAL_NAME_CATEGORIES = {
    "keyboard", "monitor", "mouse", "ups", "workstation", "software / os license",
}

# The list table's Tag column reads "Tag" by default. For categories whose
# own sheet had its own ID-column name, show that instead (e.g. Hard Disk's
# "Hard disk Number" column, Keyboard's "Keyboard Id" column) — it's the
# same value either way, just labeled the way that category's own data
# actually calls it.
TAG_LABEL_OVERRIDES = {
    "hard disk": "Hard Disk Number",
    "keyboard": "Keyboard Id",
    "monitor": "Monitor Id",
    "mouse": "Mouse Id",
    "ups": "UPS Id",
}


# Fields that must never be the list-table summary column, even though
# they're not duplicates of Tag/Name — these are secrets/credentials that
# should only be visible on the asset's own View/Edit page, not in the
# shared list table everyone sees at a glance.
LIST_SUMMARY_EXCLUDED_FIELDS = {
    "password", "product key", "cd key", "license key", "va rating",
}

# Explicit "best" column to show in the list table for a category, when the
# first-available field (after skipping Tag/Name duplicates and the names
# above) still wouldn't be the most useful thing to show at a glance.
LIST_SUMMARY_FIELD_OVERRIDES = {
    "cpu / system unit": "Processor",
    "software / os license": "Type",
    "project backup": "Hard disk Name",
}


def _list_summary_field(category, category_fields):
    """Pick one category field to show as the extra list-table column for an
    info-register-style category. Skips fields that just duplicate the Tag
    column (e.g. "Workstation ID", "System No" — these map to asset_tag on
    import via SHEET_HEADER_OVERRIDES, so showing them again next to Tag is
    redundant) and skips anything sensitive (Password, keys, etc.) — those
    stay hidden until someone opens View/Edit on that specific record."""
    from .import_utils import SHEET_HEADER_OVERRIDES, HEADER_ALIASES, _normalize_header
    from .sheet_templates import CATEGORY_TO_TEMPLATE

    cat_norm = category.name.strip().lower()
    template_key = CATEGORY_TO_TEMPLATE.get(cat_norm)
    overrides = SHEET_HEADER_OVERRIDES.get(template_key, {})

    override_label = LIST_SUMMARY_FIELD_OVERRIDES.get(cat_norm)
    if override_label:
        for f in category_fields:
            if f["label"] == override_label:
                return f

    def duplicates_tag_or_name(field):
        norm = _normalize_header(field["label"])
        mapped = overrides.get(norm) or HEADER_ALIASES.get(norm)
        return mapped in ("asset_tag", "name")

    def is_sensitive_for_list(field):
        return _normalize_header(field["label"]) in LIST_SUMMARY_EXCLUDED_FIELDS

    for f in category_fields:
        if not duplicates_tag_or_name(f) and not is_sensitive_for_list(f):
            return f
    return None

# Records that live in the register but are NOT physical assets. They are left
# out of the dashboard's "Total Active Assets" and status cards.
NON_ASSET_CATEGORIES = {"it vendor", "incident register", "employee", "employee list"}

# Status values that make sense for each category. The Status dropdown on the
# Add/Edit form only lists these (and the form JS swaps the list when the
# Category dropdown changes). Any category not listed here is a physical
# hardware asset and gets HARDWARE_STATUS_VALUES.
HARDWARE_STATUS_VALUES = ["working", "not_working", "idle", "scrap", "missing", "service"]
STATUS_VALUES_BY_CATEGORY = {
    # Employees are people, projects are work, incidents are tickets —
    # none of them are "Working / Not Working" hardware.
    "employee": ["active", "inactive", "other"],
    "employee list": ["active", "inactive", "other"],
    "project details": ["active", "completed", "stopped"],
    "incident register": ["open", "resolved"],
    "air conditioner": ["working", "not_working", "service", "scrap"],
}


def _status_choices_for(category_name):
    """[(value, label), ...] of the statuses allowed for a category."""
    key = (category_name or "").strip().lower()
    labels = dict(Asset.STATUS_CHOICES)
    values = STATUS_VALUES_BY_CATEGORY.get(key, HARDWARE_STATUS_VALUES)
    return [(v, labels[v]) for v in values]


def _status_form_setup(form, category=None, instance=None):
    """Limit the Status dropdown to the statuses that suit this category.

    `instance` (edit only) is the record as currently saved: if its status is
    a legacy value outside the allowed list (e.g. "other" from an old import)
    it is kept as an extra option so editing never silently changes it —
    except Project Details records left on the old "working"/"running"
    import default, which just show as Active.
    """
    cat_name = getattr(category, "name", category) or ""
    key = cat_name.strip().lower()
    choices = _status_choices_for(cat_name)
    allowed = dict(choices)
    current = getattr(instance, "status", None)
    same_category = instance is not None and getattr(category, "pk", None) == instance.category_id

    if key == "project details" and current in ("working", "running"):
        form.initial["status"] = "active"
    elif current and same_category and current not in allowed:
        choices.append((current, dict(Asset.STATUS_CHOICES).get(current, current)))
    form.fields["status"].choices = choices
    if instance is None:
        form.initial.setdefault("status", choices[0][0])


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


def _sync_status_column(asset, extra, old_status=None):
    """Keep the sheet's own Status/Condition column (the one the Status
    dropdown replaces on the form) in step with the real status, so the
    detail page and Excel export match. The original wording (e.g. "working
    but no stand") is kept unless the status was actually changed."""
    driver = status_driver_column(asset.category)
    if not driver:
        return extra
    for f in get_category_fields(asset.category):
        if _normalize_header(f["label"]) == driver:
            if not extra.get(f["name"]) or old_status != asset.status:
                extra[f["name"]] = asset.get_status_display()
    return extra


def _sync_project_details(asset, extra):
    """Project Details: the project name is the asset tag, so Name (used in
    search / history / titles) and the sheet's "Project Name" column follow
    it instead of the auto-filler "Project Details <tag>"."""
    if (asset.category.name or "").strip().lower() != "project details":
        return extra
    asset.name = asset.asset_tag
    for f in get_category_fields(asset.category):
        if _normalize_header(f["label"]) == "project name":
            extra[f["name"]] = asset.asset_tag
    return extra


def order_branch_wise(qs):
    """Records grouped branch by branch — all of the first branch's, then the
    next branch's, and so on (branches in the order they were created:
    Chennai, Tindivanam, ...) — with no-branch records last, and by asset tag
    inside each branch. Used by the category list pages and the Excel export,
    so both show the same order. With a single branch selected it's just
    tag order."""
    return qs.order_by(F("branch_id").asc(nulls_last=True), "asset_tag")


@login_required
def category_detail(request, category_id):
    category = get_object_or_404(AssetCategory, pk=category_id)
    selected_branch, accessible_branches = resolve_selected_branch(request)

    assets = category.assets.filter(is_active=True).select_related("branch")
    assets = filter_assets_to_branch(
        assets, selected_branch, accessible_branches,
        include_unassigned=is_super_admin(request.user) and selected_branch is None,
    )
    assets = order_branch_wise(assets)

    cat_norm = category.name.strip().lower()
    is_info_register = cat_norm in INFO_REGISTER_CATEGORIES
    is_employee = cat_norm in ("employee", "employee list")
    is_incident = cat_norm == "incident register"
    is_cupboard = cat_norm in ("inside cupboard", "inside the cupboard")
    is_project_details = cat_norm == "project details"
    is_hard_disk = cat_norm == "hard disk"
    is_ac = cat_norm == "air conditioner"
    hide_name = cat_norm in NO_REAL_NAME_CATEGORIES
    tag_label = TAG_LABEL_OVERRIDES.get(cat_norm, "Tag")
    category_fields = get_category_fields(category)
    list_summary_field = _list_summary_field(category, category_fields) if (is_info_register and not is_employee and not is_incident and not is_project_details) else None
    return render(request, "assets/category_detail.html", {
        "category": category,
        "assets": assets,
        "category_fields": category_fields,
        "list_summary_field": list_summary_field,
        "is_info_register": is_info_register,
        "is_employee": is_employee,
        "is_incident": is_incident,
        "is_cupboard": is_cupboard,
        "is_project_details": is_project_details,
        "is_hard_disk": is_hard_disk,
        "is_ac": is_ac,
        "hide_name": hide_name,
        "tag_label": tag_label,
        "is_vendor": cat_norm == "it vendor",
        "accessible_branches": accessible_branches,
        "selected_branch": selected_branch,
        "is_super_admin": is_super_admin(request.user),
        "show_branch_column": selected_branch is None,
    })


def _extract_extra_details(request, category, existing_extra=None):
    """Pull category-specific extra field values out of POST into a clean dict."""
    config = _build_category_form_config().get(str(getattr(category, "pk", "")))
    if not config:
        return {}
    extra_fields = config.get("extra_fields", [])
    extra = dict(existing_extra or {}) if existing_extra else {}
    for field in extra_fields:
        name = field["name"]
        if name in request.POST:
            val = request.POST.get(name, "").strip()
            if val != "":
                extra[name] = val
            elif name in extra:
                extra.pop(name, None)
    return extra


def _build_category_form_config():
    """Per-category config for the Add/Edit Asset form's JavaScript, so
    switching the Category dropdown updates which fields show instantly
    with no page reload: which common fields apply, custom labels, and that category's
    own extra fields (with type/options/workstation-lookup info)."""
    from .import_utils import SHEET_HEADER_OVERRIDES, HEADER_ALIASES, LABEL_TO_FIELD, _normalize_header
    from .sheet_templates import CATEGORY_TO_TEMPLATE
    from .category_fields import get_category_field_labels, get_common_fields, get_category_fields, workstation_lookup_category

    config = {}
    for cat in AssetCategory.objects.all():
        cat_norm = cat.name.strip().lower()
        employee_form = cat_norm in {"employee", "employee list"}
        lbl_cfg = get_category_field_labels(cat.name)
        common_fields = get_common_fields(cat.name)
        show_name = lbl_cfg.get("show_name", True)

        template_key = CATEGORY_TO_TEMPLATE.get(cat_norm)
        overrides = SHEET_HEADER_OVERRIDES.get(template_key, {})

        driver = status_driver_column(cat)
        status_label = "Status"
        if driver:
            for f in get_category_fields(cat):
                if _normalize_header(f["label"]) == driver:
                    status_label = f["label"]
        extra_fields = []
        if cat_norm not in ("employee", "employee list", "project details", "other asset", "laptop", "networking equipment"):
            for f in get_category_fields(cat):
                label = f["label"]
                norm = _normalize_header(label)
                mapped = overrides.get(norm) if overrides else None
                if mapped is None:
                    mapped = LABEL_TO_FIELD.get(norm) or HEADER_ALIASES.get(norm)

                # Exclude if handled by core or common model fields:
                if mapped in ("asset_tag", "name", "notes", "is_active", "category", "branch"):
                    continue
                if norm in ("asset tag", "name", "notes", "active", "is active", "category", "branch"):
                    continue
                if mapped in common_fields and not (driver and mapped == "status"):
                    continue
                if driver:
                    # this column is replaced by the real Status dropdown
                    if norm == driver:
                        continue
                elif norm in ("status", "current status") and "status" in common_fields:
                    continue
                if norm in ("brand",) and "brand" in common_fields:
                    continue
                if norm in ("model", "model no", "model number") and "model_number" in common_fields:
                    continue
                if norm in ("serial no", "serial no.", "serial number", "s.no", "sno") and "serial_number" in common_fields:
                    continue
                if norm in ("location", "current location") and "current_location" in common_fields:
                    continue
                if norm in ("user name", "employee name") and "current_assigned_to" in common_fields:
                    continue
                if norm in ("s.no", "sno"):
                    continue
                if cat_norm in ("inside cupboard", "inside the cupboard") and norm in ("item / description", "asset tag", "status", "location / storage notes"):
                    continue
                if cat_norm == "hard disk" and norm in ("hard disk name", "hard disk  number", "hard disk number"):
                    continue

                entry = {"name": f["name"], "label": f["label"], "type": f["type"]}
                if f.get("options"):
                    entry["options"] = f["options"]
                lookup = workstation_lookup_category(f["label"])
                if lookup:
                    entry["lookup_category"] = lookup
                extra_fields.append(entry)

        status_choices = _status_choices_for(cat.name)
        config[str(cat.pk)] = {
            "name": cat.name,
            "status_choices": status_choices,
            "status_default": status_choices[0][0],
            "status_label": status_label,
            "common_fields": common_fields,
            "extra_fields": extra_fields,
            "employee_form": employee_form,
            "show_name": show_name,
            "tag_label": lbl_cfg.get("tag_label", "Asset Tag"),
            "name_label": lbl_cfg.get("name_label", "Name"),
            "serial_label": lbl_cfg.get("serial_label", "Serial Number"),
            "location_label": lbl_cfg.get("location_label", "Current Location"),
            "assigned_label": lbl_cfg.get("assigned_label", "Current Assigned To"),
            "model_label": lbl_cfg.get("model_label", "Model Number"),
            "asset_tag_help": "" if employee_form else "Unique ID",
        }
    return config


@login_required
def asset_create(request):
    accessible_branches = get_accessible_branches(request.user)
    if not accessible_branches.exists():
        messages.error(request, "You don't have any branches assigned yet — contact the Super Admin.")
        return redirect("assets:dashboard")

    initial = {}
    category_id = request.GET.get("category") or request.POST.get("category")
    category = None
    if category_id:
        initial["category"] = category_id
        category = AssetCategory.objects.filter(pk=category_id).first()
    selected_branch, _ = resolve_selected_branch(request)
    if selected_branch is not None:
        initial["branch"] = selected_branch.pk
    elif accessible_branches.count() == 1:
        initial["branch"] = accessible_branches.first().pk
    category_fields = get_category_fields(category)
    cat_key = category.name.strip().lower() if category else ""
    employee_form = cat_key in {"employee", "employee list"}
    project_form = cat_key == "project details"
    is_info_register = cat_key in INFO_REGISTER_CATEGORIES

    if request.method == "POST":
        form = AssetForm(request.POST, accessible_branches=accessible_branches, user=request.user)
        _status_form_setup(form, category)
        if form.is_valid():
            asset = form.save(commit=False)
            asset.updated_by = request.user
            if not asset.name or not asset.name.strip():
                if asset.brand:
                    asset.name = f"{asset.brand} {asset.model_number}".strip() or asset.brand
                else:
                    asset.name = f"{asset.category.name} {asset.asset_tag}"
            asset.extra_details = _extract_extra_details(request, asset.category)
            if employee_form:
                asset.extra_details = _sync_employee_extra_details(asset, asset.extra_details)
            else:
                asset.extra_details = _sync_status_column(asset, asset.extra_details)
            asset.extra_details = _sync_project_details(asset, asset.extra_details)
            asset.save()
            messages.success(request, f"Asset {asset.asset_tag} added.")
            return redirect("assets:category_detail", category_id=asset.category_id)
    else:
        form = AssetForm(initial=initial, accessible_branches=accessible_branches, user=request.user)
        _status_form_setup(form, category)

    return render(request, "assets/asset_form.html", {
        "form": form, "title": f"Add {category.name}" if category else "Add Asset",
        "category": category, "category_fields": category_fields,
        "employee_form": employee_form, "is_info_register": is_info_register,
        "workstation_lookup_category": workstation_lookup_category,
        "is_workstation": cat_key == "workstation",
        "category_form_config": _build_category_form_config(),
        "asset_extra_details": {},
    })


@login_required
def asset_update(request, asset_id):
    asset = get_object_or_404(Asset, pk=asset_id)
    require_branch_access(request.user, asset.branch)
    accessible_branches = get_accessible_branches(request.user)

    category_fields = get_category_fields(asset.category)
    cat_key = asset.category.name.strip().lower()
    employee_form = cat_key in {"employee", "employee list"}
    project_form = cat_key == "project details"
    is_info_register = cat_key in INFO_REGISTER_CATEGORIES
    old_status = asset.status

    if request.method == "POST":
        form = AssetForm(request.POST, instance=asset, accessible_branches=accessible_branches, user=request.user)
        posted_category = AssetCategory.objects.filter(pk=request.POST.get("category") or None).first()
        _status_form_setup(form, posted_category or asset.category, instance=Asset.objects.get(pk=asset.pk))
        if form.is_valid():
            updated = form.save(commit=False)
            require_branch_access(request.user, updated.branch)
            updated.updated_by = request.user
            if not updated.name or not updated.name.strip():
                if updated.brand:
                    updated.name = f"{updated.brand} {updated.model_number}".strip() or updated.brand
                else:
                    updated.name = f"{updated.category.name} {updated.asset_tag}"
            category_fields = get_category_fields(updated.category)
            updated.extra_details = _extract_extra_details(request, updated.category, existing_extra=asset.extra_details)
            if employee_form:
                # keep values of the (hidden) sheet columns that the form doesn't post
                merged = dict(Asset.objects.get(pk=asset.pk).extra_details or {})
                merged.update({k: v for k, v in updated.extra_details.items() if v != ""})
                updated.extra_details = _sync_employee_extra_details(updated, merged, old_status)
            else:
                updated.extra_details = _sync_status_column(updated, updated.extra_details, old_status)
            updated.extra_details = _sync_project_details(updated, updated.extra_details)
            updated.save()
            messages.success(request, f"Asset {updated.asset_tag} updated.")
            return redirect("assets:category_detail", category_id=updated.category_id)
    else:
        form = AssetForm(instance=asset, accessible_branches=accessible_branches, user=request.user)
        _status_form_setup(form, asset.category, instance=asset)

    return render(request, "assets/asset_form.html", {
        "form": form, "title": f"Edit Asset — {asset.asset_tag}", "asset": asset,
        "category": asset.category, "category_fields": category_fields,
        "employee_form": employee_form, "is_info_register": is_info_register,
        "workstation_lookup_category": workstation_lookup_category,
        "is_workstation": cat_key == "workstation",
        "category_form_config": _build_category_form_config(),
        "asset_extra_details": asset.extra_details or {},
    })


def _resolve_back_link(request, default_url, default_label):
    """Where the View page's "Back" button should go.

    Cross-links (e.g. a Workstation's Employee ID badge) add ?back=<the page
    the person was on>, so Back returns there instead of always jumping to
    the asset's own category. Only same-site paths that resolve to a known
    page are accepted (no open redirect); anything else falls back to the
    default (the asset's category page).
    """
    from urllib.parse import urlparse
    from django.urls import resolve, Resolver404
    from django.utils.http import url_has_allowed_host_and_scheme

    back = (request.GET.get("back") or "").strip()
    if not back or not back.startswith("/") or back.startswith("//"):
        return default_url, default_label
    if not url_has_allowed_host_and_scheme(
        back, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return default_url, default_label
    try:
        match = resolve(urlparse(back).path)
    except Resolver404:
        return default_url, default_label

    name = match.url_name
    if name == "category_detail":
        cat = AssetCategory.objects.filter(pk=match.kwargs.get("category_id")).first()
        if cat:
            return back, f"Back to {cat.name}"
    elif name == "asset_detail":
        prev = Asset.objects.filter(pk=match.kwargs.get("asset_id")).first()
        if prev and can_access_branch(request.user, prev.branch):
            return back, f"Back to {prev.asset_tag}"
    elif name == "dashboard":
        return back, "Back to Dashboard"
    elif name == "search_assets":
        return back, "Back to Search"
    return default_url, default_label


@login_required
def asset_detail(request, asset_id):
    """Read-only 'View' page — the complete record, organized into
    sections, per the 'don't overload the list table' requirement."""
    asset = get_object_or_404(Asset, pk=asset_id)
    require_branch_access(request.user, asset.branch)
    cat_norm = asset.category.name.strip().lower()
    if cat_norm == "air conditioner":
        # New/edited rows store this cleanly under "capacity_location"
        # (see category_fields.CATEGORY_FIELDS). Rows still on their
        # original import keep the raw "Status"/"Details" columns mirrored
        # from the old "Others" sheet template — fall back to those so
        # nothing already in the database looks blank.
        capacity_location = asset.extra_details.get("capacity_location") or asset.extra_details.get("Status", "")
        serviced = asset.last_service_date or asset.extra_details.get("Details", "")
        detail_fields = [
            {"label": "Capacity / Location", "value": capacity_location},
            {"label": "Serviced", "value": serviced},
        ]
    else:
        category_fields = get_category_fields(asset.category)
        driver = status_driver_column(asset.category)
        detail_fields = [
            {
                "label": f["label"],
                # the Status/Condition column shows the real status, not the
                # (possibly empty / stale) imported text copy
                "value": asset.get_status_display() if driver and _normalize_header(f["label"]) == driver
                         else asset.extra_details.get(f["name"], ""),
            }
            for f in category_fields
        ]
    is_info_register = cat_norm in INFO_REGISTER_CATEGORIES
    
    from .relations import get_forward_relationships, get_reverse_relationships
    forward_rels = get_forward_relationships(asset)
    reverse_rels = get_reverse_relationships(asset)

    back_url, back_label = _resolve_back_link(
        request,
        reverse("assets:category_detail", args=[asset.category_id]),
        f"Back to {asset.category.name}",
    )

    return render(request, "assets/asset_detail.html", {
        "asset": asset,
        "back_url": back_url,
        "back_label": back_label,
        "detail_fields": detail_fields,
        "is_info_register": is_info_register,
        "forward_relationships": forward_rels,
        "reverse_relationships": reverse_rels,
    })


@login_required
def asset_delete(request, asset_id):
    asset = get_object_or_404(Asset, pk=asset_id)
    require_branch_access(request.user, asset.branch)
    if request.method == "POST":
        category_id = asset.category_id
        asset.is_active = False
        asset.updated_by = request.user
        asset.save()
        messages.success(request, f"Asset {asset.asset_tag} deactivated.")
        return redirect("assets:category_detail", category_id=category_id)
    return render(request, "assets/asset_confirm_delete.html", {"asset": asset})


@login_required
def asset_lookup(request):
    """JSON endpoint powering the Workstation page's searchable combo
    boxes (requirement 8). Returns existing assets of the requested
    category whose tag/name/serial matches `q`, restricted to branches the
    current user may see (requirement 9/11 — enforced here, not just by
    hiding options in the UI)."""
    category_name = request.GET.get("category", "").strip()
    query = request.GET.get("q", "").strip()
    branch_id = request.GET.get("branch", "").strip()

    accessible_branches = get_accessible_branches(request.user)
    if branch_id.isdigit() and accessible_branches.filter(pk=int(branch_id)).exists():
        branches = accessible_branches.filter(pk=int(branch_id))
    else:
        branches = accessible_branches

    if not category_name or not branches.exists():
        return JsonResponse({"results": []})

    if category_name.strip().lower() == "employee":
        category_q = Q(category__name__in=["Employee", "Employee List"])
    else:
        category_q = Q(category__name__iexact=category_name)

    qs = (
        Asset.objects.filter(is_active=True, branch__in=branches)
        .filter(category_q)
    )
    if query:
        qs = qs.filter(
            Q(asset_tag__icontains=query) | Q(name__icontains=query)
            | Q(serial_number__icontains=query)
        )
    qs = qs.select_related("category")[:15]

    results = [
        {
            "tag": a.asset_tag,
            "label": f"{a.asset_tag} — {a.name}" + (f" — SN {a.serial_number}" if a.serial_number else ""),
        }
        for a in qs
    ]
    return JsonResponse({"results": results})


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


# ─────────────────────────────────────────────────────────────────────────
# Branch management (Super Admin only)
# ─────────────────────────────────────────────────────────────────────────

@login_required
@superuser_required
def branch_list(request):
    branches = Branch.objects.annotate(
        asset_count=Count("assets", filter=Q(assets__is_active=True)),
        admin_count=Count("admins", distinct=True),
    ).order_by("code", "name")
    return render(request, "assets/branch_list.html", {"branches": branches})


@login_required
@superuser_required
def branch_create(request):
    if request.method == "POST":
        form = BranchForm(request.POST)
        if form.is_valid():
            branch = form.save()
            messages.success(request, f"Branch '{branch.name}' added.")
            return redirect("assets:branch_list")
    else:
        form = BranchForm()
    return render(request, "assets/branch_form.html", {"form": form, "title": "Add Branch"})


@login_required
@superuser_required
def branch_update(request, branch_id):
    branch = get_object_or_404(Branch, pk=branch_id)
    if request.method == "POST":
        form = BranchForm(request.POST, instance=branch)
        if form.is_valid():
            form.save()
            messages.success(request, f"Branch '{branch.name}' updated.")
            return redirect("assets:branch_list")
    else:
        form = BranchForm(instance=branch)
    return render(request, "assets/branch_form.html", {
        "form": form, "title": f"Edit Branch — {branch.name}", "branch": branch,
    })


# ─────────────────────────────────────────────────────────────────────────
# Branch Admin management (Super Admin only)
# ─────────────────────────────────────────────────────────────────────────

@login_required
@superuser_required
def admin_list(request):
    admins = (
        User.objects.filter(is_superuser=False, is_staff=True)
        .select_related("branch_access")
        .prefetch_related("branch_access__branches")
        .order_by("username")
    )
    return render(request, "assets/admin_list.html", {"admins": admins})


@login_required
@superuser_required
def admin_create(request):
    if request.method == "POST":
        form = BranchAdminCreateForm(request.POST)
        if form.is_valid():
            user = User.objects.create_user(
                username=form.cleaned_data["username"],
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
                is_staff=True,
                is_active=form.cleaned_data["is_active"],
            )
            profile = UserBranchAccess.objects.create(user=user)
            profile.branches.set(form.cleaned_data["branches"])
            messages.success(request, f"Branch Admin '{user.username}' created.")
            return redirect("assets:admin_list")
    else:
        form = BranchAdminCreateForm()
    return render(request, "assets/admin_form.html", {"form": form, "title": "Add Branch Admin"})


@login_required
@superuser_required
def admin_update(request, user_id):
    admin_user = get_object_or_404(User, pk=user_id, is_superuser=False)
    profile, _created = UserBranchAccess.objects.get_or_create(user=admin_user)

    if request.method == "POST":
        form = BranchAdminEditForm(request.POST, admin_user=admin_user)
        if form.is_valid():
            admin_user.username = form.cleaned_data["username"]
            admin_user.email = form.cleaned_data["email"]
            admin_user.is_active = form.cleaned_data["is_active"]
            if form.cleaned_data["new_password"]:
                admin_user.set_password(form.cleaned_data["new_password"])
            admin_user.save()
            profile.branches.set(form.cleaned_data["branches"])
            messages.success(request, f"Branch Admin '{admin_user.username}' updated.")
            return redirect("assets:admin_list")
    else:
        form = BranchAdminEditForm(admin_user=admin_user, initial={
            "username": admin_user.username,
            "email": admin_user.email,
            "is_active": admin_user.is_active,
            "branches": profile.branches.all(),
        })
    return render(request, "assets/admin_form.html", {
        "form": form, "title": f"Edit Branch Admin — {admin_user.username}", "admin_user": admin_user,
    })


# ─────────────────────────────────────────────────────────────────────────
# Self-service account settings (any logged-in user, including Super Admin)
# ─────────────────────────────────────────────────────────────────────────

@login_required
@superuser_required
def account_settings(request):
    """Lets the Super Admin change their own username, email, and/or login
    password. `admin_update` deliberately excludes superusers, so this is
    the only place they can do this for themselves. Branch Admins don't
    get this page — their username/password is managed by the Super Admin
    via `admin_update`."""
    user = request.user

    if request.method == "POST":
        form = AccountSettingsForm(request.POST, user=user)
        if form.is_valid():
            user.username = form.cleaned_data["username"]
            user.email = form.cleaned_data["email"]
            new_password = form.cleaned_data["new_password"]
            if new_password:
                user.set_password(new_password)
            user.save()
            if new_password:
                # Keep the current session logged in after a password change.
                update_session_auth_hash(request, user)
            messages.success(request, "Account settings updated.")
            return redirect("assets:account_settings")
    else:
        form = AccountSettingsForm(user=user, initial={
            "username": user.username,
            "email": user.email,
        })
    return render(request, "assets/account_settings.html", {"form": form})


# ─────────────────────────────────────────────────────────────────────────
# Deactivated Assets (Super Admin only — see and undo a deactivation)
# ─────────────────────────────────────────────────────────────────────────

@login_required
@superuser_required
def deactivated_assets(request):
    """Lists every asset with is_active=False, with who deactivated it and
    when (from AssetHistory), so the Super Admin can find and reactivate
    one without needing DB/admin-panel access."""
    last_deactivation = Prefetch(
        "history",
        queryset=AssetHistory.objects.filter(
            field_name="is_active", new_value="False"
        ).select_related("changed_by").order_by("-changed_at", "-id"),
        to_attr="deactivation_entries",
    )
    assets = (
        Asset.objects.filter(is_active=False)
        .select_related("category", "branch")
        .prefetch_related(last_deactivation)
        .order_by("-updated_at")
    )
    return render(request, "assets/deactivated_assets.html", {"assets": assets})


@login_required
@superuser_required
def asset_reactivate(request, asset_id):
    """Super Admin only: flip a deactivated asset back to active. Separate
    from asset_update so this stays a one-click action from the
    Deactivated Assets list (the Edit-form checkbox is also available and
    goes through the same superuser check)."""
    asset = get_object_or_404(Asset, pk=asset_id, is_active=False)
    if request.method == "POST":
        asset.is_active = True
        asset.updated_by = request.user
        asset.save()
        messages.success(request, f"Asset {asset.asset_tag} reactivated.")
        return redirect("assets:deactivated_assets")
    return redirect("assets:deactivated_assets")


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
# Keep in sync with sample_template.SHEET_DATA.
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
    if not export_password:
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
    categories = AssetCategory.objects.all().order_by("name")

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
        assets = list(order_branch_wise(assets.select_related("branch")))
        sheet_name = _safe_sheet_name(category.name, used_names)
        ws = wb.create_sheet(title=sheet_name)

        template = get_template_for_category(category.name)
        is_info_reg = category.name.strip().lower() in INFO_REGISTER_CATEGORIES

        cat_key = category.name.strip().lower()
        if not template and cat_key in TEMPLATE_EXPORT_COLUMNS:
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
                    vals = [(a, a.extra_details.get(f["name"], "")) for a in asset_list]
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
                    val = (asset.extra_details or {}).get(key)
                    if val is None or str(val).strip() == "":
                        norm = _normalize_header(header)
                        if norm in ("asset tag", "asset id", "tag", "id", "hard disk number", "keyboard id", "monitor no", "mouse", "ups no", "bluetooth no", "system no", "employee id"):
                            val = asset.asset_tag
                        elif norm in ("item description", "item / description", "name", "asset name", "hard disk name", "system name", "vendor name", "device name", "devices", "projects", "project name", "employeename", "employee name", "description model", "description / model"):
                            val = asset.name
                        elif norm in ("serial no", "serial no.", "serial number", "s.no", "s no", "product key"):
                            val = asset.serial_number
                        elif norm == "status":
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

        # Branch column (last) so it's clear which branch each row belongs
        # to when several branches are in one file. Import ignores it (the
        # branch is still chosen on the import screen).
        if "branch" not in {str(h).strip().lower() for h in headers}:
            headers = list(headers) + ["Branch"]
            data_rows = [
                list(row) + [a.branch.name if a.branch else ""]
                for row, a in zip(data_rows, assets)
            ]

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
    encrypted = encrypt_xlsx_bytes(buffer.getvalue(), export_password)

    response = HttpResponse(
        encrypted,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="Swift_ProSys_Asset_Register.xlsx"'
    return response


@login_required
@superuser_required
def export_password_settings(request):
    """Super Admin only: set or reset the password on downloaded Excel
    exports. The current password is never displayed."""
    from .forms import ExportPasswordForm

    if request.method == "POST":
        form = ExportPasswordForm(request.POST)
        if form.is_valid():
            set_export_password(form.cleaned_data["password1"], user=request.user)
            messages.success(request, "Excel export password updated.")
            return redirect("assets:export_password")
    else:
        form = ExportPasswordForm()
    is_set, source, updated_at, updated_by = export_password_status()
    return render(request, "assets/export_password.html", {
        "form": form, "is_set": is_set, "source": source,
        "updated_at": updated_at, "updated_by": updated_by,
    })


SESSION_IMPORT_KEY = "bulk_import_rows"
SESSION_IMPORT_BRANCH_KEY = "bulk_import_branch_id"


@login_required
@superuser_required
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

    branch_id = request.POST.get("branch") or request.GET.get("branch")
    import_branch = Branch.objects.filter(pk=branch_id, status=True).first() if branch_id else None

    if request.method == "POST":
        uploaded_file = request.FILES.get("import_file")
        if not import_branch:
            messages.error(request, "Please select which branch this import belongs to.")
        elif not uploaded_file:
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

    rows = request.session.get(SESSION_IMPORT_KEY)
    import_branch_id = request.session.get(SESSION_IMPORT_BRANCH_KEY)
    import_branch = Branch.objects.filter(pk=import_branch_id, status=True).first() if import_branch_id else None
    if not rows:
        messages.error(request, "Nothing to import — please upload a file again.")
        return redirect("assets:bulk_import")
    if not import_branch:
        messages.error(request, "No branch was selected for this import — please upload the file again and choose a branch.")
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
    """Quick search across assets and categories by category name, tag,
    name, brand, serial number, model number, assigned user or location."""
    query = request.GET.get("q", "").strip()
    results = []
    matching_categories = []
    accessible_branches = get_accessible_branches(request.user)
    branch_scope_q = Q(branch__in=accessible_branches)
    if is_super_admin(request.user):
        branch_scope_q |= Q(branch__isnull=True)
    if query:
        matching_categories = list(
            AssetCategory.objects.filter(name__icontains=query)
            .annotate(total=Count(
                "assets", filter=Q(assets__is_active=True) & (Q(assets__branch__in=accessible_branches) | Q(assets__branch__isnull=True) if is_super_admin(request.user) else Q(assets__branch__in=accessible_branches))
            ))
            .order_by("name")
        )
        for cat in matching_categories:
            cat.icon_class = CATEGORY_ICONS.get(cat.name, cat.icon or DEFAULT_CATEGORY_ICON)

        results = (
            Asset.objects.filter(is_active=True).filter(branch_scope_q)
            .filter(
                Q(category__name__icontains=query)
                | Q(asset_tag__icontains=query)
                | Q(name__icontains=query)
                | Q(brand__icontains=query)
                | Q(serial_number__icontains=query)
                | Q(model_number__icontains=query)
                | Q(current_assigned_to__icontains=query)
                | Q(current_location__icontains=query)
                | Q(notes__icontains=query)
            )
            .select_related("category", "branch")
            .order_by("category__name", "asset_tag")[:100]
        )
    return render(request, "assets/search_results.html", {
        "query": query,
        "results": results,
        "matching_categories": matching_categories,
    })


@login_required
def search_suggestions(request):
    """JSON API endpoint providing instant autocomplete suggestions for categories and assets."""
    query = request.GET.get("q", "").strip()
    if not query:
        return JsonResponse({"categories": [], "assets": []})

    accessible_branches = get_accessible_branches(request.user)

    categories = list(
        AssetCategory.objects.filter(name__icontains=query)
        .annotate(total=Count(
            "assets", filter=Q(assets__is_active=True) & Q(assets__branch__in=accessible_branches)
        ))
        .order_by("name")[:5]
    )
    cat_data = [
        {
            "id": c.id,
            "name": c.name,
            "total": c.total,
            "icon": CATEGORY_ICONS.get(c.name, c.icon or DEFAULT_CATEGORY_ICON),
        }
        for c in categories
    ]

    assets = (
        Asset.objects.filter(is_active=True, branch__in=accessible_branches)
        .filter(
            Q(asset_tag__icontains=query)
            | Q(name__icontains=query)
            | Q(brand__icontains=query)
            | Q(serial_number__icontains=query)
            | Q(model_number__icontains=query)
            | Q(current_assigned_to__icontains=query)
            | Q(current_location__icontains=query)
            | Q(category__name__icontains=query)
        )
        .select_related("category")
        .order_by("category__name", "asset_tag")[:8]
    )
    asset_data = [
        {
            "id": a.id,
            "tag": a.asset_tag,
            "name": a.name,
            "category": a.category.name,
            "assigned_to": a.current_assigned_to or "",
            "location": a.current_location or "",
            "status": a.get_status_display(),
        }
        for a in assets
    ]

    return JsonResponse({
        "query": query,
        "categories": cat_data,
        "assets": asset_data,
    })


@login_required
def history_list(request):
    """Full change history ("View all" from the dashboard's Recent Changes),
    newest first, paginated, with optional search / category / field filters."""
    query = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "").strip()
    field = request.GET.get("field", "").strip()

    selected_branch, accessible_branches = resolve_selected_branch(request)
    if selected_branch:
        entries = AssetHistory.objects.filter(asset__branch=selected_branch)
    else:
        branch_q = Q(asset__branch__in=accessible_branches)
        if is_super_admin(request.user):
            branch_q |= Q(asset__branch__isnull=True)
        entries = AssetHistory.objects.filter(branch_q)

    entries = (
        entries
        .select_related("asset", "asset__category", "asset__branch", "changed_by")
    )
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
