"""Category / Workstation field builders and the configuration audit log."""
import json
import re
import re as _re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from ..category_fields import (
    reserved_field_label_problem,
)
from ..models import (
    AssetCategory,
    AssetField,
    AssetFieldOption,
    ConfigAuditLog,
    WorkstationField,
    WorkstationFieldOption,
)
from .common import (
    WORKSTATION_CATEGORY_NAME,
    _log_config_change,
    builtin_category_names,
    superuser_required,
)


def _auto_key(label, existing_keys):

    base = _re.sub(r"[^a-z0-9]+", "_", str(label or "").lower()).strip("_")[:90] or "field"

    key = base

    n = 2

    while key in existing_keys:

        key = f"{base}_{n}"

        n += 1

    return key



@login_required

@superuser_required

def category_builder(request, category_id):

    category = get_object_or_404(AssetCategory, pk=category_id)

    if category.name.strip().lower() == "workstation":

        messages.error(request, "Workstation is managed separately and cannot be configured here.")

        return redirect("assets:dashboard")



    fields = AssetField.objects.filter(category=category).order_by("display_order", "id")

    # How many records exist, and how many actually hold a value for each field,
    # so the deactivate/delete warnings can show real numbers.
    field_list = list(fields.select_related("lookup_category"))
    record_count = category.assets.count()
    filled = {}
    for extra in category.assets.values_list("extra_details", flat=True):
        if not isinstance(extra, dict):
            continue
        for k, v in extra.items():
            if v not in (None, "", [], {}):
                filled[k] = filled.get(k, 0) + 1
    for f in field_list:
        f.filled_count = filled.get(f.key, 0)

    return render(request, "assets/category_builder.html", {

        "category": category,

        "record_count": record_count,

        "fields": field_list,
        "category_choices": AssetCategory.objects.order_by("name"),

        "is_builtin": category.name.strip().lower() in builtin_category_names(),

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


def _field_usage(category, key):
    """(records in the category, records holding a value for field `key`)."""
    record_count = category.assets.count()
    filled = 0
    for extra in category.assets.values_list("extra_details", flat=True):
        if isinstance(extra, dict) and extra.get(key) not in (None, "", [], {}):
            filled += 1
    return record_count, filled


@login_required
@superuser_required
def builder_field_delete(request, category_id, field_id):
    """Permanently removes the field definition. GET shows a confirmation page
    (with record counts and a built-in warning); POST performs the delete.
    Only allowed while the field is inactive, as a safety rail against
    accidental data loss on a field still in use. Any stored values under this
    field's key are left untouched inside each Asset's extra_details JSON
    (simply orphaned, never displayed again) rather than being scrubbed out."""
    category = get_object_or_404(AssetCategory, pk=category_id)
    field = get_object_or_404(AssetField, pk=field_id, category=category)
    is_xhr = request.headers.get("x-requested-with") == "XMLHttpRequest"

    is_builtin = category.name.strip().lower() in builtin_category_names()
    problem = f"Deactivate '{field.label}' before deleting it." if field.is_active else None
    record_count, filled_count = _field_usage(category, field.key)
    needs_confirm = bool(filled_count or is_builtin)

    if request.method == "POST":
        if problem:
            if is_xhr:
                return JsonResponse({"ok": False, "error": problem}, status=400)
            messages.error(request, problem)
            return redirect("assets:category_builder", category_id=category_id)
        if needs_confirm and not is_xhr and request.POST.get("confirm_delete_field") != "yes":
            messages.error(request, "Tick the confirmation box to delete this field.")
            return redirect("assets:builder_field_delete", category_id=category_id, field_id=field_id)

        label, key = field.label, field.key
        field.delete()
        _log_config_change(
            request.user, "delete", "AssetField", field_id,
            f"{category.name}: {label} ({key})",
        )
        if is_xhr:
            return JsonResponse({"ok": True})
        messages.success(request, f"Field '{label}' permanently deleted.")
        return redirect("assets:category_builder", category_id=category_id)

    return render(request, "assets/field_confirm_delete.html", {
        "category": category, "field": field, "problem": problem,
        "is_builtin": is_builtin, "record_count": record_count,
        "filled_count": filled_count, "needs_confirm": needs_confirm,
    })





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



    return render(request, "assets/config_audit_log.html", {"logs": logs, "allow_delete_all": request.user.is_superuser})


@login_required
@superuser_required
def config_audit_log_clear(request):
    if request.method != "POST":
        return redirect("assets:config_audit_log")
    if not request.user.check_password(request.POST.get("admin_password", "")):
        messages.error(request, "Incorrect administrator password. Audit log was not deleted.")
        return redirect("assets:config_audit_log")
    with transaction.atomic():
        deleted = ConfigAuditLog.objects.count()
        ConfigAuditLog.objects.all().delete()
        # Leave one entry behind so it is always visible who wiped the log.
        _log_config_change(request.user, "delete", "ConfigAuditLog", None,
                           f"Cleared {deleted} audit log entries")
    messages.success(request, f"Deleted all {deleted} audit log entries.")
    return redirect("assets:config_audit_log")
