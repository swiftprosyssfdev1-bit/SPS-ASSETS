"""Stage 1 - List and Detail pages follow the Category Builder.

Covers: labels, order, List/Detail visibility, extra_details vs real columns,
select values, status labels, null / zero / long values, legacy fallback for
categories with no builder fields, and per-category isolation.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assets.display import (
    build_detail_fields, build_list_cells, build_list_columns, format_value,
)
from assets.models import Asset, AssetCategory, AssetField, AssetFieldOption, Branch

User = get_user_model()


def add_field(cat, label, key, ftype="text", order=0, options=(), **kw):
    f = AssetField.objects.create(
        category=cat, label=label, key=key, field_type=ftype, display_order=order, **kw)
    for i, o in enumerate(options):
        AssetFieldOption.objects.create(field=f, label=o, value=o, display_order=i)
    return f


def fresh_category(name):
    """Migrations may pre-seed some categories; reuse them but start with no
    builder fields so each test controls the configuration."""
    cat, _ = AssetCategory.objects.get_or_create(name=name)
    AssetField.objects.filter(category=cat).delete()
    Asset.objects.filter(category=cat).delete()
    return cat


class BuilderDisplayBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "a@x.com", "pw")
        cls.branch = Branch.objects.get_or_create(name="Chennai")[0]

        # Hard Disk: Size / Conditions / Purpose / Status / Current Status
        cls.hd = fresh_category("Hard Disk")
        add_field(cls.hd, "Hard Disk Name", "hard_disk_name", order=1)
        add_field(cls.hd, "Size", "size", order=2)
        add_field(cls.hd, "Serial No", "serial_no", order=3)
        add_field(cls.hd, "Conditions", "conditions", "textarea", order=4)
        add_field(cls.hd, "Purpose", "purpose", order=5)
        add_field(cls.hd, "Status", "status", "select", order=6, options=["Working", "Not Working"])
        add_field(cls.hd, "Current Status", "current_status", order=7)
        add_field(cls.hd, "Internal Remark", "internal_remark", order=8, show_in_list=False)
        add_field(cls.hd, "List Only", "list_only", order=9, show_in_detail=False)
        add_field(cls.hd, "Retired Field", "retired", order=10, is_active=False)

        # Employee: Employee ID / Employee Name / Status (custom wording kept)
        cls.emp = fresh_category("Employee")
        add_field(cls.emp, "Employee ID", "employee_id", order=1)
        add_field(cls.emp, "Employee Name", "employee_name", order=2)
        add_field(cls.emp, "Status", "status", "select", order=3, options=["Active", "Inactive"])

        # Air Conditioner: its OWN fields only
        cls.ac = fresh_category("Air Conditioner")
        add_field(cls.ac, "Tonnage", "tonnage", "number", order=1)
        add_field(cls.ac, "Install Room", "install_room", order=2)
        add_field(cls.ac, "Location", "location", order=3)

        # Category with NO builder fields -> legacy rendering must stay
        cls.legacy = fresh_category("Gadget")

    def mk(self, cat, tag, **kw):
        kw.setdefault("name", f"{cat.name} {tag}")
        kw.setdefault("branch", self.branch)
        return Asset.objects.create(category=cat, asset_tag=tag, **kw)

    def setUp(self):
        self.client.login(username="admin", password="pw")

    def list_html(self, cat):
        r = self.client.get(reverse("assets:category_detail", args=[cat.id]))
        self.assertEqual(r.status_code, 200)
        return r.content.decode(), r

    def detail_html(self, asset):
        r = self.client.get(reverse("assets:asset_detail", args=[asset.id]))
        self.assertEqual(r.status_code, 200)
        return r.content.decode(), r


class FormatValueTests(TestCase):
    def test_none_zero_and_false_are_handled(self):
        self.assertEqual(format_value(None), "")
        self.assertEqual(format_value(0), "0")
        self.assertEqual(format_value(False), "No")
        self.assertEqual(format_value(["a", "", "b"]), "a, b")

    def test_select_value_uses_option_label_case(self):
        f = {"type": "select", "options": ["Not Working", "Working"]}
        self.assertEqual(format_value("not  working", f), "Not Working")
        self.assertEqual(format_value("Something custom", f), "Something custom")


class HardDiskTests(BuilderDisplayBase):
    def setUp(self):
        super().setUp()
        self.a = self.mk(
            self.hd, "HD001", name="WD Blue", serial_number="SN-1", status="not_working",
            extra_details={"size": "1 TB", "conditions": "x" * 400, "purpose": "Backup",
                           "current_status": "In cupboard", "internal_remark": "secret-remark",
                           "list_only": "list-only-val", "retired": "retired-val"})

    def test_list_columns_follow_builder_order_labels_and_visibility(self):
        cols = build_list_columns(self.hd, default_tag_label="Hard Disk Number")
        labels = [c["label"] for c in cols]
        self.assertEqual(labels, [
            "Hard Disk Number", "Hard Disk Name", "Size", "Serial No", "Conditions",
            "Purpose", "Status", "Current Status", "List Only"])
        self.assertNotIn("Internal Remark", labels)     # show_in_list off
        self.assertNotIn("Retired Field", labels)       # inactive

    def test_list_page_renders_values_and_status_label(self):
        html, _ = self.list_html(self.hd)
        for text in ["1 TB", "Backup", "In cupboard", "SN-1", "WD Blue", "Not Working", "list-only-val"]:
            self.assertIn(text, html)
        self.assertNotIn("secret-remark", html)
        self.assertNotIn("retired-val", html)
        self.assertIn("badge-status-not_working", html)

    def test_current_status_is_its_own_field_not_the_status_badge(self):
        cols = build_list_columns(self.hd)
        kinds = {c["label"]: c["kind"] for c in cols}
        self.assertEqual(kinds["Status"], "status")
        self.assertEqual(kinds["Current Status"], "field")
        cells = build_list_cells(self.a, cols)
        by = dict(zip([c["label"] for c in cols], cells))
        self.assertEqual(by["Current Status"]["text"], "In cupboard")
        self.assertEqual(by["Status"]["text"], "Not Working")

    def test_long_text_is_cut_in_list_and_full_on_detail(self):
        cols = build_list_columns(self.hd)
        by = dict(zip([c["label"] for c in cols], build_list_cells(self.a, cols)))
        self.assertLess(len(by["Conditions"]["text"]), 400)
        self.assertEqual(by["Conditions"]["title"], "x" * 400)
        html, _ = self.detail_html(self.a)
        self.assertIn("x" * 400, html)

    def test_detail_respects_visibility_order_and_ignores_other_category_fields(self):
        html, resp = self.detail_html(self.a)
        labels = [f["label"] for f in resp.context["detail_fields"]]
        self.assertEqual(labels, [
            "Hard Disk Name", "Size", "Serial No", "Conditions", "Purpose", "Status",
            "Current Status", "Internal Remark"])  # List Only hidden in detail
        self.assertNotIn("list-only-val", html)
        self.assertNotIn("retired-val", html)
        self.assertNotIn("Brand / Model", html)         # no generic overview block
        self.assertNotIn("Previous User", html)

    def test_missing_null_and_zero_values_are_safe(self):
        b = self.mk(self.hd, "HD002", extra_details={"size": 0, "purpose": None})
        c = self.mk(self.hd, "HD003", extra_details={})
        html, resp = self.detail_html(b)
        vals = {f["label"]: f["value"] for f in resp.context["detail_fields"]}
        self.assertEqual(vals["Size"], "0")
        self.assertEqual(vals["Purpose"], "")
        self.detail_html(c)
        self.list_html(self.hd)

    def test_legacy_details_key_still_feeds_current_status(self):
        old = self.mk(self.hd, "HD004", extra_details={"Details": "legacy-detail"})
        _, resp = self.detail_html(old)
        vals = {f["label"]: f["value"] for f in resp.context["detail_fields"]}
        self.assertEqual(vals["Current Status"], "legacy-detail")

    def test_old_key_spelling_is_found(self):
        old = self.mk(self.hd, "HD005", extra_details={"PURPOSE": "Backup", "Size ": "500 GB"})
        _, resp = self.detail_html(old)
        vals = {f["label"]: f["value"] for f in resp.context["detail_fields"]}
        self.assertEqual(vals["Purpose"], "Backup")
        self.assertEqual(vals["Size"], "500 GB")

    def test_name_field_shows_the_assets_real_name(self):
        _, resp = self.detail_html(self.a)
        vals = {f["label"]: f["value"] for f in resp.context["detail_fields"]}
        self.assertEqual(vals["Hard Disk Name"], "WD Blue")


class EmployeeTests(BuilderDisplayBase):
    def setUp(self):
        super().setUp()
        self.e1 = self.mk(self.emp, "SPS001", name="Priya", status="active",
                          extra_details={"employee_name": "Priya", "employee_id": "SPS001",
                                         "status": "Active"})
        self.e2 = self.mk(self.emp, "SPS002", name="Ravi", status="other",
                          extra_details={"employee_name": "Ravi", "status": "Resigned"})
        self.e3 = self.mk(self.emp, "SPS003", name="Old Row", status="active", extra_details={})

    def test_columns_are_employee_id_name_status_with_no_duplicate_tag(self):
        labels = [c["label"] for c in build_list_columns(self.emp)]
        self.assertEqual(labels, ["Employee ID", "Employee Name", "Status"])

    def test_list_shows_values_and_custom_status_wording(self):
        html, _ = self.list_html(self.emp)
        for t in ["SPS001", "Priya", "Ravi", "Resigned", "Active", "badge-status-resigned"]:
            self.assertIn(t, html)

    def test_existing_row_without_extra_details_still_shows_name_and_status(self):
        cols = build_list_columns(self.emp)
        by = dict(zip([c["label"] for c in cols], build_list_cells(self.e3, cols)))
        self.assertEqual(by["Employee ID"]["text"], "SPS003")
        self.assertEqual(by["Employee Name"]["text"], "Old Row")
        self.assertEqual(by["Status"]["text"], "Active")

    def test_detail(self):
        _, resp = self.detail_html(self.e2)
        vals = {f["label"]: f["value"] for f in resp.context["detail_fields"]}
        self.assertEqual(vals, {"Employee ID": "SPS002", "Employee Name": "Ravi", "Status": "Resigned"})


class AirConditionerTests(BuilderDisplayBase):
    def test_shows_own_fields_not_another_categorys(self):
        a = self.mk(self.ac, "AC001", current_location="Hall",
                    extra_details={"tonnage": 1.5, "install_room": "Server room",
                                   "size": "1 TB"})  # stray key from another category
        html, _ = self.list_html(self.ac)
        self.assertIn("Tonnage", html)
        self.assertIn("Server room", html)
        self.assertIn("Hall", html)
        for foreign in ["Conditions", "Purpose", "Current Status", "Capacity / Location"]:
            self.assertNotIn(foreign, html)
        self.assertNotIn("1 TB", html)
        dhtml, resp = self.detail_html(a)
        self.assertEqual([f["label"] for f in resp.context["detail_fields"]],
                         ["Tonnage", "Install Room", "Location"])
        self.assertIn("1.5", dhtml)

    def test_legacy_ac_status_other_still_reads_working(self):
        # AC has no Status field here, so use a builder Status for this check
        add_field(self.ac, "Status", "status", "select", order=9, options=["Working", "Service"])
        a = self.mk(self.ac, "AC002", status="other")
        cols = build_list_columns(self.ac)
        by = dict(zip([c["label"] for c in cols], build_list_cells(a, cols)))
        self.assertEqual(by["Status"]["text"], "Working")


class LegacyFallbackTests(BuilderDisplayBase):
    def test_category_without_builder_fields_keeps_legacy_layout(self):
        self.assertIsNone(build_list_columns(self.legacy))
        a = self.mk(self.legacy, "G001", brand="Acme", model_number="X1", serial_number="S9")
        self.assertIsNone(build_detail_fields(a))
        html, _ = self.list_html(self.legacy)
        self.assertIn("Brand / Model", html)
        self.assertIn("Acme X1", html)
        dhtml, _ = self.detail_html(a)
        self.assertIn("Brand / Model", dhtml)

    def test_empty_list_colspan_matches_columns(self):
        html, resp = self.list_html(self.emp)
        self.assertIn(f'colspan="{resp.context["column_count"]}"', html)
        # 3 columns + Branch (all branches) + Actions
        self.assertEqual(resp.context["column_count"], 5)


class OrderingAndVisibilityTests(BuilderDisplayBase):
    def test_reordering_in_builder_reorders_columns(self):
        f = AssetField.objects.get(category=self.emp, key="employee_name")
        f.display_order = 0
        f.save()
        self.assertEqual([c["label"] for c in build_list_columns(self.emp)],
                         ["Employee Name", "Employee ID", "Status"])

    def test_hiding_status_in_list_removes_status_column(self):
        AssetField.objects.filter(category=self.emp, key="status").update(show_in_list=False)
        self.assertNotIn("Status", [c["label"] for c in build_list_columns(self.emp)])

    def test_renamed_label_shows_everywhere(self):
        AssetField.objects.filter(category=self.hd, key="size").update(label="Capacity")
        html, _ = self.list_html(self.hd)
        self.assertIn("Capacity", html)
        a = self.mk(self.hd, "HD009", extra_details={"size": "2 TB"})
        _, resp = self.detail_html(a)
        self.assertIn("Capacity", [f["label"] for f in resp.context["detail_fields"]])


class AddEditRoundTripTests(BuilderDisplayBase):
    """Add -> List -> Detail -> Edit -> List/Detail for the three scenario
    categories (what the stage-1 checklist asks to verify)."""

    def post_add(self, cat, **data):
        data.setdefault("category", cat.id)
        data.setdefault("branch", self.branch.id)
        data.setdefault("is_active", "on")
        r = self.client.post(reverse("assets:asset_create") + f"?category={cat.id}", data)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        return Asset.objects.get(category=cat, asset_tag=data["asset_tag"])

    def post_edit(self, asset, **data):
        data.setdefault("category", asset.category_id)
        data.setdefault("branch", self.branch.id)
        data.setdefault("is_active", "on")
        r = self.client.post(reverse("assets:asset_update", args=[asset.id]), data)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        asset.refresh_from_db()
        return asset

    def test_hard_disk(self):
        a = self.post_add(self.hd, asset_tag="HD100", name="Toshiba", serial_number="T-1",
                          status="working", size="2 TB", conditions="Good", purpose="Archive")
        html, _ = self.list_html(self.hd)
        for t in ["2 TB", "Good", "Archive", "T-1", "Toshiba"]:
            self.assertIn(t, html)
        dhtml, _ = self.detail_html(a)
        self.assertIn("2 TB", dhtml)
        a = self.post_edit(a, asset_tag="HD100", name="Toshiba", serial_number="T-1",
                           status="not_working", size="4 TB", conditions="Bad sector",
                           purpose="Archive")
        html, _ = self.list_html(self.hd)
        self.assertIn("4 TB", html)
        self.assertNotIn("2 TB", html)
        dhtml, resp = self.detail_html(a)
        vals = {f["label"]: f["value"] for f in resp.context["detail_fields"]}
        self.assertEqual(vals["Status"], "Not Working")
        # The Add/Edit form has no separate storage for a 2nd Status-type field
        # (it writes the same status column), so display mirrors that column.
        self.assertEqual(vals["Current Status"], "Not Working")

    def test_employee(self):
        a = self.post_add(self.emp, asset_tag="SPS500", name="Meena", status="active",
                          employee_name="Meena", employee_id="SPS500")
        html, _ = self.list_html(self.emp)
        self.assertIn("SPS500", html)
        self.assertIn("Meena", html)
        a = self.post_edit(a, asset_tag="SPS500", name="Meena K", status="inactive",
                           employee_name="Meena K", employee_id="SPS500")
        _, resp = self.detail_html(a)
        vals = {f["label"]: f["value"] for f in resp.context["detail_fields"]}
        self.assertEqual(vals["Employee ID"], "SPS500")
        self.assertEqual(vals["Employee Name"], "Meena K")
        self.assertEqual(vals["Status"], "Inactive")

    def test_air_conditioner(self):
        a = self.post_add(self.ac, asset_tag="AC100", name="Voltas", tonnage="1.5",
                          install_room="Lab", current_location="Floor 2", status="working")
        html, _ = self.list_html(self.ac)
        for t in ["1.5", "Lab", "Floor 2"]:
            self.assertIn(t, html)
        a = self.post_edit(a, asset_tag="AC100", name="Voltas", tonnage="2",
                           install_room="Lab 2", current_location="Floor 2", status="working")
        _, resp = self.detail_html(a)
        vals = {f["label"]: f["value"] for f in resp.context["detail_fields"]}
        self.assertEqual((vals["Tonnage"], vals["Install Room"]), ("2", "Lab 2"))


class LegacyHandBuiltStillRenderTests(BuilderDisplayBase):
    """Hard Disk / Employee / AC / Inside Cupboard with NO builder fields keep
    their existing hand-built List and Detail layouts (existing data safe)."""

    def test_unconfigured_special_categories_render_as_before(self):
        # (category, extra_details, real Asset columns, text that must appear)
        # Inputs use the CURRENT real-sheet names: Hard Disk's "Purpose" (the old
        # "Details" column became "Status"), Employee status is the real status
        # column, Inside Cupboard's size is "Capacity / Size".
        cases = [
            ("Hard Disk", {"Size": "1 TB", "Conditions": "ok", "Purpose": "legacy-cs"}, {},
             ["Hard disk Number", "Conditions", "legacy-cs"]),
            ("Employee", {}, {"status": "Resigned"}, ["Resigned"]),
            # Air Conditioner has no hand-built column set any more (its
            # "Capacity / Location" header only exists once builder fields are
            # set up - see fix_air_conditioner_fields); the data must still show.
            ("Air Conditioner", {"Status": "1.5 Ton Hall", "Details": "2025"}, {},
             ["1.5 Ton Hall"]),
            ("Inside Cupboard", {"Item Type": "HDD", "Capacity / Size": "500 GB"}, {},
             ["Item Type", "500 GB"]),
        ]
        for name, extra, cols, expected in cases:
            cat = fresh_category(name)
            a = self.mk(cat, f"L-{name[:2]}", extra_details=extra, **cols)
            html, resp = self.list_html(cat)
            self.assertIsNone(resp.context["list_columns"], name)
            # The legacy Hard Disk header is the real sheet's "Hard disk  Number"
            # (two spaces); browsers collapse that, so compare collapsed text.
            flat = " ".join(html.split())
            for t in expected:
                with self.subTest(category=name, expected=t):
                    self.assertIn(t, flat, f"{name}: {t}")
            _, dresp = self.detail_html(a)
            if name == "Employee":
                # the list header is "Tag"; the real sheet's "Employee Id" is the Detail label
                labels = [f["label"] for f in dresp.context["detail_fields"]]
                self.assertIn("Employee Id", labels)
