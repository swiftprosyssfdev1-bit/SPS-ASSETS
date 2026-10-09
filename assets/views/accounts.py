"""Branches, branch admins, my-account and export-password settings."""
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from ..excel_security import (
    disable_export_password,
    export_password_status,
    set_export_password,
)
from ..forms import (
    AccountSettingsForm,
    BranchAdminCreateForm,
    BranchAdminEditForm,
    BranchForm,
)
from ..models import Branch, UserBranchAccess
from .common import (
    User,
    superuser_required,
)


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


@login_required
@superuser_required
def export_password_settings(request):
    """Super Admin only: set or reset the password on downloaded Excel
    exports. The current password is never displayed."""
    from ..forms import ExportPasswordForm

    if request.method == "POST" and request.POST.get("action") == "remove":
        if request.POST.get("confirm_remove") != "yes":
            messages.error(request, "Tick the confirmation box to remove the export password.")
        else:
            disable_export_password(user=request.user)
            messages.success(request, "Export password removed. Excel exports now open without a password.")
        return redirect("assets:export_password")
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
        "form": form, "is_set": is_set, "source": source, "is_disabled": source == "disabled",
        "updated_at": updated_at, "updated_by": updated_by,
    })
