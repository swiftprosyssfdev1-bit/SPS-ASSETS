import re

from io import BytesIO



from django.contrib import messages

from django.contrib.auth import get_user_model

from django.contrib.auth.decorators import login_required

from django.contrib.auth.views import LoginView


from django.db import transaction, IntegrityError
from django.core.paginator import Paginator
from django.db.models import Count, F, Q, Prefetch, Max
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
from .models import Asset, AssetCategory, AssetHistory, Branch, UserBranchAccess, Workstation, ImportStaging, prime_display_tags
from .category_fields import (
    _norm_key,
    get_category_fields, resolve_field_value, resolve_extra_value, rekey_extra, is_employee_category, EMPLOYEE_MAIN_COLUMNS,
    workstation_lookup_category, status_driver_column, get_builder_layout, builder_field_column,
    builder_status_choices,
    get_list_display_fields, is_category_db_configured, get_workstation_fields,
    reserved_field_label_problem,
)
from .naming import generate_asset_name
from .sheet_templates import get_template_for_category
from .display import (
    build_detail_fields, build_export_cells, build_export_columns, build_list_cells,
    build_list_columns, detail_heading_shows_name,
)
from .import_utils import (
    IMPORT_COLUMNS, ImportFileError, parse_uploaded_file, validate_rows,
    parse_uploaded_workbook_sheets, validate_workbook_sheets,
    serialize_for_session, deserialize_from_session, _normalize_header,
    read_upload_bytes, validate_workstation_rows, resolve_pending_workstation_lookups,
)
from .excel_security import (
    get_export_password, encrypt_xlsx_bytes, set_export_password, export_password_status,
)
from .permissions import (
    is_super_admin, super_admin_required,
    get_accessible_branches, can_access_branch, require_branch_access,
    resolve_selected_branch, filter_assets_to_branch,
)

from django.conf import settings
from .context_processors import workstation_labels

User = get_user_model()

# Backwards-compatible aliases (kept so nothing else in this file has to
# change its decorator name): superuser_required === Super Admin only.
superuser_required = super_admin_required


class BrandedLoginView(LoginView):
    template_name = "assets/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
            user = form.get_user()

            # Only staff users can access the Asset Register
            if not user.is_staff:
                form.add_error(None, "You do not have permission to access the Asset Register.")
                return self.form_invalid(form)

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
            # Workstation rows hold a PROTECTed FK to Asset, so they must be
            # cleared first or the bulk Asset delete below raises
            # ProtectedError and the whole "Clear All" action fails.
            Workstation.objects.all().delete()
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


def _is_hand_built_category(category):
    """Categories whose list / View / export are still hand-built (not driven
    by the Category Builder yet)."""
    n = category.name.strip().lower()
    return is_workstation_category(category.name)


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
        AssetCategory.objects.exclude(name__iexact=WORKSTATION_CATEGORY_NAME).annotate(
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

    # A builder category with no Status field has no visible status, so it must
    # not show a "need attention" badge driven by the hidden one.
    for _c in main_categories:
        if _c.not_working and not _is_hand_built_category(_c):
            _lay = get_builder_layout(_c)
            if _lay is not None and "status" not in _lay["common_fields"]:
                _c.not_working = 0
    main_categories.sort(key=_dashboard_card_sort_key)

    branch_scoped = Asset.objects.filter(is_active=True)
    branch_scoped = filter_assets_to_branch(
        branch_scoped, selected_branch, accessible_branches,
        include_unassigned=is_super_admin(request.user) and selected_branch is None,
    )

    total_records = branch_scoped.count()
    # Workstation gets its own summary card, computed straight from Asset
    # rows (same pattern relations.py uses) instead of coming from the
    # AssetCategory queryset above, since that queryset now deliberately
    # excludes Workstation.
    workstation_qs = branch_scoped.filter(category__name__iexact=WORKSTATION_CATEGORY_NAME)
    workstation_summary = {
        "total": workstation_qs.count(),
        "not_working": workstation_qs.filter(status__in=ATTENTION_STATUSES).count(),
    }
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
        [:60]
    )

    # Leave out entries whose SHOWN tag still ends in -2 / -3 (a same-sheet
    # collision, reads as a glitch). A tag shared with another category, e.g.
    # SPS011-2 shown as SPS011, is fine and stays. /history has everything.
    prime_display_tags([h.asset for h in recent_changes])
    recent_changes = [h for h in recent_changes if not re.search(r"-\d+$", h.asset.display_tag or "")][:15]
    allow_clear_all = bool(getattr(settings, "ALLOW_CLEAR_ALL", True) and request.user.is_superuser)
    context = {
        "main_categories": main_categories,
        "workstation_summary": workstation_summary,
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
# Workstation is spec'd as its own first-class module, not a normal Asset
# Category — even though (for now) its Asset rows still carry a real
# AssetCategory("Workstation") FK for backward compatibility with existing
# data (see the inspect_workstation_category management command). Every
# place that lists/offers Asset Categories for browsing, counting, the
# Category Builder, new-asset creation, or exports/imports should exclude
# it; only the dedicated workstation_list view and relations.py's
# cross-reference panels are meant to query it directly by name.
WORKSTATION_CATEGORY_NAME = "workstation"


def is_workstation_category(name):
    return (name or "").strip().lower() == WORKSTATION_CATEGORY_NAME

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
    if key:
        _cat = AssetCategory.objects.filter(name__iexact=key).first()
        if _cat is not None:
            _bchoices, _ = builder_status_choices(_cat)
            if _bchoices:
                return _bchoices   # Category Builder's Status dropdown wins
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
    # Sync widget.choices too — the Select widget validates submitted values
    # against its own choices list independently of the field-level choices.
    form.fields["status"].widget.choices = choices
    if instance is None:
        form.initial.setdefault("status", choices[0][0])



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


def _upload_too_large(uploaded_file):
    """Error text if the file exceeds settings.MAX_IMPORT_UPLOAD_MB, else None."""
    limit_mb = getattr(settings, "MAX_IMPORT_UPLOAD_MB", 10)
    if uploaded_file and uploaded_file.size > limit_mb * 1024 * 1024:
        size_mb = uploaded_file.size / (1024 * 1024)
        return f"File is too large ({size_mb:.1f} MB). The maximum allowed size is {limit_mb} MB."
    return None


def order_branch_wise(qs):
    """Records grouped branch by branch — all of the first branch's, then the
    next branch's, and so on (branches in the order they were created:
    Chennai, Tindivanam, ...) — with no-branch records last, and by asset tag
    inside each branch. Used by the category list pages and the Excel export,
    so both show the same order. With a single branch selected it's just
    tag order."""
    return qs.order_by(F("branch_id").asc(nulls_last=True), "asset_tag")


@login_required
def workstation_list(request):
    """Dedicated entry point for the Workstation module.

    Workstation is intentionally NOT part of the Asset Categories section
    (see WORKSTATION_CATEGORY_NAME / is_workstation_category below) — this
    view is its own stable URL (/workstation/) so it never depends on the
    underlying AssetCategory row's numeric id. It reuses category_detail's
    rendering because the existing 45 Workstation Asset records still carry
    category=AssetCategory("Workstation") under the hood (see
    inspect_workstation_category management command for why that row can't
    be deleted), but every other Asset Category surface treats this category
    as hidden.
    """
    category = AssetCategory.objects.filter(name__iexact=WORKSTATION_CATEGORY_NAME).first()
    if category is None:
        messages.error(request, "The Workstation module has no data yet — contact the Super Admin.")
        return redirect("assets:dashboard")
    return category_detail(request, category.id)


# ---------------------------------------------------------------------------
# Workstation add/edit/view — driven entirely by WorkstationField (the
# module configured in workstation_builder()), NOT by AssetForm/AssetField/
# category_fields.py. This is the piece that actually reads what the
# Workstation Field Builder saves; the Field Builder itself only edits
# WorkstationField/WorkstationFieldOption rows and doesn't render a real
# record on its own.
#
# The underlying Asset row (asset_tag, branch, status, notes, is_active) is
# still a normal Asset(category="Workstation") — only its extra_details-
# equivalent data lives on the separate Workstation.extra_details, keyed by
# WorkstationField.key, exactly like Asset.extra_details/AssetField do for
# every other category.
# ---------------------------------------------------------------------------

WORKSTATION_STATUS_CHOICES = [
    ("working", "Working"),
    ("not_working", "Not Working"),
    ("idle", "Idle / In Cupboard"),
    ("scrap", "Scrap / Destroyed"),
    ("missing", "Missing"),
    ("service", "Under Service"),
]


def _workstation_active_fields(mode):
    """Active WorkstationField rows relevant to this form mode ('add' or
    'edit'), as as_field_dict()s — JSON-safe dicts the template/JS can use
    directly (same shape asset_form.html's fieldHtml() already expects:
    name/label/type/width/lookup_category/options)."""
    flag = "show_in_add" if mode == "add" else "show_in_edit"
    qs = WorkstationField.objects.filter(is_active=True).order_by("display_order", "id")
    return [f.as_field_dict() for f in qs if getattr(f, flag)]


def _workstation_render_fields(field_dicts, existing_extra):
    """Attaches each field's current value (or value_json, for a multi
    Lookup) from existing_extra, ready for the template to render."""
    existing_extra = existing_extra or {}
    rendered = []
    for f in field_dicts:
        f = dict(f)
        raw = existing_extra.get(f["name"])
        if f["type"] == "lookup" and f.get("lookup_multi"):
            values = raw if isinstance(raw, list) else ([raw] if raw else [])
            f["value_json"] = json.dumps(values)
        else:
            f["value"] = raw if raw is not None else ""
        rendered.append(f)
    return rendered


def _extract_workstation_extra_details(request, existing_extra=None):
    """Mirrors _extract_extra_details, but keyed off WorkstationField
    instead of AssetField/category_fields, and supports lookup_multi
    (posted as a JSON-encoded array in the same POST field name — see
    the ws-combo-multi widget in workstation_asset_form.html).

    Only fields whose input actually exists in this POST are touched, so
    a field hidden on this form (show_in_add False on the Add form, say)
    never gets silently blanked out — its previous value is preserved.
    """
    result = dict(existing_extra or {})
    for field in WorkstationField.objects.filter(is_active=True):
        name = field.key
        if name not in request.POST:
            continue
        if field.field_type == "lookup" and field.lookup_multi:
            raw = request.POST.get(name, "")
            try:
                tokens = [str(t).strip() for t in json.loads(raw) if str(t or "").strip()]
            except (ValueError, TypeError):
                tokens = [t.strip() for t in raw.split(",") if t.strip()]
            result[name] = tokens
        else:
            result[name] = request.POST.get(name, "").strip()
    return result


@login_required
def workstation_asset_add(request):
    accessible_branches = get_accessible_branches(request.user)
    if not accessible_branches.exists():
        messages.error(request, "You don't have any branches assigned yet — contact the Super Admin.")
        return redirect("assets:dashboard")

    category = AssetCategory.objects.filter(name__iexact=WORKSTATION_CATEGORY_NAME).first()
    if category is None:
        messages.error(request, "The Workstation module has no data yet — contact the Super Admin.")
        return redirect("assets:dashboard")

    selected_branch, _ = resolve_selected_branch(request)
    existing_extra = {}

    if request.method == "POST":
        asset_tag = (request.POST.get("asset_tag") or "").strip()
        status = request.POST.get("status", "working")
        notes = request.POST.get("notes", "")
        branch = accessible_branches.filter(pk=request.POST.get("branch")).first()

        if not asset_tag:
            messages.error(request, "Workstation ID is required.")
        elif Asset.objects.filter(asset_tag__iexact=asset_tag).exists():
            messages.error(request, f"Asset tag \"{asset_tag}\" is already in use.")
        elif branch is None:
            messages.error(request, "Please select a valid branch.")
        else:
            with transaction.atomic():
                asset = Asset.objects.create(
                    category=category, branch=branch, asset_tag=asset_tag,
                    name=generate_asset_name("Workstation", asset_tag), status=status, notes=notes,
                    updated_by=request.user,
                )
                Workstation.objects.create(
                    asset=asset,
                    extra_details=_extract_workstation_extra_details(request),
                )
            messages.success(request, f"Workstation {asset.asset_tag} added.")
            return redirect("assets:workstation_list")
        existing_extra = _extract_workstation_extra_details(request)

    return render(request, "assets/workstation_asset_form.html", {
        "title": "Add Workstation",
        "asset": None,
        "accessible_branches": accessible_branches,
        "selected_branch_id": (selected_branch.pk if selected_branch else
                                (accessible_branches.first().pk if accessible_branches.count() == 1 else None)),
        "status_choices": WORKSTATION_STATUS_CHOICES,
        "selected_status": "working",
        "fields": _workstation_render_fields(_workstation_active_fields("add"), existing_extra),
    })


@login_required
def workstation_asset_edit(request, asset_id):
    asset = get_object_or_404(Asset, pk=asset_id, category__name__iexact=WORKSTATION_CATEGORY_NAME)
    require_branch_access(request.user, asset.branch)
    accessible_branches = get_accessible_branches(request.user)
    profile, _ = Workstation.objects.get_or_create(asset=asset)

    if request.method == "POST":
        asset_tag = (request.POST.get("asset_tag") or "").strip()
        status = request.POST.get("status", asset.status)
        notes = request.POST.get("notes", "")
        branch = accessible_branches.filter(pk=request.POST.get("branch")).first()
        was_inactive = not asset.is_active

        if not asset_tag:
            messages.error(request, "Workstation ID is required.")
        elif Asset.objects.filter(asset_tag__iexact=asset_tag).exclude(pk=asset.pk).exists():
            messages.error(request, f"Asset tag \"{asset_tag}\" is already in use.")
        elif branch is None:
            messages.error(request, "Please select a valid branch.")
        else:
            require_branch_access(request.user, branch)
            asset.asset_tag = asset_tag
            asset.branch = branch
            asset.status = status
            asset.notes = notes
            if request.user.is_superuser or not was_inactive:
                asset.is_active = request.POST.get("is_active") == "true"
            elif was_inactive:
                messages.info(request, "Only the Super Admin can reactivate a deactivated asset — Active status unchanged.")
            asset.updated_by = request.user
            asset.save()

            profile.extra_details = _extract_workstation_extra_details(request, profile.extra_details)
            profile.save()

            messages.success(request, f"Workstation {asset.asset_tag} updated.")
            return redirect("assets:workstation_list")

    return render(request, "assets/workstation_asset_form.html", {
        "title": f"Edit Workstation — {asset.asset_tag}",
        "asset": asset,
        "accessible_branches": accessible_branches,
        "selected_branch_id": asset.branch_id,
        "status_choices": WORKSTATION_STATUS_CHOICES,
        "selected_status": asset.status,
        "fields": _workstation_render_fields(_workstation_active_fields("edit"), profile.extra_details),
    })


@login_required
def workstation_asset_view(request, asset_id):
    asset = get_object_or_404(Asset, pk=asset_id, category__name__iexact=WORKSTATION_CATEGORY_NAME)
    require_branch_access(request.user, asset.branch)
    profile = getattr(asset, "workstation_profile", None)
    extra = (profile.extra_details if profile else {}) or {}

    display_fields = []
    for f in WorkstationField.objects.filter(is_active=True, show_in_detail=True).order_by("display_order", "id"):
        value = extra.get(f.key)
        resolved = None
        if f.field_type == "lookup" and value:
            if f.lookup_multi:
                resolved = list(Asset.objects.filter(asset_tag__in=value))
            else:
                resolved = Asset.objects.filter(asset_tag=value).first()
        display_fields.append({"field": f, "value": value, "resolved": resolved})

    back_url, back_label = _resolve_back_link(request, reverse("assets:workstation_list"), "Back to " + workstation_labels()[0])
    return render(request, "assets/workstation_asset_detail.html", {
        "asset": asset, "display_fields": display_fields,
        "back_url": back_url, "back_label": back_label,
    })


def _create_workstation_rows(request, rows, import_branch):
    """Create Workstation records (Asset + Workstation) from validated rows.
    Shared by the standalone Workstation import and the combined multi-sheet
    import. Returns (created_count, failed_messages). Deferred lookups
    (pending_lookups) are resolved here, so when this runs after the other
    sheets were saved, links to assets from the same upload resolve."""
    category = AssetCategory.objects.filter(name__iexact=WORKSTATION_CATEGORY_NAME).first()
    if category is None:
        return 0, ["Workstation: the Workstation module has no data yet — contact the Super Admin."]
    created_count, failed = 0, []
    for f in rows:
        f = dict(f)
        branch = Branch.objects.filter(pk=f.get("branch_id") or import_branch.id).first() or import_branch
        tag = f.get("asset_tag", "")
        if not tag or Asset.objects.filter(asset_tag__iexact=tag).exists():
            failed.append(f"{tag or '?'}: Asset tag missing or already exists")
            continue
        extra = dict(f.get("extra_details") or {})
        unresolved = dict(f.get("unresolved") or {})
        # Values for Dropdown fields that weren't in the option list yet: add
        # them as options (once) so the record's value shows up in the dropdown.
        for _fkey, _val in (f.get("new_options") or {}).items():
            _field = WorkstationField.objects.filter(key=_fkey).first()
            if _field and not WorkstationFieldOption.objects.filter(field=_field, value__iexact=_val).exists():
                WorkstationFieldOption.objects.create(
                    field=_field, label=_val, value=_val,
                    display_order=_field.options.count() + 1,
                )
        if f.get("pending_lookups"):
            updates, more_unresolved = resolve_pending_workstation_lookups(f["pending_lookups"])
            extra.update(updates)
            unresolved.update(more_unresolved)
        try:
            # Savepoint per row: a failure rolls back that row's Asset too
            # and leaves the rest of the import usable.
            with transaction.atomic():
                asset = Asset.objects.create(
                    category=category, branch=branch, asset_tag=tag,
                    name=generate_asset_name("Workstation", tag), status=f.get("status", "working"),
                    notes=f.get("notes", ""), updated_by=request.user,
                )
                Workstation.objects.create(asset=asset, extra_details=extra, unresolved=unresolved)
            created_count += 1
        except Exception as exc:
            failed.append(f"{tag or '?'}: {exc}")
    return created_count, failed


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

    branch_id = request.POST.get("branch") or request.GET.get("branch")
    import_branch = Branch.objects.filter(pk=branch_id, status=True).first() if branch_id else None

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


@login_required
def category_detail(request, category_id):
    category = get_object_or_404(AssetCategory, pk=category_id)
    selected_branch, accessible_branches = resolve_selected_branch(request)

    assets = category.assets.filter(is_active=True).select_related("branch", "workstation_profile")
    assets = filter_assets_to_branch(
        assets, selected_branch, accessible_branches,
        include_unassigned=is_super_admin(request.user) and selected_branch is None,
    )
    assets = order_branch_wise(assets)

    cat_norm = category.name.strip().lower()
    is_info_register = cat_norm in INFO_REGISTER_CATEGORIES
    is_incident = cat_norm == "incident register"
    is_project_details = cat_norm == "project details"
    hide_name = cat_norm in NO_REAL_NAME_CATEGORIES
    tag_label = TAG_LABEL_OVERRIDES.get(cat_norm, "Tag")
    category_fields = get_category_fields(category)
    list_fields = get_list_display_fields(category)
    show_status = True
    _layout = get_builder_layout(category)
    if _layout is not None and not is_workstation_category(category.name):
        # Category Builder is the single source of truth: only its fields are columns.
        list_fields = [f for f in list_fields if f["name"] not in _layout["list_skip"]]
        tag_label = _layout["labels"].get("tag_label", tag_label)
        hide_name = not _layout["show_name"]
        show_status = "status" in _layout["common_fields"]
    name_label = "Name"
    if cat_norm == "other asset":
        # On this page the Asset Tag column IS the "ID" and the Name column IS
        # the "Device Name" (that is what the old Others sheet calls them), so
        # show just those two — not a second, duplicate ID / Device Name pair.
        tag_label = "ID"
        name_label = "Device Name"
        hide_name = False
        list_fields = [
            f for f in list_fields
            if re.sub(r"[^a-z0-9]+", " ", str(f.get("label") or "").lower()).strip()
            not in ("id", "device name", "asset tag", "name")
        ]
    _generic_list = not (
        is_info_register or is_incident
        or is_project_details or cat_norm == "it vendor"
        or cat_norm == "other asset" or is_workstation_category(category.name)
    )
    if _generic_list and list_fields:
        # Same rule the Add/Edit form uses: the field whose header is the
        # category's ID (Monitor No, Keyboard Id, Mouse, UPS No, Bluetooth No,
        # ...) IS the Asset Tag, and the one that is its name IS the Name.
        # The list already draws those two as its first column(s), so they
        # must not be drawn a second time as ordinary field columns. The
        # column header takes the field's own label from the Field Builder.
        from .import_utils import SHEET_HEADER_OVERRIDES, HEADER_ALIASES, LABEL_TO_FIELD, _normalize_header
        from .sheet_templates import CATEGORY_TO_TEMPLATE
        _ov = SHEET_HEADER_OVERRIDES.get(CATEGORY_TO_TEMPLATE.get(cat_norm), {}) or {}
        _tag_f = _name_f = None
        _kept = []
        for _f in list_fields:
            _n = _normalize_header(_f.get("label"))
            _m = _ov.get(_n) or LABEL_TO_FIELD.get(_n) or HEADER_ALIASES.get(_n)
            if _m == "asset_tag" and _tag_f is None:
                _tag_f = _f
            elif _m == "name" and _name_f is None:
                _name_f = _f
            else:
                _kept.append(_f)
        list_fields = _kept
        if _tag_f:
            tag_label = _tag_f["label"]
        if _name_f:
            name_label = _name_f["label"]
            hide_name = False
    if is_workstation_category(category.name):
        # Workstation data lives on Workstation.extra_details under
        # WorkstationField.key. Point each list column at the matching
        # WorkstationField (by label, else by key) so its value is found.
        _ws = {}
        for _wf in WorkstationField.objects.filter(is_active=True):
            _ws[_norm_key(_wf.label)] = _wf.key
            _ws.setdefault(_norm_key(_wf.key), _wf.key)
        _fixed = []
        for _f in list_fields:
            _f = dict(_f)
            _k = _ws.get(_norm_key(_f.get("label"))) or _ws.get(_norm_key(_f.get("name")))
            if _k:
                _f["name"] = _k
            _fixed.append(_f)
        list_fields = _fixed
        _fix_summary = lambda f: (lambda _k: {**f, "name": _k} if _k else f)(
            _ws.get(_norm_key(f.get("label"))) or _ws.get(_norm_key(f.get("name"))))

    # The generic (else-branch) list table renders dynamically from
    # list_fields once a category has ANY configured/discovered fields —
    # via the Category Builder, a legacy sheet template, curated defaults,
    # or data discovery. Categories with a hand-built display of their own
    # (cupboard, vendor, employee, incident, project details, hard disk,
    # air conditioner, info-register) keep that dedicated branch untouched.
    has_dynamic_fields = bool(list_fields)

    list_summary_field = _list_summary_field(category, category_fields) if (is_info_register and not is_incident and not is_project_details) else None

    if is_workstation_category(category.name) and list_summary_field:
        list_summary_field = _fix_summary(list_summary_field)
    assets = prime_display_tags(assets)

    # Category Builder is the single source of truth for a builder-configured
    # category: its active fields (order, labels, List visibility) ARE the
    # columns (see display.py). None = no builder fields -> legacy layout.
    list_columns = None
    if not is_workstation_category(category.name):
        list_columns = build_list_columns(category, default_tag_label=tag_label)
    show_branch_column = selected_branch is None
    if list_columns:
        link_key = list_summary_field["name"] if list_summary_field else None
        from .relations import resolve_asset_reference
        assets = list(assets)
        for _a in assets:
            _a.list_cells = build_list_cells(
                _a, list_columns, link_key=link_key,
                resolver=lambda v: resolve_asset_reference(v, None),
            )
        # columns + Branch (when shown) + Actions
        column_count = len(list_columns) + (1 if show_branch_column else 0) + 1
    else:
        column_count = 8
    # These flags are only needed by the template's LEGACY fallback branches
    # (the hand-built table headers for when list_columns is None). Once a
    # category has builder fields, list_columns is non-None and these flags
    # are never consulted by the template. Keeping them here preserves the
    # existing HTML layout for categories that have no builder fields yet.
    _no_builder = list_columns is None
    return render(request, "assets/category_detail.html", {
        "category": category,
        "assets": assets,
        "list_columns": list_columns,
        "column_count": column_count,
        "category_fields": category_fields,
        "list_fields": list_fields,
        "has_dynamic_fields": has_dynamic_fields,
        "list_summary_field": list_summary_field,
        "is_info_register": is_info_register,
        "is_incident": is_incident,
        "is_project_details": is_project_details,
        "hide_name": hide_name,
        "show_status": show_status,
        "tag_label": tag_label,
        "name_label": name_label,
        "is_vendor": cat_norm == "it vendor",
        "accessible_branches": accessible_branches,
        "selected_branch": selected_branch,
        "is_super_admin": is_super_admin(request.user),
        "show_branch_column": show_branch_column,
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






def _extra_details_valid(form, request, category, mode="add", existing_extra=None):
    """Server-side check of the category-specific (dynamic) fields.
    Enforces required + type (number/date/email/url) + dropdown options.
    Errors are attached to the form (non-field) so the page re-renders.
    JavaScript is UX only; this is the real gate."""
    from datetime import datetime
    from django.core.validators import URLValidator, validate_email
    from django.core.exceptions import ValidationError
    config = _build_category_form_config().get(str(getattr(category, "pk", "")))
    if not config:
        return True
    existing_extra = existing_extra or {}
    flag = "show_in_add" if mode == "add" else "show_in_edit"
    ok = True
    for col, label in config.get("required_common", []):
        if not form.cleaned_data.get(col):
            form.add_error(None, f"{label} is required.")
            ok = False
    for f in config.get("extra_fields", []):
        if not f.get(flag, True):
            continue
        name, label, ftype = f["name"], f["label"], f.get("type", "text")
        val = (request.POST.get(name, "") or "").strip()
        if val == "":
            if f.get("required"):
                form.add_error(None, f"{label} is required.")
                ok = False
            continue
        err = None
        if ftype == "number":
            try:
                float(val)
            except ValueError:
                err = f"{label} must be a number."
        elif ftype == "date":
            try:
                datetime.strptime(val, "%Y-%m-%d")
            except ValueError:
                err = f"{label} must be a valid date (YYYY-MM-DD)."
        elif ftype == "email":
            try:
                validate_email(val)
            except ValidationError:
                err = f"{label} must be a valid email address."
        elif ftype == "url":
            try:
                URLValidator()(val)
            except ValidationError:
                err = f"{label} must be a valid URL."
        elif ftype in ("select", "searchable_select"):
            opts = f.get("options") or []
            if opts and val not in opts and val != str(existing_extra.get(name, "")):
                err = f"{label}: choose a valid option."
        
        if not err and f.get("max_length") and len(val) > f["max_length"]:
            err = f"{label} cannot exceed {f['max_length']} characters."
            
        if err:
            form.add_error(None, err)
            ok = False
    return ok


def _build_category_form_config():
    """Per-category config for the Add/Edit Asset form's JavaScript, so
    switching the Category dropdown updates which fields show instantly
    with no page reload: which common fields apply, custom labels, and that category's
    own extra fields (with type/options/workstation-lookup info)."""
    from .import_utils import SHEET_HEADER_OVERRIDES, HEADER_ALIASES, LABEL_TO_FIELD, _normalize_header
    from .sheet_templates import CATEGORY_TO_TEMPLATE
    from .category_fields import get_category_field_labels, get_common_fields, get_category_fields, workstation_lookup_category, get_builder_layout

    config = {}
    for cat in AssetCategory.objects.all():
        cat_norm = cat.name.strip().lower()
        lbl_cfg = get_category_field_labels(cat.name)
        common_fields = get_common_fields(cat.name)
        layout = get_builder_layout(cat)
        if layout is not None:
            # Category Builder is the single source of truth for this category.
            common_fields = layout["common_fields"]
            lbl_cfg = {**lbl_cfg, **layout["labels"], "show_name": layout["show_name"]}
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
        if layout is not None or cat_norm not in (
                            "employee",
                            "employee list",
                            "project details",
                            "other asset",
                            "networking equipment",
                        ):

            for f in get_category_fields(cat):
                if layout is not None and f["name"] in layout["consumed"]:
                    continue
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

                entry = {"name": f["name"], "label": f["label"], "type": f["type"], "width": f.get("width", 6),
                         "required": bool(f.get("required", False)),
                         "show_in_add": f.get("show_in_add", True), "show_in_edit": f.get("show_in_edit", True)}
                if f.get("options"):
                    entry["options"] = f["options"]
                lookup = f.get("lookup_category") or workstation_lookup_category(f["label"])
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
            "show_name": show_name,
            "show_notes": layout["show_notes"] if layout is not None else True,
            "core_meta": layout["core_meta"] if layout is not None else {},
            "required_common": layout["required_common"] if layout is not None else [],
            "tag_label": lbl_cfg.get("tag_label", "Asset Tag"),
            "name_label": lbl_cfg.get("name_label", "Name"),
            "serial_label": lbl_cfg.get("serial_label", "Serial Number"),
            "location_label": lbl_cfg.get("location_label", "Current Location"),
            "assigned_label": lbl_cfg.get("assigned_label", "Current Assigned To"),
            "model_label": lbl_cfg.get("model_label", "Model Number"),
            "asset_tag_help": "Unique ID",
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
    is_info_register = cat_key in INFO_REGISTER_CATEGORIES

    if request.method == "POST":
        form = AssetForm(request.POST, accessible_branches=accessible_branches, user=request.user)
        _status_form_setup(form, category)
        if form.is_valid() and _extra_details_valid(form, request, form.cleaned_data.get("category") or category, "add"):
            asset = form.save(commit=False)
            asset.updated_by = request.user
            if not asset.name or not asset.name.strip():
                asset.name = generate_asset_name(
                    asset.category.name, asset.asset_tag, asset.brand, asset.model_number)
            asset.extra_details = _extract_extra_details(request, asset.category)
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
        "is_info_register": is_info_register,
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
    is_info_register = cat_key in INFO_REGISTER_CATEGORIES
    old_status = asset.status

    if request.method == "POST":
        form = AssetForm(request.POST, instance=asset, accessible_branches=accessible_branches, user=request.user)
        posted_category = AssetCategory.objects.filter(pk=request.POST.get("category") or None).first()
        _status_form_setup(form, posted_category or asset.category, instance=Asset.objects.get(pk=asset.pk))
        if form.is_valid() and _extra_details_valid(form, request, form.cleaned_data.get("category") or asset.category, "edit", rekey_extra(asset.category, asset.extra_details)):
            updated = form.save(commit=False)
            require_branch_access(request.user, updated.branch)
            updated.updated_by = request.user
            if not updated.name or not updated.name.strip():
                updated.name = generate_asset_name(
                    updated.category.name, updated.asset_tag, updated.brand, updated.model_number)
            category_fields = get_category_fields(updated.category)
            updated.extra_details = _extract_extra_details(request, updated.category, existing_extra=rekey_extra(asset.category, asset.extra_details))
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
        "is_info_register": is_info_register,
        "workstation_lookup_category": workstation_lookup_category,
        "is_workstation": cat_key == "workstation",
        "category_form_config": _build_category_form_config(),
        "asset_extra_details": rekey_extra(asset.category, asset.extra_details),
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
    elif name == "workstation_list":
        return back, "Back to " + workstation_labels()[0]
    elif name == "workstation_asset_view":
        prev = Asset.objects.filter(pk=match.kwargs.get("asset_id")).first()
        if prev and can_access_branch(request.user, prev.branch):
            return back, "Back to " + workstation_labels()[0]
    return default_url, default_label


@login_required
def asset_detail(request, asset_id):
    """Read-only 'View' page - the complete record, organized into
    sections. For a category configured in the Category Builder the page
    follows the builder exactly (same rule as the list page): the Tag is
    always the heading, Name / Status show only if the builder has that field,
    and every other builder field is listed under Details."""
    asset = get_object_or_404(Asset, pk=asset_id)
    require_branch_access(request.user, asset.branch)
    cat_norm = asset.category.name.strip().lower()

    # Defaults = legacy behaviour (unchanged for non-builder categories)
    builder_mode = False
    show_name = True
    show_status = True

    # Hand-built categories keep their own layout for now.
    hand_built = is_workstation_category(asset.category.name)
    layout = None if hand_built else get_builder_layout(asset.category)

    category_fields = get_category_fields(asset.category)
    driver = status_driver_column(asset.category)

    if layout is not None:
        builder_mode = True
        show_name = layout["show_name"]
        show_status = "status" in layout["common_fields"]
        # Tag / Name / Status are drawn in the heading + overview, not repeated in Details
        category_fields = [f for f in category_fields if f["name"] not in layout["list_skip"]]

    def _detail_value(f):
        # the Status/Condition column shows the real status, not the
        # (possibly empty / stale) imported text copy
        if driver and _normalize_header(f["label"]) == driver:
            return asset.get_status_display()
        # extra_details (any old-key spelling), then the asset's own
        # columns - see category_fields.resolve_field_value
        return resolve_field_value(asset, f)

    detail_fields = [
        {"label": f["label"], "value": _detail_value(f),
         "width": 12 if f.get("width") == 12 else 6,
         "type": f.get("type") or "text"}
        for f in category_fields
    ]

    # A category configured in the Category Builder follows the builder
    # exactly (order, labels, Detail visibility) - see display.py. This
    # replaces the hand-built / legacy layouts above for those categories.
    built_fields = None if is_workstation_category(asset.category.name) else build_detail_fields(asset)
    if built_fields is not None:
        detail_fields = built_fields
        builder_mode = True
        show_name = detail_heading_shows_name(asset) or cat_norm == "other asset"
        # Status is shown in the overview only when the builder doesn't list it
        show_status = not any(f.get("kind") == "status" for f in built_fields)
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
        "builder_mode": builder_mode,
        "show_name": show_name,
        "show_status": show_status,
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
    elif category_name.isdigit():
        category_q = Q(category_id=int(category_name))
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
            "name": a.name,
            "label": f"{a.asset_tag} — {a.name}" + (f" — SN {a.serial_number}" if a.serial_number else ""),
        }
        for a in qs
    ]
    # Tell the form which single branch the list was limited to, so an empty
    # result can say "in Chennai" instead of a bare "not found".
    branch_name = branches.first().name if branches.count() == 1 else ""
    return JsonResponse({"results": results, "branch": branch_name})


def builtin_category_names():
    """Lower-case names of categories the code treats specially (sheet
    templates, curated fields, status/tag rules...). Their NAME can't change
    and they can't be deleted, or list / import / export logic would break."""
    from .sheet_templates import CATEGORY_TO_TEMPLATE
    from .category_fields import CATEGORY_FIELDS, STATUS_DRIVER_COLUMN, STATUS_ONLY_CATEGORIES
    names = {"workstation", "hard disk", "air conditioner", "inside cupboard", "inside the cupboard"}
    for group in (INFO_REGISTER_CATEGORIES, TAG_LABEL_OVERRIDES, NO_REAL_NAME_CATEGORIES,
                  CATEGORY_TO_TEMPLATE, CATEGORY_FIELDS, STATUS_DRIVER_COLUMN, STATUS_ONLY_CATEGORIES):
        names.update(str(n).strip().lower() for n in group)
    return names


@login_required
@superuser_required
def workstation_rename(request):
    """Rename the Workstation module's DISPLAY name only (nav, dashboard,
    list title, buttons). The internal name stays "workstation"."""
    from .models import SiteSetting
    cur_label, cur_plural = workstation_labels()
    error = None
    if request.method == "POST":
        label = (request.POST.get("label") or "").strip()
        plural = (request.POST.get("plural") or "").strip()
        if not label:
            error = "Name is required."
        elif len(label) > 60 or len(plural) > 60:
            error = "Keep the name under 60 characters."
        else:
            SiteSetting.objects.update_or_create(key="workstation_label", defaults={"value": label})
            SiteSetting.objects.update_or_create(key="workstation_label_plural", defaults={"value": plural})
            _log_config_change(request.user, "update", "AssetCategory", None, "Workstation (display name)",
                               {"label": [cur_label, label]})
            messages.success(request, "Name updated.")
            return redirect("assets:workstation_list")
    return render(request, "assets/workstation_rename.html", {
        "label": cur_label, "plural": cur_plural, "error": error,
    })


@login_required
@superuser_required
def category_edit(request, category_id):
    category = get_object_or_404(AssetCategory, pk=category_id)
    builtin = builtin_category_names()
    is_builtin = category.name.strip().lower() in builtin
    if category.name.strip().lower() == "workstation":
        messages.error(request, "Workstation is managed separately and cannot be edited here.")
        return redirect("assets:dashboard")
    old_name = category.name
    if request.method == "POST":
        form = AssetCategoryForm(request.POST, instance=category,
                                 lock_name=is_builtin, reserved_names=builtin)
        if form.is_valid():
            with transaction.atomic():
                saved = form.save()
                _log_config_change(request.user, "update", "AssetCategory", saved.pk, saved.name,
                                   {"name": [old_name, saved.name]} if saved.name != old_name else None)
            messages.success(request, "Category updated.")
            return redirect("assets:category_detail", category_id=saved.pk)
    else:
        form = AssetCategoryForm(instance=category, lock_name=is_builtin, reserved_names=builtin)
    return render(request, "assets/category_form.html", {
        "form": form, "title": "Edit Category", "category": category, "name_locked": is_builtin,
    })


@login_required
@superuser_required
def category_delete(request, category_id):
    category = get_object_or_404(AssetCategory, pk=category_id)
    name = category.name
    problem = None
    asset_count = 0
    active_count = 0
    if name.strip().lower() in builtin_category_names():
        problem = "This is a built-in category used by the system, so it can't be deleted."
    else:
        asset_count = category.assets.count()
        active_count = category.assets.filter(is_active=True).count()
        if WorkstationField.objects.filter(lookup_category_id=category.pk).exists():
            problem = "A Workstation field links to this category. Change that field first."
        else:
            # Category Builder fields that pull their dropdown options from this
            # category. Deleting it would silently leave them with an empty list.
            _linked = list(
                AssetField.objects.filter(lookup_category_id=category.pk, is_active=True)
                .select_related("category").order_by("category__name", "label")[:5]
            )
            if _linked:
                _names = ", ".join(f'"{f.label}" ({f.category.name})' for f in _linked)
                problem = (
                    f"These fields use this category as their Lookup Source: {_names}. "
                    "Change or remove those fields first."
                )
    if request.method == "POST":
        if problem:
            messages.error(request, problem)
            return redirect("assets:category_detail", category_id=category.pk)
        # Assets exist: the admin must explicitly confirm deleting them too.
        if asset_count and request.POST.get("confirm_delete_assets") != "yes":
            messages.error(request, "Tick the confirmation box to delete the category together with its assets.")
            return redirect("assets:category_delete", category_id=category.pk)
        with transaction.atomic():
            deleted_assets = category.assets.count()
            if deleted_assets:
                category.assets.all().delete()   # history rows cascade
            AssetField.objects.filter(category=category).delete()
            cat_id = category.pk
            category.delete()
            _log_config_change(request.user, "delete", "AssetCategory", cat_id, name, None)
        messages.success(request, f'Category "{name}" deleted' + (f" along with {deleted_assets} asset record(s)." if deleted_assets else "."))
        return redirect("assets:dashboard")
    return render(request, "assets/category_confirm_delete.html", {
        "category": category, "problem": problem,
        "asset_count": asset_count, "active_count": active_count,
        "inactive_count": asset_count - active_count,
    })


@login_required
@superuser_required
def category_create(request):
    if request.method == "POST":
        form = AssetCategoryForm(request.POST)
        if form.is_valid():
            new_category = form.save()
            messages.success(request, "Category added. Now add its fields below.")
            return redirect("assets:category_builder", category_id=new_category.pk)
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
    assets = prime_display_tags(assets)
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

    branch_id = request.POST.get("branch") or request.GET.get("branch")
    import_branch = Branch.objects.filter(pk=branch_id, status=True).first() if branch_id else None

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
    results = prime_display_tags(results)
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
    prime_display_tags([h.asset for h in page])

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




# ─────────────────────────────────────────────────────────────────────────

# Category Builder (Super Admin only)

# ─────────────────────────────────────────────────────────────────────────

import json

import re as _re










from .models import AssetField, AssetFieldOption, ConfigAuditLog
from .models import WorkstationField, WorkstationFieldOption



def _auto_key(label, existing_keys):

    base = _re.sub(r"[^a-z0-9]+", "_", str(label or "").lower()).strip("_")[:90] or "field"

    key = base

    n = 2

    while key in existing_keys:

        key = f"{base}_{n}"

        n += 1

    return key



def _log_config_change(user, action, model_name, obj_id, obj_repr, diff=None):

    ConfigAuditLog.objects.create(

        changed_by=user,

        action=action,

        model_name=model_name,

        object_id=obj_id,

        object_repr=str(obj_repr)[:255],

        diff=json.dumps(diff or {}),

    )



@login_required

@superuser_required

def category_builder(request, category_id):

    category = get_object_or_404(AssetCategory, pk=category_id)

    if category.name.strip().lower() == "workstation":

        messages.error(request, "Workstation is managed separately and cannot be configured here.")

        return redirect("assets:dashboard")



    fields = AssetField.objects.filter(category=category).order_by("display_order", "id")

    return render(request, "assets/category_builder.html", {

        "category": category,

        "fields": fields.select_related("lookup_category"),
        "category_choices": AssetCategory.objects.order_by("name"),

        "is_super_admin": True,

    })





@login_required

@superuser_required

def builder_field_add(request, category_id):

    category = get_object_or_404(AssetCategory, pk=category_id)

    if category.name.strip().lower() == "workstation":

        if request.headers.get("x-requested-with") == "XMLHttpRequest":

            return JsonResponse({"ok": False, "error": "Workstation is excluded from the builder."}, status=400)

        messages.error(request, "Workstation is excluded from the builder.")

        return redirect("assets:dashboard")



    if request.method != "POST":

        return redirect("assets:category_builder", category_id=category_id)



    label       = request.POST.get("label", "").strip()

    field_type  = request.POST.get("field_type", "text")

    width       = int(request.POST.get("width", 6))

    required    = request.POST.get("required") == "true"

    placeholder = request.POST.get("placeholder", "").strip()

    help_text   = request.POST.get("help_text", "").strip()

    show_list   = request.POST.get("show_in_list", "true") != "false"

    show_detail = request.POST.get("show_in_detail", "true") != "false"

    show_add    = request.POST.get("show_in_add", "true") != "false"

    show_edit   = request.POST.get("show_in_edit", "true") != "false"
    options_raw = request.POST.get("options", "")
    ml_raw = request.POST.get("max_length", "").strip()
    max_length = int(ml_raw) if ml_raw.isdigit() else None
    lookup_cat_id = None
    if field_type == "searchable_select" and request.POST.get("options_source") == "assets":
        # Lookup mode: options come from existing records of the chosen category.
        # (Manual mode: typed options, handled below like a normal Dropdown.)
        cid = (request.POST.get("lookup_category") or "").strip()
        if not (cid.isdigit() and AssetCategory.objects.filter(pk=int(cid)).exists()):
            messages.error(request, "Pick a Lookup Source for the Searchable Dropdown.")
            return redirect("assets:category_builder", category_id=category_id)
        lookup_cat_id = int(cid)



    if not label:

        if request.headers.get("x-requested-with") == "XMLHttpRequest":

            return JsonResponse({"ok": False, "error": "Field label is required."}, status=400)

        messages.error(request, "Field label is required.")

        return redirect("assets:category_builder", category_id=category_id)



    existing_keys = set(AssetField.objects.filter(category=category).values_list("key", flat=True))

    key = _auto_key(label, existing_keys)

    _reserved = reserved_field_label_problem(category.name, label, key)
    if _reserved:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": _reserved}, status=400)
        messages.error(request, _reserved)
        return redirect("assets:category_builder", category_id=category_id)



    max_order = AssetField.objects.filter(category=category).aggregate(

        m=Max("display_order")

    )["m"] or 0



    field = AssetField.objects.create(

        category=category,

        label=label,

        key=key,

        field_type=field_type,

        width=width,

        required=required,

        placeholder=placeholder,

        help_text=help_text,

        show_in_list=show_list,

        show_in_detail=show_detail,

        show_in_add=show_add,

        show_in_edit=show_edit,
        display_order=max_order + 10,
        is_active=True,
        max_length=max_length,
        lookup_category_id=lookup_cat_id,

    )



    if field_type in ("select", "searchable_select") and options_raw.strip():

        for idx, opt_label in enumerate(options_raw.splitlines()):

            opt_label = opt_label.strip()

            if opt_label:

                AssetFieldOption.objects.create(

                    field=field, label=opt_label, value=opt_label, display_order=idx

                )



    _log_config_change(

        request.user, "create", "AssetField", field.pk,

        f"{category.name}: {field.label} ({field.key})",

        {"label": [None, label], "field_type": [None, field_type], "key": [None, key]},

    )




    if request.headers.get("x-requested-with") == "XMLHttpRequest":

        return JsonResponse({

            "ok": True,

            "field_id": field.pk,

            "label": field.label,

            "key": field.key,

            "field_type": field.field_type,

            "width": field.width,

            "required": field.required,

            "is_active": field.is_active,

            "display_order": field.display_order,

        })

    messages.success(request, f"Field '{field.label}' added.")

    return redirect("assets:category_builder", category_id=category_id)





@login_required

@superuser_required

def builder_field_edit(request, category_id, field_id):

    category = get_object_or_404(AssetCategory, pk=category_id)

    field = get_object_or_404(AssetField, pk=field_id, category=category)



    if request.method != "POST":

        return redirect("assets:category_builder", category_id=category_id)



    old = {

        "label":         field.label,

        "field_type":    field.field_type,

        "width":         field.width,

        "required":      field.required,

        "placeholder":   field.placeholder,

        "help_text":     field.help_text,

        "show_in_list":  field.show_in_list,

        "show_in_detail":field.show_in_detail,

        "show_in_add":   field.show_in_add,

        "show_in_edit":  field.show_in_edit,

    }



    _new_label = request.POST.get("label", field.label).strip() or field.label
    if _new_label != field.label:
        _reserved = reserved_field_label_problem(category.name, _new_label, _auto_key(_new_label, set()))
        if _reserved:
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"ok": False, "error": _reserved}, status=400)
            messages.error(request, _reserved)
            return redirect("assets:category_builder", category_id=category_id)
    field.label         = _new_label

    field.field_type    = request.POST.get("field_type", field.field_type)

    field.width         = int(request.POST.get("width", field.width))

    field.required      = request.POST.get("required") == "true"

    field.placeholder   = request.POST.get("placeholder", "").strip()

    field.help_text     = request.POST.get("help_text", "").strip()

    field.show_in_list  = request.POST.get("show_in_list", "true") != "false"

    field.show_in_detail= request.POST.get("show_in_detail", "true") != "false"

    field.show_in_add   = request.POST.get("show_in_add", "true") != "false"

    field.show_in_edit  = request.POST.get("show_in_edit", "true") != "false"
    ml_raw = request.POST.get("max_length", "").strip()
    field.max_length    = int(ml_raw) if ml_raw.isdigit() else None
    use_assets = False
    if field.field_type == "searchable_select" and request.POST.get("options_source") == "assets":
        cid = (request.POST.get("lookup_category") or "").strip()
        if not (cid.isdigit() and AssetCategory.objects.filter(pk=int(cid)).exists()):
            messages.error(request, "Pick a Lookup Source for the Searchable Dropdown.")
            return redirect("assets:category_builder", category_id=category_id)
        field.lookup_category_id = int(cid)
        use_assets = True
    else:
        field.lookup_category_id = None
    field.save()



    new = {

        "label":         field.label,

        "field_type":    field.field_type,

        "width":         field.width,

        "required":      field.required,

        "placeholder":   field.placeholder,

        "help_text":     field.help_text,

        "show_in_list":  field.show_in_list,

        "show_in_detail":field.show_in_detail,

        "show_in_add":   field.show_in_add,

        "show_in_edit":  field.show_in_edit,

    }

    diff = {k: [old[k], new[k]] for k in old if old[k] != new[k]}

    if diff:

        _log_config_change(request.user, "update", "AssetField", field.pk,

                           f"{category.name}: {field.label} ({field.key})", diff)



    if field.field_type in ("select", "searchable_select") and not use_assets:

        options_raw = request.POST.get("options", "")

        if options_raw.strip():

            field.options.update(is_active=False)

            for idx, opt_label in enumerate(options_raw.splitlines()):

                opt_label = opt_label.strip()

                if not opt_label:

                    continue

                existing = field.options.filter(label=opt_label).first()

                if existing:

                    existing.is_active = True

                    existing.display_order = idx

                    existing.save()

                else:

                    AssetFieldOption.objects.create(

                        field=field, label=opt_label, value=opt_label,

                        display_order=idx, is_active=True,

                    )




    if request.headers.get("x-requested-with") == "XMLHttpRequest":

        return JsonResponse({"ok": True, "label": field.label, "field_id": field.pk})

    messages.success(request, f"Field '{field.label}' updated.")

    return redirect("assets:category_builder", category_id=category_id)





@login_required

@superuser_required

def builder_field_deactivate(request, category_id, field_id):

    category = get_object_or_404(AssetCategory, pk=category_id)

    field = get_object_or_404(AssetField, pk=field_id, category=category)



    if request.method != "POST":

        return redirect("assets:category_builder", category_id=category_id)



    field.is_active = False

    field.save()

    _log_config_change(

        request.user, "deactivate", "AssetField", field.pk,

        f"{category.name}: {field.label} ({field.key})",

    )



    if request.headers.get("x-requested-with") == "XMLHttpRequest":

        return JsonResponse({"ok": True})

    messages.success(request, f"Field '{field.label}' deactivated. Existing asset data is preserved.")

    return redirect("assets:category_builder", category_id=category_id)


@login_required
@superuser_required
def builder_field_reactivate(request, category_id, field_id):
    category = get_object_or_404(AssetCategory, pk=category_id)
    field = get_object_or_404(AssetField, pk=field_id, category=category)

    if request.method != "POST":
        return redirect("assets:category_builder", category_id=category_id)

    field.is_active = True
    field.save()
    _log_config_change(
        request.user, "reactivate", "AssetField", field.pk,
        f"{category.name}: {field.label} ({field.key})",
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    messages.success(request, f"Field '{field.label}' reactivated.")
    return redirect("assets:category_builder", category_id=category_id)


@login_required
@superuser_required
def builder_field_delete(request, category_id, field_id):
    """Permanently removes the field definition. Only allowed while the
    field is inactive, as a safety rail against accidental data loss on a
    field still in use. Any stored values under this field's key are left
    untouched inside each Asset's extra_details JSON (simply orphaned,
    never displayed again) rather than being scrubbed out."""
    category = get_object_or_404(AssetCategory, pk=category_id)
    field = get_object_or_404(AssetField, pk=field_id, category=category)

    if request.method != "POST":
        return redirect("assets:category_builder", category_id=category_id)

    if field.is_active:
        message = f"Deactivate '{field.label}' before deleting it."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": message}, status=400)
        messages.error(request, message)
        return redirect("assets:category_builder", category_id=category_id)

    label, key = field.label, field.key
    field.delete()
    _log_config_change(
        request.user, "delete", "AssetField", field_id,
        f"{category.name}: {label} ({key})",
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    messages.success(request, f"Field '{label}' permanently deleted.")
    return redirect("assets:category_builder", category_id=category_id)





@login_required

@superuser_required

def builder_field_reorder(request, category_id):

    if request.method != "POST":

        return JsonResponse({"ok": False, "error": "POST required."}, status=405)



    category = get_object_or_404(AssetCategory, pk=category_id)

    try:

        data = json.loads(request.body)

        order_list = data.get("order", [])

    except (json.JSONDecodeError, AttributeError):

        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)



    for idx, fid in enumerate(order_list):

        AssetField.objects.filter(pk=fid, category=category).update(display_order=idx * 10)



    _log_config_change(

        request.user, "update", "AssetField", None,

        f"{category.name}: field order changed",

        {"order": [None, order_list]},

    )

    return JsonResponse({"ok": True})





# ---------------------------------------------------------------------------
# Workstation Field Builder — deliberately separate from category_builder()
# above. Workstation is not an AssetCategory as far as any admin-facing
# screen is concerned, so it gets its own builder screen/URL instead of
# reusing the category one keyed by category_id.
# ---------------------------------------------------------------------------

@login_required
@superuser_required
def workstation_builder(request):
    fields = WorkstationField.objects.all().order_by("display_order", "id")
    for f in fields:
        # Pre-formatted for the Edit modal's "Dropdown Options (one per
        # line)" textarea — see showEditModal() in the template, which
        # otherwise has no way to know a field's existing options.
        f.options_text = "\n".join(
            f.options.filter(is_active=True).order_by("display_order").values_list("label", flat=True)
        )
    return render(request, "assets/workstation_builder.html", {
        "fields": fields,
        "category_choices": AssetCategory.objects.exclude(
            name__iexact=WORKSTATION_CATEGORY_NAME
        ).order_by("name"),
    })


@login_required
@superuser_required
def workstation_field_add(request):
    if request.method != "POST":
        return redirect("assets:workstation_builder")

    label = (request.POST.get("label") or "").strip()
    if not label:
        messages.error(request, "Field label is required.")
        return redirect("assets:workstation_builder")

    field_type = request.POST.get("field_type", "text")
    if field_type == "searchable_select" and request.POST.get("options_source") == "assets":
        field_type = "lookup"   # Searchable Dropdown + Lookup Source -> stored as a Lookup field
    lookup_category = (request.POST.get("lookup_category") or "").strip()
    if field_type == "lookup" and not lookup_category:
        messages.error(request, "Pick a Lookup Source for the Searchable Dropdown.")
        return redirect("assets:workstation_builder")

    existing_keys = set(WorkstationField.objects.values_list("key", flat=True))
    key = _auto_key(label, existing_keys)
    max_order = WorkstationField.objects.aggregate(m=Max("display_order"))["m"] or 0

    field = WorkstationField.objects.create(
        label=label,
        key=key,
        field_type=field_type,
        width=int(request.POST.get("width", 6)),
        required=request.POST.get("required") == "true",
        placeholder=request.POST.get("placeholder", ""),
        help_text=request.POST.get("help_text", ""),
        lookup_category_id=lookup_category if field_type == "lookup" else None,
        lookup_multi=(request.POST.get("lookup_multi") == "true") if field_type == "lookup" else False,
        display_order=max_order + 10,
        max_length=int(request.POST.get("max_length").strip()) if request.POST.get("max_length", "").strip().isdigit() else None,
    )

    if field_type in ("select", "searchable_select"):
        for i, opt_label in enumerate(request.POST.get("options", "").splitlines()):
            opt_label = opt_label.strip()
            if opt_label:
                WorkstationFieldOption.objects.create(field=field, label=opt_label, display_order=i * 10)

    _log_config_change(request.user, "create", "WorkstationField", field.pk, f"{field.label} ({field.key})")
    messages.success(request, f"Added field \"{field.label}\".")
    return redirect("assets:workstation_builder")


@login_required
@superuser_required
def workstation_field_edit(request, field_id):
    field = get_object_or_404(WorkstationField, pk=field_id)
    if request.method != "POST":
        return redirect("assets:workstation_builder")

    old = {
        "label": field.label, "field_type": field.field_type, "width": field.width,
        "required": field.required, "placeholder": field.placeholder,
        "help_text": field.help_text, "lookup_category": field.lookup_category_id,
        "lookup_multi": field.lookup_multi,
    }

    field.label = (request.POST.get("label") or field.label).strip()
    field.field_type = request.POST.get("field_type", field.field_type)
    if field.field_type == "searchable_select" and request.POST.get("options_source") == "assets":
        field.field_type = "lookup"   # Searchable Dropdown + Lookup Source -> stored as a Lookup field
    field.width = int(request.POST.get("width", field.width))
    field.required = request.POST.get("required") == "true"
    field.placeholder = request.POST.get("placeholder", "")
    field.help_text = request.POST.get("help_text", "")
    field.show_in_list = request.POST.get("show_in_list", "true") == "true"
    field.show_in_detail = request.POST.get("show_in_detail", "true") == "true"
    field.show_in_add = request.POST.get("show_in_add", "true") == "true"
    field.show_in_edit = request.POST.get("show_in_edit", "true") == "true"
    if field.field_type == "lookup":
        lookup_category = (request.POST.get("lookup_category") or "").strip()
        if not lookup_category:
            messages.error(request, "Pick a Lookup Source for the Searchable Dropdown.")
            return redirect("assets:workstation_builder")
        field.lookup_category_id = lookup_category
        field.lookup_multi = request.POST.get("lookup_multi") == "true"
    else:
        field.lookup_category_id = None
        field.lookup_multi = False

    ml_raw = request.POST.get("max_length", "").strip()
    field.max_length = int(ml_raw) if ml_raw.isdigit() else None
    field.save()

    if field.field_type in ("select", "searchable_select"):
        field.options.all().delete()
        for i, opt_label in enumerate(request.POST.get("options", "").splitlines()):
            opt_label = opt_label.strip()
            if opt_label:
                WorkstationFieldOption.objects.create(field=field, label=opt_label, display_order=i * 10)

    new = {
        "label": field.label, "field_type": field.field_type, "width": field.width,
        "required": field.required, "placeholder": field.placeholder,
        "help_text": field.help_text, "lookup_category": field.lookup_category_id,
        "lookup_multi": field.lookup_multi,
    }
    diff = {k: [old[k], new[k]] for k in old if old[k] != new[k]}
    _log_config_change(request.user, "update", "WorkstationField", field.pk, field.label, diff)
    messages.success(request, f"Updated \"{field.label}\".")
    return redirect("assets:workstation_builder")


@login_required
@superuser_required
def workstation_field_deactivate(request, field_id):
    field = get_object_or_404(WorkstationField, pk=field_id)
    if request.method != "POST":
        return redirect("assets:workstation_builder")

    field.is_active = False
    field.save()
    _log_config_change(request.user, "deactivate", "WorkstationField", field.pk,
                        f"{field.label} ({field.key})")

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    messages.success(request, f"Deactivated \"{field.label}\". Existing Workstation data is not deleted.")
    return redirect("assets:workstation_builder")


@login_required
@superuser_required
def workstation_field_reactivate(request, field_id):
    field = get_object_or_404(WorkstationField, pk=field_id)
    if request.method != "POST":
        return redirect("assets:workstation_builder")

    field.is_active = True
    field.save()
    _log_config_change(request.user, "reactivate", "WorkstationField", field.pk,
                        f"{field.label} ({field.key})")

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    messages.success(request, f"Reactivated \"{field.label}\".")
    return redirect("assets:workstation_builder")


@login_required
@superuser_required
def workstation_field_delete(request, field_id):
    """Permanently removes the field definition. Only allowed while the
    field is inactive. Existing Workstation data under this key is left
    as-is inside each asset's extra_details JSON (orphaned, not shown)."""
    field = get_object_or_404(WorkstationField, pk=field_id)
    if request.method != "POST":
        return redirect("assets:workstation_builder")

    if field.is_active:
        message = f"Deactivate '{field.label}' before deleting it."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": message}, status=400)
        messages.error(request, message)
        return redirect("assets:workstation_builder")

    label, key = field.label, field.key
    field.delete()
    _log_config_change(request.user, "delete", "WorkstationField", field_id,
                        f"{label} ({key})")

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    messages.success(request, f"Field '{label}' permanently deleted.")
    return redirect("assets:workstation_builder")


@login_required
@superuser_required
def workstation_field_reorder(request):
    """AJAX: persists new field order after a drag-and-drop reorder.
    Same contract as the existing category-fields reorder endpoint."""
    if request.method != "POST":
        return JsonResponse({"ok": False}, status=405)
    order = json.loads(request.body or "{}").get("order", [])
    for idx, fid in enumerate(order):
        WorkstationField.objects.filter(pk=fid).update(display_order=idx * 10)
    _log_config_change(request.user, "update", "WorkstationField", None, "Reordered fields")
    return JsonResponse({"ok": True})


@login_required
@superuser_required
def config_audit_log(request):



    logs = ConfigAuditLog.objects.select_related("changed_by").order_by("-changed_at")[:500]



    allow_clear_all = bool(getattr(settings, "ALLOW_CLEAR_ALL", True) and request.user.is_superuser)
    return render(request, "assets/config_audit_log.html", {"logs": logs, "allow_clear_all": allow_clear_all})


@login_required
@superuser_required
def config_audit_log_clear(request):
    if not getattr(settings, "ALLOW_CLEAR_ALL", True):
        messages.error(request, "Clearing the audit log is disabled in this environment.")
        return redirect("assets:config_audit_log")
    if request.method != "POST":
        return redirect("assets:config_audit_log")
    if not request.user.check_password(request.POST.get("admin_password", "")):
        messages.error(request, "Incorrect administrator password. Audit log was not cleared.")
        return redirect("assets:config_audit_log")
    with transaction.atomic():
        deleted = ConfigAuditLog.objects.count()
        ConfigAuditLog.objects.all().delete()
        # Leave one entry behind so it is always visible who wiped the log.
        _log_config_change(request.user, "delete", "ConfigAuditLog", None,
                           f"Cleared {deleted} audit log entries")
    messages.success(request, f"Cleared {deleted} audit log entries.")
    return redirect("assets:config_audit_log")



