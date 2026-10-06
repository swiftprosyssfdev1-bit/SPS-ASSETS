"""Other Asset: ID / Device Name / Device Type on the List and View pages.

ID IS the Asset Tag and Device Name IS the Name (never a second copy), and
Device Type falls back to the Item Type kept by Inside Cupboard rows. A
Device Type stored on the record itself wins.
"""
from assets.category_fields import resolve_field_value
from assets.models import AssetCategory

from .test_dynamic_display import BuilderDisplayBase, add_field, fresh_category


class OtherAssetAliasTests(BuilderDisplayBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other = fresh_category("Other Asset")
        add_field(cls.other, "ID", "id", order=1)
        add_field(cls.other, "Device Name", "device_name", order=2)
        add_field(cls.other, "Device Type", "device_type", order=3)
        add_field(cls.other, "Details", "details", order=4)
        add_field(cls.other, "Item Type", "item_type", order=5)

    def detail_values(self, asset):
        _, resp = self.detail_html(asset)
        return {f["label"]: f["value"] for f in resp.context["detail_fields"]}

    def test_old_others_row_shows_tag_and_name(self):
        a = self.mk(self.other, "C001", name="MX",
                    extra_details={"device_type": "Camera", "details": "BPO Office"})
        v = self.detail_values(a)
        self.assertEqual(v["ID"], "C001")
        self.assertEqual(v["Device Name"], "MX")
        self.assertEqual(v["Device Type"], "Camera")      # stored value wins
        self.assertEqual(v["Details"], "BPO Office")

    def test_inside_cupboard_row_device_type_from_item_type(self):
        a = self.mk(self.other, "IC-001", name="Seagate 500GB Internal HDD",
                    extra_details={"item_type": "Hard Disk"})
        v = self.detail_values(a)
        self.assertEqual(v["ID"], "IC-001")
        self.assertEqual(v["Device Name"], "Seagate 500GB Internal HDD")
        self.assertEqual(v["Device Type"], "Hard Disk")

    def test_id_and_device_name_are_tag_and_name_but_stored_device_type_wins(self):
        a = self.mk(self.other, "IC-002", name="RAM",
                    extra_details={"id": "OLD-ID", "device_name": "OLD-NAME",
                                   "device_type": "KEEP-TYPE", "item_type": "RAM"})
        v = self.detail_values(a)
        self.assertEqual((v["ID"], v["Device Name"], v["Device Type"]),
                         ("IC-002", "RAM", "KEEP-TYPE"))

    def test_no_item_type_leaves_device_type_blank(self):
        a = self.mk(self.other, "X1", name="Thing")
        self.assertEqual(self.detail_values(a)["Device Type"], "")

    def test_other_categories_are_not_affected(self):
        a = self.mk(self.legacy, "G1", name="Gadget One",
                    extra_details={"item_type": "Hard Disk"})
        for label in ("ID", "Device Name", "Device Type"):
            self.assertEqual(resolve_field_value(a, {"name": label.lower(), "label": label}), "")

    def test_list_page_shows_tag_as_id_and_name_as_device_name(self):
        self.mk(self.other, "IC-777", name="Seagate 80GB Internal HDD",
                extra_details={"item_type": "Hard Disk"})
        html, resp = self.list_html(self.other)
        for text in ("IC-777", "Seagate 80GB Internal HDD", "Hard Disk"):
            self.assertIn(text, html)
        labels = [c["label"] for c in resp.context["list_columns"]]
        self.assertEqual(labels[:3], ["ID", "Device Name", "Device Type"])
