from django import forms
from django.contrib.auth import get_user_model

from .models import Asset, AssetCategory, Branch
from .naming import refreshed_name

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

    def __init__(self, *args, accessible_branches=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Workstation is its own module now (see views.workstation_list) —
        # it should not be offered as a category when creating or editing
        # a normal asset. The one exception: an asset that is ALREADY a
        # Workstation record (one of the 45 legacy rows) must keep showing
        # its current category in the dropdown so re-saving that asset
        # (e.g. to edit its notes) doesn't fail validation.
        category_qs = AssetCategory.objects.exclude(name__iexact="Workstation")
        if self.instance is not None and self.instance.pk and self.instance.category_id:
            if self.instance.category.name.strip().lower() == "workstation":
                category_qs = AssetCategory.objects.all()
        self.fields["category"].queryset = category_qs
        if accessible_branches is not None:
            self.fields["branch"].queryset = accessible_branches
        self.fields["branch"].required = True
        self.fields["name"].required = False
        self._user = user
        # Only the Super Admin may flip a deactivated asset back to active;
        # everyone else keeps the field read-only when it's already off.
        editing_inactive_asset = self.instance.pk and not self.instance.is_active
        if editing_inactive_asset and not (user is not None and user.is_superuser):
            self.fields["is_active"].disabled = True

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get("name")
        category = cleaned_data.get("category")
        brand = cleaned_data.get("brand")
        model_number = cleaned_data.get("model_number")
        asset_tag = cleaned_data.get("asset_tag")
        # Names are generated automatically (see naming.py): a blank name is
        # generated, and a name that is still the auto-generated one follows
        # the record when its Tag / Brand / Model / Category is edited. At this
        # point self.instance still holds the SAVED values (the form copies the
        # submitted ones onto it later), so it gives the "before".
        new_inputs = (category.name if category else "", asset_tag, brand, model_number)
        old_inputs = new_inputs
        if self.instance.pk and self.instance.category_id:
            inst = self.instance
            old_inputs = (inst.category.name, inst.asset_tag, inst.brand, inst.model_number)
        cleaned_data["name"] = refreshed_name(name, old_inputs, new_inputs)

        # Core columns (Brand, Model ...) the Category Builder made a Dropdown:
        # only its options (or the value already saved) may be submitted.
        if category is not None:
            from .category_fields import builder_core_options
            for col, info in builder_core_options(category).items():
                value = (cleaned_data.get(col) or "").strip()
                if not value:
                    continue
                allowed = {str(o).strip().lower(): o for o in info["options"]}
                if value.lower() in allowed:
                    cleaned_data[col] = allowed[value.lower()]
                elif not (self.instance.pk and value == (getattr(self.instance, col, "") or "")):
                    self.add_error(col, f"Choose one of the {info['label']} options.")

        was_inactive = self.instance.pk and not self.instance.is_active
        is_superuser = bool(self._user is not None and self._user.is_superuser)
        if was_inactive and cleaned_data.get("is_active") and not is_superuser:
            self.add_error("is_active", "Only the Super Admin can reactivate a deactivated asset.")
        return cleaned_data

    def clean_status(self):
        # The status dropdown choices are injected at request time by
        # _status_form_setup() and may include custom Category Builder
        # statuses (e.g. "Resigned") that are not in STATUS_CHOICES.
        # Return the raw submitted value to skip widget-level choice validation.
        return self.data.get("status", "")

    def clean_branch(self):
        branch = self.cleaned_data.get("branch")
        if branch is None:
            raise forms.ValidationError("Please select a branch.")
        return branch


class AssetCategoryForm(forms.ModelForm):
    def __init__(self, *args, lock_name=False, reserved_names=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._reserved_names = {str(n).strip().lower() for n in (reserved_names or [])}
        if lock_name:
            # built-in category: name is used by code, so it stays fixed
            self.fields["name"].disabled = True

    class Meta:
        model = AssetCategory
        fields = ["name", "icon", "description", "show_under_other"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "icon": forms.TextInput(attrs={"class": "form-control", "placeholder": "bi-hdd-stack"}),
            "description": forms.TextInput(attrs={"class": "form-control"}),
            "show_under_other": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_name(self):
        name = self.cleaned_data["name"]
        if name.strip().lower() == "workstation":
            raise forms.ValidationError(
                "\"Workstation\" is reserved for the dedicated Workstation module "
                "and can't be created as a normal Asset Category."
            )
        if (self.instance.pk and name.strip().lower() != self.instance.name.strip().lower()
                and name.strip().lower() in self._reserved_names):
            raise forms.ValidationError(
                "That name belongs to a built-in category and can't be used."
            )
        return name


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
