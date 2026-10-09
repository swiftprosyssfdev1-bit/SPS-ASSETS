"""Workstation module: list, add, edit, view and rename."""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from ..context_processors import workstation_labels
from ..import_utils import (
    resolve_pending_workstation_lookups,
)
from ..models import (
    Asset,
    AssetCategory,
    Branch,
    Workstation,
    WorkstationField,
    WorkstationFieldOption,
)
from ..naming import generate_asset_name
from ..permissions import (
    get_accessible_branches,
    require_branch_access,
    resolve_selected_branch,
)
from .assets import (
    _resolve_back_link,
    category_detail,
)
from .common import (
    WORKSTATION_CATEGORY_NAME,
    _log_config_change,
    superuser_required,
)


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
def workstation_rename(request):
    """Rename the Workstation module's DISPLAY name only (nav, dashboard,
    list title, buttons). The internal name stays "workstation"."""
    from ..models import SiteSetting
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
