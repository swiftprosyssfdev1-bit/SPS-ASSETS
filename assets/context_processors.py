from django.conf import settings
from .permissions import is_super_admin, resolve_selected_branch


def workstation_labels():
    """(singular, plural) display name of the Workstation module: value saved
    via the Rename button, else settings.WORKSTATION_LABEL(_PLURAL), else
    "Workstation". Display only — the internal name never changes."""
    label = getattr(settings, "WORKSTATION_LABEL", "Workstation")
    plural = getattr(settings, "WORKSTATION_LABEL_PLURAL", "")
    try:
        from .models import SiteSetting
        saved = dict(SiteSetting.objects.filter(
            key__in=["workstation_label", "workstation_label_plural"]
        ).values_list("key", "value"))
        if saved.get("workstation_label", "").strip():
            label = saved["workstation_label"].strip()
            plural = saved.get("workstation_label_plural", "").strip()
    except Exception:
        pass  # table not migrated yet -> fall back to settings/default
    return label, (plural or label + "s")


def branch_context(request):
    """Available on every template via base.html's nav (branch switcher,
    Manage menu). Views that need the fully-resolved branch for filtering
    still call resolve_selected_branch() themselves — this just avoids
    every single view having to also pass it purely for the navbar."""
    # Display name of the Workstation module. Change WORKSTATION_LABEL (and
    # optionally WORKSTATION_LABEL_PLURAL) in settings.py; the internal name
    # stays "workstation", so nothing else is affected.
    _label, _plural = workstation_labels()
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {"ws_label": _label, "ws_label_plural": _plural}
    selected_branch, accessible_branches = resolve_selected_branch(request)
    return {
        "is_super_admin": is_super_admin(request.user),
        "accessible_branches": accessible_branches,
        "selected_branch": selected_branch,
        "ws_label": _label,
        "ws_label_plural": _plural,
    }
