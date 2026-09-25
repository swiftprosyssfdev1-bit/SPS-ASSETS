from django import forms
from django.contrib.auth import get_user_model

from .models import Asset, AssetCategory, Branch

User = get_user_model()


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            "category", "branch", "asset_tag", "name", "brand", "model_number", "serial_number",
            "status", "current_assigned_to", "current_location", "linked_workstation",
            "purchase_date", "last_service_date", "notes", "is_active",
        ]
        widgets = {
            "category": forms.Select(attrs={"class": "form-select"}),
            "branch": forms.Select(attrs={"class": "form-select"}),
            "asset_tag": forms.TextInput(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "brand": forms.TextInput(attrs={"class": "form-control"}),
            "model_number": forms.TextInput(attrs={"class": "form-control"}),
            "serial_number": forms.TextInput(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "current_assigned_to": forms.TextInput(attrs={"class": "form-control"}),
            "current_location": forms.TextInput(attrs={"class": "form-control"}),
            "linked_workstation": forms.TextInput(attrs={"class": "form-control"}),
            "purchase_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "last_service_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, accessible_branches=None, **kwargs):
        super().__init__(*args, **kwargs)
        if accessible_branches is not None:
            self.fields["branch"].queryset = accessible_branches
        self.fields["branch"].required = True
        self.fields["name"].required = False

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get("name")
        category = cleaned_data.get("category")
        brand = cleaned_data.get("brand")
        model_number = cleaned_data.get("model_number")
        asset_tag = cleaned_data.get("asset_tag")
        if not name or not name.strip():
            if brand:
                cleaned_data["name"] = f"{brand} {model_number or ''}".strip() or brand
            elif category and asset_tag:
                cleaned_data["name"] = f"{category.name} {asset_tag}"
            elif category:
                cleaned_data["name"] = f"{category.name}"
            else:
                cleaned_data["name"] = "Asset"
        return cleaned_data

    def clean_branch(self):
        branch = self.cleaned_data.get("branch")
        if branch is None:
            raise forms.ValidationError("Please select a branch.")
        return branch


class AssetCategoryForm(forms.ModelForm):
    class Meta:
        model = AssetCategory
        fields = ["name", "icon", "description", "show_under_other"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "icon": forms.TextInput(attrs={"class": "form-control", "placeholder": "bi-hdd-stack"}),
            "description": forms.TextInput(attrs={"class": "form-control"}),
            "show_under_other": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class BranchForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = ["name", "code", "status"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Coimbatore"}),
            "code": forms.TextInput(attrs={"class": "form-control", "placeholder": "Auto-filled if left blank"}),
            "status": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {"status": "Active (uncheck to retire this branch without deleting it)"}


class BranchAdminCreateForm(forms.Form):
    """Super Admin: create a new Branch Admin login and assign branches in
    one step."""
    username = forms.CharField(
        max_length=150, label="Username",
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "username"}),
    )
    email = forms.EmailField(
        required=False, label="Email (optional)",
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    password = forms.CharField(
        label="Temporary password",
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
        min_length=8,
    )
    branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.filter(status=True),
        widget=forms.CheckboxSelectMultiple,
        label="Branches this admin can manage",
    )
    is_active = forms.BooleanField(required=False, initial=True, label="Account active")

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username


class BranchAdminEditForm(forms.Form):
    """Super Admin: change an existing Branch Admin's username, branch
    assignments, active status, and (optionally) reset their password."""
    username = forms.CharField(
        max_length=150, label="Username",
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "username"}),
    )
    email = forms.EmailField(
        required=False, label="Email",
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    new_password = forms.CharField(
        required=False, label="Reset password (leave blank to keep current)",
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
        min_length=8,
    )
    branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.filter(status=True),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Branches this admin can manage",
    )
    is_active = forms.BooleanField(required=False, label="Account active")

    def __init__(self, *args, admin_user=None, **kwargs):
        self._admin_user = admin_user
        super().__init__(*args, **kwargs)

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        existing = User.objects.filter(username__iexact=username)
        if self._admin_user is not None:
            existing = existing.exclude(pk=self._admin_user.pk)
        if existing.exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username


class AccountSettingsForm(forms.Form):
    """Super Admin only: change their own username, email, and/or login
    password. Changing either the username or the password requires
    confirming the current password."""
    username = forms.CharField(
        max_length=150, label="Username",
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "username"}),
    )
    email = forms.EmailField(
        required=False, label="Email",
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    new_password = forms.CharField(
        required=False, label="New password (leave blank to keep current)",
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
        min_length=8,
    )
    confirm_new_password = forms.CharField(
        required=False, label="Confirm new password",
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )
    current_password = forms.CharField(
        required=False, label="Current password",
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "current-password"}),
        help_text="Required to change your username or set a new password.",
    )

    def __init__(self, *args, user=None, **kwargs):
        self._user = user
        super().__init__(*args, **kwargs)

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        existing = User.objects.filter(username__iexact=username)
        if self._user is not None:
            existing = existing.exclude(pk=self._user.pk)
        if existing.exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username

    def clean(self):
        cleaned = super().clean()
        new_password = cleaned.get("new_password")
        confirm_password = cleaned.get("confirm_new_password")
        current_password = cleaned.get("current_password")
        username_changed = (
            self._user is not None
            and cleaned.get("username")
            and cleaned["username"] != self._user.username
        )

        if new_password or confirm_password:
            if new_password != confirm_password:
                self.add_error("confirm_new_password", "The two passwords don't match.")

        if (new_password or username_changed) and not current_password:
            self.add_error("current_password", "Enter your current password to confirm this change.")
        elif current_password and self._user is not None and not self._user.check_password(current_password):
            self.add_error("current_password", "Current password is incorrect.")

        return cleaned


class ExportPasswordForm(forms.Form):
    """Super Admin: set or reset the password that locks Excel exports."""
    password1 = forms.CharField(
        label="New password", min_length=8,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
        help_text="At least 8 characters.",
    )
    password2 = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords don't match.")
        return cleaned
