from .permissions import is_super_admin, resolve_selected_branch


def branch_context(request):
    """Available on every template via base.html's nav (branch switcher,
    Manage menu). Views that need the fully-resolved branch for filtering
    still call resolve_selected_branch() themselves — this just avoids
    every single view having to also pass it purely for the navbar."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    selected_branch, accessible_branches = resolve_selected_branch(request)
    return {
        "is_super_admin": is_super_admin(request.user),
        "accessible_branches": accessible_branches,
        "selected_branch": selected_branch,
    }
