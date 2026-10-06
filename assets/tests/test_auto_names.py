"""Stage 3 - names are generated automatically, by ONE rule (assets/naming.py).

Covers: the rule itself, legacy filler recognition, the Add / Edit form (blank
name, typed name, auto name following Tag / Brand / Model, hidden Name box),
the Excel import, Project Details and Workstation.
"""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse
from openpyxl import Workbook

from assets.import_utils import parse_uploaded_workbook_sheets, validate_workbook_sheets
from assets.models import Asset, AssetCategory
from assets.naming import (
    NAME_MAX_LENGTH, auto_name_candidates, generate_asset_name, is_auto_name, refreshed_name,
)

from .test_dynamic_display import BuilderDisplayBase, fresh_category


class RuleTests(SimpleTestCase):
    def test_brand_and_model(self):
        self.assertEqual(generate_asset_name("Laptop", "L001", "Dell", "Latitude 5420"), "Dell Latitude 5420")

    def test_brand_without_model(self):
        self.assertEqual(generate_asset_name("Laptop", "L001", "Dell", ""), "Dell")

    def test_category_and_tag_when_no_brand(self):
        self.assertEqual(generate_asset_name("Laptop", "L001"), "Laptop L001")

    def test_category_only_and_last_resort(self):
        self.assertEqual(generate_asset_name("Laptop", ""), "Laptop")
        self.assertEqual(generate_asset_name("", ""), "Asset")
        self.assertEqual(generate_asset_name(None, None, None, None), "Asset")

    def test_project_details_name_is_the_tag(self):
        self.assertEqual(generate_asset_name("Project Details", "Alpha", "Dell"), "Alpha")
        self.assertEqual(generate_asset_name("Project Details", ""), "Project Details")

    def test_whitespace_is_tidied_and_length_is_capped(self):
        self.assertEqual(generate_asset_name(" Laptop ", "  L  001 "), "Laptop L 001")
        self.assertLessEqual(len(generate_asset_name("Laptop", "x" * 400)), NAME_MAX_LENGTH)

    def test_workstation(self):
        self.assertEqual(generate_asset_name("Workstation", "WS004"), "Workstation WS004")


class RecognisingAutoNamesTests(SimpleTestCase):
    def test_current_and_legacy_fillers_are_auto(self):
        # imported before: "<Category> <tag>" even though a Brand exists
        self.assertTrue(is_auto_name("Laptop L001", "Laptop", "L001", "Dell", "X"))
        self.assertTrue(is_auto_name("Dell X", "Laptop", "L001", "Dell", "X"))
        self.assertTrue(is_auto_name("", "Laptop", "L001"))
        self.assertTrue(is_auto_name("Laptop", "Laptop", "L001"))
        self.assertTrue(is_auto_name("Asset", "Laptop", "L001"))

    def test_suffixed_tag_filler_is_auto(self):
        # the same ID on two sheets is stored as SPS011-2; filler used SPS011
        self.assertTrue(is_auto_name("Software SPS011", "Software", "SPS011-2"))

    def test_typed_names_are_not_auto(self):
        self.assertFalse(is_auto_name("Reception printer", "Laptop", "L001", "Dell", "X"))
        self.assertIn("Laptop L001", auto_name_candidates("Laptop", "L001", "Dell", "X"))

    def test_refresh_rules(self):
        old, new = ("Laptop", "L001", "", ""), ("Laptop", "L002", "", "")
        self.assertEqual(refreshed_name("Laptop L001", old, new), "Laptop L002")      # follows the tag
        self.assertEqual(refreshed_name("Front desk", old, new), "Front desk")        # typed: kept
        self.assertEqual(refreshed_name("Laptop L001", old, old), "Laptop L001")      # nothing changed
        self.assertEqual(refreshed_name("   ", old, new), "Laptop L002")              # blank: generated
        # unchanged record keeps a legacy "Cat tag" name even though a Brand exists
        legacy = ("Laptop", "L001", "Dell", "X")
        self.assertEqual(refreshed_name("Laptop L001", legacy, legacy), "Laptop L001")


class FormTests(BuilderDisplayBase):
    """Through the real Add / Edit views. Air Conditioner's builder has no Name
    field, so the Name box is hidden - but the browser still posts its old
    value, which is exactly the stale-name case."""

    def post_add(self, cat, **data):
        data.setdefault("category", cat.id)
        data.setdefault("branch", self.branch.id)
        data.setdefault("is_active", "on")
        data.setdefault("status", "working")
        r = self.client.post(reverse("assets:asset_create") + f"?category={cat.id}", data)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        return Asset.objects.get(category=cat, asset_tag=data["asset_tag"])

    def post_edit(self, asset, **data):
        data.setdefault("category", asset.category_id)
        data.setdefault("branch", self.branch.id)
        data.setdefault("is_active", "on")
        data.setdefault("status", asset.status)
        data.setdefault("name", asset.name)               # the hidden box still posts it
        data.setdefault("brand", asset.brand)
        data.setdefault("model_number", asset.model_number)
        r = self.client.post(reverse("assets:asset_update", args=[asset.id]), data)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        asset.refresh_from_db()
        return asset

    def test_blank_name_is_generated_on_add(self):
        a = self.post_add(self.ac, asset_tag="AC100", name="")
        self.assertEqual(a.name, "Air Conditioner AC100")

    def test_brand_and_model_name_on_add(self):
        a = self.post_add(self.ac, asset_tag="AC101", name="", brand="Voltas", model_number="1.5T")
        self.assertEqual(a.name, "Voltas 1.5T")

    def test_typed_name_is_kept_on_add(self):
        a = self.post_add(self.ac, asset_tag="AC102", name="Boardroom AC", brand="Voltas")
        self.assertEqual(a.name, "Boardroom AC")

    def test_auto_name_follows_the_tag_when_edited(self):
        a = self.post_add(self.ac, asset_tag="AC200", name="")
        a = self.post_edit(a, asset_tag="AC201")
        self.assertEqual(a.name, "Air Conditioner AC201")

    def test_auto_name_follows_brand_and_model_when_edited(self):
        a = self.post_add(self.ac, asset_tag="AC300", name="")
        a = self.post_edit(a, asset_tag="AC300", brand="Daikin", model_number="X9")
        self.assertEqual(a.name, "Daikin X9")
        a = self.post_edit(a, asset_tag="AC300", brand="Daikin", model_number="X10")
        self.assertEqual(a.name, "Daikin X10")

    def test_typed_name_survives_edits_of_tag_and_brand(self):
        a = self.post_add(self.ac, asset_tag="AC400", name="Server room AC")
        a = self.post_edit(a, asset_tag="AC401", brand="Voltas")
        self.assertEqual(a.name, "Server room AC")

    def test_saving_without_changing_the_inputs_keeps_the_name(self):
        # a row imported before this stage: Brand present, name is the old filler
        a = self.mk(self.ac, "AC500", name="Air Conditioner AC500", brand="Voltas")
        a = self.post_edit(a, asset_tag="AC500", notes="serviced")
        self.assertEqual(a.name, "Air Conditioner AC500")

    def test_clearing_the_name_box_regenerates_it(self):
        a = self.post_add(self.ac, asset_tag="AC600", name="Custom")
        a = self.post_edit(a, asset_tag="AC600", name="")
        self.assertEqual(a.name, "Air Conditioner AC600")


def workbook_upload(sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile("names.xlsx", buf.getvalue())


class ImportTests(BuilderDisplayBase):
    def names_for(self, sheets):
        parsed, _skipped, _rerouted = parse_uploaded_workbook_sheets(workbook_upload(sheets))
        results, sheet_errors, _w, _n = validate_workbook_sheets(parsed)
        self.assertEqual(sheet_errors, [])
        return {r["fields"]["asset_tag"]: r["fields"]["name"] for r in results if r["is_valid"]}

    def test_row_without_a_name_column_gets_the_shared_rule(self):
        names = self.names_for({"Gadget": [
            ["Asset Tag", "Brand", "Model"],
            ["G1", "Acme", "X1"],
            ["G2", "", ""],
        ]})
        self.assertEqual(names["G1"], "Acme X1")
        self.assertEqual(names["G2"], "Gadget G2")

    def test_a_name_in_the_sheet_is_kept(self):
        names = self.names_for({"Gadget": [
            ["Asset Tag", "Name", "Brand"],
            ["G3", "Front desk", "Acme"],
        ]})
        self.assertEqual(names["G3"], "Front desk")

    def test_project_details_name_is_the_tag(self):
        AssetCategory.objects.get_or_create(name="Project Details")
        fresh_category("Project Details")
        names = self.names_for({"Project Details": [
            ["Asset Tag", "Brand"],
            ["Alpha", "Whatever"],
        ]})
        self.assertEqual(names["Alpha"], "Alpha")
