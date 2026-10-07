"""Regression tests for the five "known issues" fix pass.

1. Reserved Employee Id / Employee Name builder labels outside Employee.
2. Lookup endpoint reports the branch it was limited to (and stays scoped).
3. A category that builder fields use as their Lookup Source can't be deleted.
(4 = test_dynamic_display label fix, 5 = migration 0024 - covered by
`makemigrations --check`.)
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from assets.category_fields import reserved_field_label_problem
from assets.models import Asset, AssetCategory, AssetField, Branch

User = get_user_model()


class ReservedFieldLabelTests(TestCase):
    def test_helper(self):
        for label in ("Employee Id", "Employee ID", "Employee Name", "EmployeeName", "employee_id"):
            msg = reserved_field_label_problem("Laptop", label)
            self.assertIsNotNone(msg, label)
            self.assertIn("Assigned", msg)
        # The suggested replacement is allowed
        self.assertIsNone(reserved_field_label_problem("Laptop", "Assigned Employee ID"))
        self.assertIsNone(reserved_field_label_problem("Laptop", "Status"))
        # Employee category legitimately uses them
        self.assertIsNone(reserved_field_label_problem("Employee", "Employee Id"))
        self.assertIsNone(reserved_field_label_problem("Employee List", "Employee Name"))

    def setUp(self):
        self.user = User.objects.create_superuser("admin", "a@x.com", "pw")
        self.client.force_login(self.user)
        self.laptop, _ = AssetCategory.objects.get_or_create(name="Laptop")
        AssetField.objects.filter(category=self.laptop).delete()

    def add_url(self, cat):
        return reverse("assets:builder_field_add", args=[cat.pk])

    def test_add_rejected_in_non_employee_category(self):
        r = self.client.post(self.add_url(self.laptop), {"label": "Employee Id", "field_type": "text"},
                             HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Assigned Employee Id", r.json()["error"])
        self.assertFalse(AssetField.objects.filter(category=self.laptop).exists())

    def test_add_allowed_with_assigned_prefix(self):
        r = self.client.post(self.add_url(self.laptop), {"label": "Assigned Employee ID", "field_type": "text"},
                             HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(AssetField.objects.filter(category=self.laptop, label="Assigned Employee ID").exists())

    def test_add_allowed_in_employee_category(self):
        emp, _ = AssetCategory.objects.get_or_create(name="Employee")
        AssetField.objects.filter(category=emp).delete()
        r = self.client.post(self.add_url(emp), {"label": "Employee Id", "field_type": "text"},
                             HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.status_code, 200)

    def test_rename_to_reserved_is_rejected_but_other_edits_still_work(self):
        f = AssetField.objects.create(category=self.laptop, label="Owner", key="owner", field_type="text")
        url = reverse("assets:builder_field_edit", args=[self.laptop.pk, f.pk])
        r = self.client.post(url, {"label": "Employee Name", "field_type": "text", "width": 6},
                             HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.status_code, 400)
        f.refresh_from_db()
        self.assertEqual(f.label, "Owner")
        # an existing field that already has a reserved label can still be edited
        # (same label) - e.g. toggling width - so old data isn't locked
        legacy = AssetField.objects.create(category=self.laptop, label="Employee Id", key="employee_id", field_type="text")
        url = reverse("assets:builder_field_edit", args=[self.laptop.pk, legacy.pk])
        self.client.post(url, {"label": "Employee Id", "field_type": "text", "width": 12})
        legacy.refresh_from_db()
        self.assertEqual(legacy.width, 12)


class LookupBranchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("admin", "a@x.com", "pw")
        self.client.force_login(self.user)
        self.chn = Branch.objects.get_or_create(name="Chennai")[0]
        self.mdu = Branch.objects.get_or_create(name="Madurai")[0]
        self.emp, _ = AssetCategory.objects.get_or_create(name="Employee")
        Asset.objects.filter(category=self.emp).delete()
        Asset.objects.create(category=self.emp, asset_tag="E1", name="Asha", branch=self.chn)
        Asset.objects.create(category=self.emp, asset_tag="E2", name="Bala", branch=self.mdu)

    def get(self, **params):
        return self.client.get(reverse("assets:asset_lookup"), params).json()

    def test_scoped_to_selected_branch_and_names_it(self):
        data = self.get(category="Employee", branch=str(self.chn.pk))
        self.assertEqual([r["tag"] for r in data["results"]], ["E1"])
        self.assertEqual(data["branch"], "Chennai")

    def test_no_branch_selected_lists_all_and_names_none(self):
        data = self.get(category="Employee", branch="")
        self.assertEqual({r["tag"] for r in data["results"]}, {"E1", "E2"})
        self.assertEqual(data["branch"], "")


class DeleteLookupSourceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("admin", "a@x.com", "pw")
        self.client.force_login(self.user)
        self.src = AssetCategory.objects.create(name="Zzz Lookup Source")
        self.host, _ = AssetCategory.objects.get_or_create(name="Laptop")
        AssetField.objects.filter(category=self.host).delete()

    def link(self, **kw):
        return AssetField.objects.create(
            category=self.host, label="Assigned Employee ID", key="assigned_employee_id",
            field_type="searchable_select", lookup_category=self.src, **kw)

    def test_blocked_while_an_active_field_uses_it(self):
        self.link()
        url = reverse("assets:category_delete", args=[self.src.pk])
        self.assertIn("Assigned Employee ID", self.client.get(url).context["problem"])
        self.client.post(url)
        self.assertTrue(AssetCategory.objects.filter(pk=self.src.pk).exists())

    def test_allowed_when_only_inactive_field_uses_it(self):
        f = self.link(is_active=False)
        self.client.post(reverse("assets:category_delete", args=[self.src.pk]))
        self.assertFalse(AssetCategory.objects.filter(pk=self.src.pk).exists())
        f.refresh_from_db()
        self.assertIsNone(f.lookup_category_id)

    def test_allowed_when_nothing_uses_it(self):
        self.client.post(reverse("assets:category_delete", args=[self.src.pk]))
        self.assertFalse(AssetCategory.objects.filter(pk=self.src.pk).exists())


class MigrationDriftTests(TestCase):
    def test_no_pending_model_changes(self):
        call_command("makemigrations", "--check", "--dry-run", verbosity=0)
