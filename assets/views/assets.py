"""Asset categories and individual assets: list, add, edit, view, delete, deactivate."""
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from ..category_fields import (
    _norm_key,
    builder_status_choices,
    get_builder_layout,
    get_category_fields,
    get_list_display_fields,
    rekey_extra,
    resolve_field_value,
    status_driver_column,
    workstation_lookup_category,
)
from ..context_processors import workstation_labels
from ..display import (
    build_detail_fields,
    build_list_cells,
    build_list_columns,
    detail_heading_shows_name,
)
from ..forms import (
    AssetCategoryForm,
    AssetForm,
)
from ..import_utils import (
    _normalize_header,
)
from ..models import (
    Asset,
    AssetCategory,
    AssetField,
    AssetHistory,
    WorkstationField,
    prime_display_tags,
)
from ..naming import generate_asset_name
from ..permissions import (
    can_access_branch,
    filter_assets_to_branch,
    get_accessible_branches,
    is_super_admin,
    require_branch_access,
    resolve_selected_branch,
)
from .common import (
    INFO_REGISTER_CATEGORIES,
    NO_REAL_NAME_CATEGORIES,
    TAG_LABEL_OVERRIDES,
    _log_config_change,
    builtin_category_names,
    is_workstation_category,
    order_branch_wise,
    superuser_required,
)


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
    from ..import_utils import HEADER_ALIASES, SHEET_HEADER_OVERRIDES, _normalize_header
    from ..sheet_templates import CATEGORY_TO_TEMPLATE

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
        from ..import_utils import (
            HEADER_ALIASES,
            LABEL_TO_FIELD,
            SHEET_HEADER_OVERRIDES,
            _normalize_header,
        )
        from ..sheet_templates import CATEGORY_TO_TEMPLATE
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
        from ..relations import resolve_asset_reference
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

    from django.core.exceptions import ValidationError
    from django.core.validators import URLValidator, validate_email
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
    # Max Length set in the Category Builder for fields bound to a built-in
    # column (Tag, Name, Brand, Model, Serial ...). These are not in
    # extra_fields, so they need their own check.
    for col, meta in (config.get("core_meta") or {}).items():
        ml = meta.get("max_length")
        val = form.cleaned_data.get(col)
        if ml and isinstance(val, str) and len(val.strip()) > ml:
            form.add_error(None, f"{meta.get('label') or col} cannot exceed {ml} characters.")
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
    from ..category_fields import (
        get_builder_layout,
        get_category_field_labels,
        get_category_fields,
        get_common_fields,
        workstation_lookup_category,
    )
    from ..import_utils import (
        HEADER_ALIASES,
        LABEL_TO_FIELD,
        SHEET_HEADER_OVERRIDES,
        _normalize_header,
    )
    from ..sheet_templates import CATEGORY_TO_TEMPLATE

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

    from django.urls import Resolver404, resolve
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

    from ..relations import get_forward_relationships, get_reverse_relationships
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
    is_builtin = name.strip().lower() in builtin_category_names()
    if name.strip().lower() == "workstation":
        # Workstation is its own module (not managed through categories).
        problem = "Workstation is managed separately and can't be deleted here."
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
        # Built-in category: the admin must explicitly acknowledge the warning.
        if is_builtin and request.POST.get("confirm_builtin") != "yes":
            messages.error(request, "Tick the box to confirm you want to delete this built-in category.")
            return redirect("assets:category_delete", category_id=category.pk)
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
        "category": category, "problem": problem, "is_builtin": is_builtin,
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
