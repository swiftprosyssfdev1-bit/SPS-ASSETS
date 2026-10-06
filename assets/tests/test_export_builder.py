"""Stage 2 - Excel Export follows the Category Builder (like the List page).

Covers: same fields / order / labels / values as the List page, hidden-in-list
fields still exported, inactive fields never exported, status wording, legacy
data fallbacks, long text and zero values, ISO dates, category isolation,
legacy categories (no builder fields) unchanged, the real download endpoint,
and export -> re-import.
"""
import datetime
import io

import msoffcrypto
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import load_workbook

from assets.display import (
    build_export_cells, build_export_columns, build_list_cells, build_list_columns,
    format_value,
)
from assets.excel_security import set_export_password
from assets.import_utils import parse_uploaded_workbook_sheets, validate_workbook_sheets
from assets.models import Asset

from .test_dynamic_display import BuilderDisplayBase, add_field

PASSWORD = "export-test-pw"


def export_cells_by_label(asset, category):
    cols = build_export_columns(category)
    return dict(zip([c["label"] for c in cols], build_export_cells(asset, cols)))


class ExportColumnTests(BuilderDisplayBase):
    def test_columns_follow_builder_order_and_labels(self):
        labels = [c["label"] for c in build_export_columns(self.hd)]
        # Hard Disk's builder has no Tag field, so the Tag leads (import needs it)
        self.assertEqual(labels, [
            "Asset Tag", "Hard Disk Name", "Size", "Serial No", "Conditions", "Purpose",
            "Status", "Current Status", "Internal Remark", "List Only",
        ])

    def test_tag_column_is_added_only_when_builder_has_no_tag_field(self):
        for cat in (self.hd, self.ac):
            cols = build_export_columns(cat)
            self.assertEqual((cols[0]["kind"], cols[0]["label"]), ("tag", "Asset Tag"))
            self.assertEqual(sum(c["kind"] == "tag" for c in cols), 1)

    def test_no_duplicate_tag_when_builder_has_a_tag_field(self):
        labels = [c["label"] for c in build_export_columns(self.emp)]
        self.assertEqual(labels, ["Employee ID", "Employee Name", "Status"])

    def test_hidden_in_list_fields_are_still_exported_but_inactive_are_not(self):
        export = [c["key"] for c in build_export_columns(self.hd)]
        listing = [c["key"] for c in build_list_columns(self.hd)]
        self.assertIn("internal_remark", export)
        self.assertNotIn("internal_remark", listing)
        self.assertNotIn("retired", export)
        # every List column is an Export column, in the same relative order
        self.assertEqual([k for k in export if k in listing], listing)

    def test_category_without_builder_fields_returns_none(self):
        self.assertIsNone(build_export_columns(self.legacy))

    def test_reorder_and_rename_in_builder_change_the_export(self):
        f = self.hd.fields.get(key="size")
        f.display_order, f.label = 99, "Capacity"
        f.save()
        labels = [c["label"] for c in build_export_columns(self.hd)]
        self.assertEqual(labels[-1], "Capacity")
        self.assertNotIn("Size", labels)


class ExportValueTests(BuilderDisplayBase):
    def test_values_match_the_list_page_for_every_list_column(self):
        # a row with new-style data, a row relying on the legacy "Details" key,
        # and a row with nothing but its core columns
        self.mk(self.hd, "HD1", status="working", serial_number="S-1",
                extra_details={"size": "1 TB", "purpose": "Backup"})
        self.mk(self.hd, "HD2", status="not_working",
                extra_details={"Details": "Clicking noise", "size": "500 GB"})
        self.mk(self.hd, "HD3", status="working")
        list_cols = build_list_columns(self.hd)
        export_cols = build_export_columns(self.hd)
        by_key = {c["key"]: i for i, c in enumerate(export_cols)}
        for a in Asset.objects.filter(category=self.hd).select_related("category", "branch"):
            shown = [c["text"] for c in build_list_cells(a, list_cols)]
            exported = build_export_cells(a, export_cols)
            for col, text in zip(list_cols, shown):
                self.assertEqual(exported[by_key[col["key"]]], text, (a.asset_tag, col["label"]))

    def test_legacy_details_key_and_duplicate_status_follow_list_wording(self):
        a = self.mk(self.hd, "HD2", status="not_working",
                    extra_details={"Details": "Clicking noise"})
        row = export_cells_by_label(a, self.hd)
        self.assertEqual(row["Current Status"], "Clicking noise")
        self.assertEqual(row["Status"], "Not Working")

    def test_custom_status_wording_is_kept(self):
        a = self.mk(self.emp, "SPS1", status="inactive",
                    extra_details={"employee_name": "Meena", "status": "Resigned"})
        self.assertEqual(export_cells_by_label(a, self.emp)["Status"], "Resigned")

    def test_legacy_air_conditioner_other_status_reads_working(self):
        a = self.mk(self.ac, "AC1", status="other")
        cols = build_export_columns(self.ac)
        self.assertEqual(build_export_cells(a, cols)[0], "AC1")   # Asset Tag first

    def test_long_text_is_not_cut(self):
        text = "x" * 500
        a = self.mk(self.hd, "HD9", extra_details={"conditions": text})
        self.assertEqual(export_cells_by_label(a, self.hd)["Conditions"], text)

    def test_zero_and_false_are_real_values_and_none_is_blank(self):
        a = self.mk(self.ac, "AC2", extra_details={"tonnage": 0, "install_room": None})
        row = export_cells_by_label(a, self.ac)
        self.assertEqual(row["Tonnage"], "0")
        self.assertEqual(row["Install Room"], "")

    def test_dates_export_as_iso_but_pages_keep_site_format(self):
        d = datetime.date(2026, 3, 7)
        self.assertEqual(format_value(d, iso_dates=True), "2026-03-07")
        self.assertEqual(format_value(datetime.datetime(2026, 3, 7, 9, 30), iso_dates=True), "2026-03-07")
        self.assertNotEqual(format_value(d), "2026-03-07")
        self.assertEqual(format_value([d, "x"], iso_dates=True), "2026-03-07, x")

    def test_other_categorys_fields_never_leak_in(self):
        a = self.mk(self.ac, "AC3", extra_details={"tonnage": "2", "size": "SHOULD-NOT-APPEAR"})
        self.assertNotIn("SHOULD-NOT-APPEAR", export_cells_by_label(a, self.ac).values())


class ExportEndpointTests(BuilderDisplayBase):
    """The real download: encrypted workbook, one sheet per category."""

    def download(self):
        set_export_password(PASSWORD, user=self.user)
        r = self.client.get(reverse("assets:export_assets"))
        self.assertEqual(r.status_code, 200)
        office = msoffcrypto.OfficeFile(io.BytesIO(r.content))
        office.load_key(password=PASSWORD)
        plain = io.BytesIO()
        office.decrypt(plain)
        plain.seek(0)
        return plain

    def sheet_rows(self, wb, name):
        return [list(r) for r in wb[name].iter_rows(values_only=True)]

    def test_builder_sheet_matches_builder_and_legacy_sheet_is_unchanged(self):
        self.mk(self.hd, "HD1", status="working", serial_number="S-1",
                extra_details={"size": "1 TB", "internal_remark": "keep"})
        self.mk(self.legacy, "G1", brand="Acme")          # category with no builder fields
        wb = load_workbook(self.download())

        hd = self.sheet_rows(wb, "Hard Disk")
        self.assertEqual(hd[0], [
            "Asset Tag", "Hard Disk Name", "Size", "Serial No", "Conditions", "Purpose",
            "Status", "Current Status", "Internal Remark", "List Only", "Branch",
        ])
        row = dict(zip(hd[0], hd[1]))
        self.assertEqual((row["Size"], row["Serial No"], row["Internal Remark"], row["Branch"]),
                         ("1 TB", "S-1", "keep", "Chennai"))
        self.assertEqual(row["Status"], "Working")

        gadget = self.sheet_rows(wb, "Gadget")
        self.assertEqual(gadget[0][:2], ["Asset Tag", "Name"])   # legacy columns, as before
        self.assertEqual(gadget[1][0], "G1")

    def test_export_requires_a_password_as_before(self):
        r = self.client.get(reverse("assets:export_assets"))
        self.assertEqual(r.status_code, 302)

    def test_hand_built_categories_follow_builder_once_configured(self):
        self.mk(self.emp, "SPS7", status="inactive",
                extra_details={"employee_name": "Asha", "status": "Resigned"})
        wb = load_workbook(self.download())
        emp = self.sheet_rows(wb, "Employee")
        self.assertEqual(emp[0], ["Employee ID", "Employee Name", "Status", "Branch"])
        self.assertEqual(emp[1][:3], ["SPS7", "Asha", "Resigned"])

    def test_export_can_be_imported_back(self):
        self.mk(self.hd, "HD1", status="working", serial_number="S-1",
                extra_details={"size": "1 TB", "purpose": "Backup"})
        self.mk(self.hd, "HD2", status="not_working", serial_number="S-2",
                extra_details={"size": "2 TB", "conditions": "Bad sectors"})
        data = self.download().getvalue()
        Asset.objects.filter(category=self.hd).delete()   # re-import into an empty category

        upload = SimpleUploadedFile("export.xlsx", data)
        sheets, _skipped, _rerouted = parse_uploaded_workbook_sheets(upload)
        sheets = [s for s in sheets if s[0] == "Hard Disk"]
        self.assertEqual(len(sheets), 1)
        results, sheet_errors, _warn, _notices = validate_workbook_sheets(sheets)
        self.assertEqual(sheet_errors, [])
        self.assertEqual([r["errors"] for r in results], [[], []])
        # Import Preview shows the same columns, in the same order, as Export
        self.assertEqual(
            results[0]["template_headers"],
            [c["label"] for c in build_export_columns(self.hd)],
        )
        sizes = sorted(r["fields"]["extra_details"].get(self.hd.fields.get(key="size").key, "")
                       for r in results)
        self.assertEqual(sizes, ["1 TB", "2 TB"])
