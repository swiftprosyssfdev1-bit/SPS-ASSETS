"""
Branch-based access control.

Two levels of admin:
  * Super Admin  -> a Django superuser (user.is_superuser). Full access to
                    every branch, branch/admin management, and Import.
  * Branch Admin -> a regular staff user with a UserBranchAccess row listing
                    the branch(es) they may see. No profile = no branches.

Every asset-affecting view MUST go through can_access_branch() /
get_accessible_branches() rather than trusting the frontend. This module
is the single place that decision is made so it can't drift out of sync
between views.
"""

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from .models import Branch

SESSION_BRANCH_KEY = "active_branch_id"


def is_super_admin(user):
    return bool(user.is_authenticated and user.is_superuser)


def is_branch_admin(user):
    """True for a logged-in, non-superuser staff account — regardless of
    whether they have any branches assigned yet."""
    return bool(user.is_authenticated and not user.is_superuser and user.is_staff)


super_admin_required = user_passes_test(is_super_admin, login_url="assets:dashboard")

# Any authenticated staff user (Super Admin or Branch Admin). Plain
# @login_required already covers "logged in"; this is for pages that must
# additionally be staff (mirrors the old admin_required in views.py).
admin_required = user_passes_test(
    lambda u: u.is_authenticated and (u.is_staff or u.is_superuser),
    login_url="assets:dashboard",
)


def get_accessible_branches(user):
    """QuerySet of every Branch this user is allowed to see. Super Admin
    gets all active branches; a Branch Admin gets only the ones assigned
    to their profile; anyone else gets none."""
    if is_super_admin(user):
        return Branch.objects.filter(status=True)
    profile = getattr(user, "branch_access", None)
    if profile is None:
        return Branch.objects.none()
    return profile.branches.filter(status=True)


def can_access_branch(user, branch):
    """branch may be a Branch instance, an id, or None."""
    if branch is None:
        # Legacy, not-yet-assigned assets: visible to Super Admin only,
        # so they're the one who assigns a real branch to them.
        return is_super_admin(user)
    if is_super_admin(user):
        return True
    branch_id = branch.pk if isinstance(branch, Branch) else branch
    return get_accessible_branches(user).filter(pk=branch_id).exists()


def require_branch_access(user, branch):
    """Raise PermissionDenied (-> Django's 403 page) if `user` may not
    touch `branch`. Call this at the top of every create/update/delete/
    view handler that takes a branch, in addition to any UI-level hiding —
    the UI hiding is just convenience, this is the real enforcement."""
    if not can_access_branch(user, branch):
        raise PermissionDenied("You do not have access to this branch.")


def get_active_branch_id(request):
    """The branch currently selected via the dropdown (session-backed).
    Returns None for 'All Branches' (Super Admin only)."""
    raw = request.session.get(SESSION_BRANCH_KEY)
    return int(raw) if raw not in (None, "", "all") else None


def set_active_branch(request, branch_id):
    request.session[SESSION_BRANCH_KEY] = branch_id if branch_id else "all"


def resolve_selected_branch(request):
    """Reads ?branch=<id|all> if present (and remembers it in the session),
    otherwise falls back to whatever is already in the session. Returns
    (branch_or_none, accessible_qs). A Branch Admin can never resolve to
    'all' — they're pinned to their own accessible set."""
    accessible = get_accessible_branches(request.user)
    param = request.GET.get("branch")
    if param is not None:
        set_active_branch(request, param)

    if is_super_admin(request.user):
        branch_id = get_active_branch_id(request)
        if branch_id is None:
            return None, accessible  # "All Branches"
        branch = accessible.filter(pk=branch_id).first()
        return branch, accessible
    else:
        branch_id = get_active_branch_id(request)
        branch = accessible.filter(pk=branch_id).first() if branch_id else None
        if branch is None:
            branch = accessible.first()
        return branch, accessible


def filter_assets_to_branch(qs, branch, accessible_branches, include_unassigned=False):
    """branch=None means 'All Branches' — only meaningful for a Super
    Admin, since resolve_selected_branch() never returns None for anyone
    else. Always constrains to `accessible_branches` too, so this is safe
    to call even if a caller forgets to check who's asking.

    include_unassigned: when True (pass this only for a Super Admin
    viewing 'All Branches'), also include legacy rows with branch=None —
    otherwise those pre-branch records become invisible to everyone,
    including the one person who's supposed to assign them a branch.
    """
    if branch is not None:
        return qs.filter(branch=branch)
    if include_unassigned:
        return qs.filter(Q(branch__in=accessible_branches) | Q(branch__isnull=True))
    return qs.filter(branch__in=accessible_branches)
