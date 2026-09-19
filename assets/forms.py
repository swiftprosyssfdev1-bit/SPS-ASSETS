from django import forms
from .models import Asset, AssetCategory


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            "category", "asset_tag", "name", "brand", "model_number", "serial_number",
            "status", "current_assigned_to", "current_location", "linked_workstation",
            "purchase_date", "last_service_date", "notes", "is_active",
        ]
        widgets = {
            "category": forms.Select(attrs={"class": "form-select"}),
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
