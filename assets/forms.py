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
        """accessible_branches: queryset the current user is allowed to
        assign. Passed in from the view — this form never decides
        permissions on its own, it just restricts the dropdown to match
        what the view has already authorized, and re-validates it in
        clean_branch() so a tampered POST can't slip an unauthorized
        branch id past this once the choices are narrowed."""
        super().__init__(*args, **kwargs)
        if accessible_branches is not None:
            self.fields["branch"].queryset = accessible_branches
        self.fields["branch"].required = True

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
    """Super Admin: change an existing Branch Admin's branch assignments,
    active status, and (optionally) reset their password."""
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
