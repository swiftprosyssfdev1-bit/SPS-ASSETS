from django.db import models
from django.conf import settings
from django.utils import timezone


class Branch(models.Model):
    """A physical office/branch. Assets and Branch Admins are scoped to
    one or more of these. New branches can be added at any time from the
    UI (Super Admin only) — nothing about branches is hard-coded.

    Field names/types here intentionally match the Branch model that was
    already migrated into this project (0009_branch_asset_branch_userbranchaccess)
    so this file doesn't require any destructive column rename."""
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True, blank=True, null=True,
                             help_text="Short code, e.g. 'CHN' (auto-filled from name if left blank)")
    status = models.BooleanField(default=True, help_text="Uncheck to retire this branch without deleting it")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Branches"
        ordering = ["code", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.name.strip().upper()[:20].replace(" ", "")
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        """Convenience alias — the DB column is `status` (boolean), this
        just reads nicer everywhere else in the app that talks about
        active/retired branches."""
        return self.status


class UserBranchAccess(models.Model):
    """Marks a user as a Branch Admin (as opposed to the Super Admin) and
    records which branch(es) they may access. A user with no row here is
    treated as a Super Admin only if they are also a Django superuser;
    every other user with no row has no branch access at all.

    Matches the UserBranchAccess model already migrated into this project
    (0009_branch_asset_branch_userbranchaccess) — same table, same
    related_name — so no schema changes are required here."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="branch_access",
    )
    branches = models.ManyToManyField(Branch, blank=True, related_name="admins")

    class Meta:
        verbose_name_plural = "User Branch Access"

    def __str__(self):
        return f"Branch Admin: {self.user.get_username()}"


class AssetCategory(models.Model):
    """e.g. Workstation, Monitor, Keyboard, Mouse, UPS, Bluetooth, Hard Disk,
    Software/OS, A/C, Biometrics, Vendor, Project, etc."""
    name = models.CharField(max_length=100, unique=True)
    icon = models.CharField(
        max_length=50, blank=True,
        help_text="Bootstrap icon class, e.g. 'bi-pc-display' (optional)"
    )
    description = models.CharField(max_length=255, blank=True)
    show_under_other = models.BooleanField(
        default=False,
        help_text="If checked, this category is grouped inside the 'Other' "
                   "dropdown on the dashboard instead of getting its own card."
    )

    class Meta:
        verbose_name_plural = "Asset Categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Asset(models.Model):
    STATUS_CHOICES = [
        ("working", "Working"),
        ("not_working", "Not Working"),
        ("idle", "Idle / In Cupboard"),
        ("scrap", "Scrap / Destroyed"),
        ("missing", "Missing"),
        ("service", "Under Service"),
        ("active", "Active"),
        ("inactive", "Inactive"),
        ("open", "Open"),
        ("resolved", "Resolved"),
        ("running", "Running"),      # Project Details sheet's Status column
        ("stopped", "Stopped"),      # Project Details sheet's Status column
        ("completed", "Completed"),  # Project Details sheet's Status column
        ("not_set", "Not Set"),
        ("other", "Other"),
    ]

    category = models.ForeignKey(
        AssetCategory, on_delete=models.PROTECT, related_name="assets"
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name="assets",
        null=True, blank=True,
        help_text="Branch this asset belongs to. Null only for legacy rows "
                   "imported before branches existed — assign one from the "
                   "Edit page.",
    )
    asset_tag = models.CharField(
        max_length=50, unique=True,
        help_text="Unique ID, e.g. WS001, M009, K014, U006"
    )
    name = models.CharField(max_length=150, db_index=True, help_text="Short display name")
    brand = models.CharField(max_length=100, blank=True, db_index=True)
    model_number = models.CharField(max_length=100, blank=True, db_index=True)
    serial_number = models.CharField(max_length=150, blank=True, db_index=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="working", db_index=True)

    current_assigned_to = models.CharField(max_length=150, blank=True, db_index=True, help_text="Current user / employee / location")
    current_location = models.CharField(max_length=150, blank=True, db_index=True)

    previous_assigned_to = models.CharField(max_length=150, blank=True)
    previous_location = models.CharField(max_length=150, blank=True)

    purchase_date = models.DateField(null=True, blank=True)
    last_service_date = models.DateField(null=True, blank=True)

    linked_workstation = models.CharField(
        max_length=50, blank=True, db_index=True,
        help_text="Workstation ID this item is used in, e.g. WS004 (optional)"
    )

    extra_details = models.JSONField(default=dict, blank=True)

    notes = models.TextField(blank=True)

    is_active = models.BooleanField(default=True, db_index=True, help_text="Uncheck if disposed/retired")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        ordering = ["category__name", "asset_tag"]
        indexes = [
            models.Index(fields=["category", "is_active", "status"]),
            models.Index(fields=["category", "is_active"]),
            models.Index(fields=["branch", "is_active"]),
        ]

    def __str__(self):
        return f"{self.asset_tag} - {self.name}"


class AssetHistory(models.Model):
    """Automatic audit trail: every time a tracked field changes on an Asset,
    a row is written here so we always know the 'previous' vs 'now' value."""
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="history")
    field_name = models.CharField(max_length=100, db_index=True)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    changed_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name_plural = "Asset History"
        ordering = ["-changed_at"]
        indexes = [
            models.Index(fields=["asset", "-changed_at"]),
        ]

    def __str__(self):
        return f"{self.asset.asset_tag}: {self.field_name} changed"


TRACKED_FIELDS = [
    "status", "current_assigned_to", "current_location", "brand",
    "model_number", "serial_number", "is_active",
]


class ExportPassword(models.Model):
    """Single-row setting: the password the downloaded Excel export is locked
    with (and that Import uses to unlock an exported file). Only the Super
    Admin can set/reset it. Stored encrypted (see excel_security), never as
    plain text, and never shown back on screen."""

    encrypted_value = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    def __str__(self):
        return "Excel export password"


class AssetField(models.Model):
    """
    One configurable field per category, stored in the database.
    Once a category has ANY AssetField rows (even inactive), it is
    'intentionally configured' and sheet_templates/category_fields.py
    fallbacks are permanently bypassed for it.

    The `key` is auto-generated on creation and is IMMUTABLE afterwards
    — the label may be renamed, but the key is what is stored in
    Asset.extra_details, so changing it would orphan existing data.
    """
    FIELD_TYPES = [
        ("text",     "Single Line Text"),
        ("textarea", "Multi-Line Text"),
        ("select",   "Dropdown"),
        ("number",   "Number"),
        ("date",     "Date"),
        ("email",    "Email"),
        ("url",      "URL"),
    ]
    WIDTH_CHOICES = [(6, "Half Width (col-6)"), (12, "Full Width (col-12)")]

    category = models.ForeignKey(
        AssetCategory, on_delete=models.PROTECT, related_name="fields"
    )
    label = models.CharField(max_length=150)
    key = models.CharField(
        max_length=100,
        help_text="Stable internal key stored in Asset.extra_details. "
                  "Auto-generated on creation; immutable afterwards.",
    )
    field_type = models.CharField(max_length=20, choices=FIELD_TYPES, default="text")
    width = models.IntegerField(default=6, choices=WIDTH_CHOICES)
    required = models.BooleanField(default=False)
    placeholder = models.CharField(max_length=200, blank=True)
    help_text = models.CharField(max_length=300, blank=True)
    default_value = models.CharField(max_length=200, blank=True)
    show_in_list = models.BooleanField(default=True)
    show_in_detail = models.BooleanField(default=True)
    show_in_add = models.BooleanField(default=True)
    show_in_edit = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "id"]
        unique_together = [("category", "key")]
        verbose_name = "Asset Field"
        verbose_name_plural = "Asset Fields"

    def __str__(self):
        return f"{self.category.name} → {self.label}"

    def as_field_dict(self):
        """Returns a dict compatible with the existing category_fields format,
        so any consumer of get_category_fields() works without changes."""
        d = {
            "name": self.key,
            "label": self.label,
            "type": self.field_type,
            "width": self.width,
            "required": self.required,
            "placeholder": self.placeholder,
            "help_text": self.help_text,
            "show_in_list": self.show_in_list,
            "show_in_detail": self.show_in_detail,
            "show_in_add": self.show_in_add,
            "show_in_edit": self.show_in_edit,
        }
        if self.field_type == "select":
            d["options"] = list(
                self.options.filter(is_active=True)
                    .order_by("display_order")
                    .values_list("label", flat=True)
            )
        return d


class AssetFieldOption(models.Model):
    """
    One selectable option for a Dropdown (select) AssetField.
    Options are soft-deleted (is_active=False) — never hard-deleted,
    so existing asset records that stored an option value are not silently
    corrupted when an option is later removed from the list.
    """
    field = models.ForeignKey(
        AssetField, on_delete=models.CASCADE, related_name="options"
    )
    label = models.CharField(max_length=200)
    value = models.CharField(
        max_length=200,
        help_text="The value stored in Asset.extra_details. Defaults to label if left blank.",
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "id"]

    def save(self, *args, **kwargs):
        if not self.value:
            self.value = self.label
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.field.label} → {self.label}"


class ConfigAuditLog(models.Model):
    """
    Audit trail for all Super Admin configuration changes: category
    renames, field creates/edits/deactivations, option changes.
    Asset data changes are tracked separately by AssetHistory.
    """
    ACTION_CHOICES = [
        ("create",     "Create"),
        ("update",     "Update"),
        ("deactivate", "Deactivate"),
        ("delete",     "Delete"),
    ]
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    model_name = models.CharField(max_length=50)   # 'AssetCategory' | 'AssetField' | 'AssetFieldOption'
    object_id = models.IntegerField(null=True, blank=True)
    object_repr = models.CharField(max_length=255)
    diff = models.TextField(blank=True)             # JSON: {field: [old, new], ...}

    class Meta:
        ordering = ["-changed_at"]
        verbose_name = "Config Audit Log"
        verbose_name_plural = "Config Audit Logs"

    def __str__(self):
        return f"{self.action} {self.model_name} — {self.object_repr}"

# ---------------------------------------------------------------------------
# Append this block to the end of assets/models.py
# ---------------------------------------------------------------------------


class Workstation(models.Model):
    """
    The dedicated Workstation module's own record. One row per legacy
    Workstation Asset (OneToOne — the Asset row keeps owning asset_tag,
    branch, status, is_active, and AssetHistory exactly as it does today;
    nothing about Asset changes). Workstation itself never appears as an
    AssetCategory anywhere in the UI — see WORKSTATION_CATEGORY_NAME /
    is_workstation_category in views.py, which still hides the underlying
    AssetCategory("Workstation") row that Asset.category (a required FK)
    needs to keep pointing at.

    All of Workstation's own fields — including its relationships to CPU /
    Monitor / Keyboard / Mouse / UPS / Employee assets — are admin
    configurable through the dedicated Workstation Field Builder
    (WorkstationField below), so they live here in extra_details exactly
    the way Asset.extra_details works for every normal category. There are
    no hardcoded relationship columns on this model; a "Lookup" WorkstationField
    is what makes a given extra_details key mean "this is a reference to
    another asset" (see relations.py's get_workstation_forward_relationships).
    """
    asset = models.OneToOneField(
        Asset, on_delete=models.PROTECT, related_name="workstation_profile",
        limit_choices_to={"category__name__iexact": "workstation"},
        help_text="The underlying Workstation Asset row (asset_tag, branch, "
                   "status, audit history all live there, unchanged).",
    )
    extra_details = models.JSONField(
        default=dict, blank=True,
        help_text="All Workstation Field Builder values, keyed by WorkstationField.key "
                   "— including Lookup fields, which store the linked asset(s)' "
                   "asset_tag(s), same convention as Asset.extra_details.",
    )
    unresolved = models.JSONField(
        default=dict, blank=True,
        help_text="Migration-time bookkeeping only: Lookup tokens from the legacy "
                   "data that didn't resolve to a real Asset (blank / no match), "
                   "kept here instead of silently dropped. Not shown on any form.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Workstation"
        verbose_name_plural = "Workstations"

    def __str__(self):
        return f"Workstation: {self.asset.asset_tag}"


class WorkstationField(models.Model):
    """
    One configurable field on the Workstation module — the Workstation
    equivalent of AssetField, but not scoped to any AssetCategory (there's
    only ever one Workstation module, not many categories), and with one
    extra field_type: "lookup", for a relationship to another asset
    (CPU / Monitor / Keyboard / Mouse / UPS / Employee, or any future one).

    `key` is auto-generated on creation and IMMUTABLE afterwards — same
    rule as AssetField.key, for the same reason (it's the key stored in
    Workstation.extra_details; renaming it would orphan existing data).
    """
    FIELD_TYPES = [
        ("text",     "Single Line Text"),
        ("textarea", "Multi-Line Text"),
        ("select",   "Dropdown"),
        ("number",   "Number"),
        ("date",     "Date"),
        ("email",    "Email"),
        ("url",      "URL"),
        ("lookup",   "Lookup (link to another asset)"),
    ]
    WIDTH_CHOICES = [(6, "Half Width (col-6)"), (12, "Full Width (col-12)")]

    label = models.CharField(max_length=150)
    key = models.CharField(
        max_length=100, unique=True,
        help_text="Stable internal key stored in Workstation.extra_details. "
                   "Auto-generated on creation; immutable afterwards.",
    )
    field_type = models.CharField(max_length=20, choices=FIELD_TYPES, default="text")
    width = models.IntegerField(default=6, choices=WIDTH_CHOICES)
    required = models.BooleanField(default=False)
    placeholder = models.CharField(max_length=200, blank=True)
    help_text = models.CharField(max_length=300, blank=True)
    default_value = models.CharField(max_length=200, blank=True)
    show_in_list = models.BooleanField(default=True)
    show_in_detail = models.BooleanField(default=True)
    show_in_add = models.BooleanField(default=True)
    show_in_edit = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    # Only meaningful when field_type == "lookup":
    lookup_category = models.CharField(
        max_length=100, blank=True,
        help_text="AssetCategory name this field searches against, e.g. "
                   "'CPU / System Unit', 'Monitor', 'Employee'. Required for "
                   "Lookup fields — the same searchable-combo-box behaviour "
                   "Workstation's CPU/Monitor/etc. fields already have today.",
    )
    lookup_multi = models.BooleanField(
        default=False,
        help_text="Allow linking more than one asset (e.g. multiple Monitors "
                   "or UPS units on one workstation).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "id"]
        verbose_name = "Workstation Field"
        verbose_name_plural = "Workstation Fields"

    def __str__(self):
        return f"Workstation → {self.label}"

    def as_field_dict(self):
        d = {
            "name": self.key,
            "label": self.label,
            "type": self.field_type,
            "width": self.width,
            "required": self.required,
            "placeholder": self.placeholder,
            "help_text": self.help_text,
            "show_in_list": self.show_in_list,
            "show_in_detail": self.show_in_detail,
            "show_in_add": self.show_in_add,
            "show_in_edit": self.show_in_edit,
            "lookup_category": self.lookup_category,
            "lookup_multi": self.lookup_multi,
        }
        if self.field_type == "select":
            d["options"] = list(
                self.options.filter(is_active=True)
                    .order_by("display_order")
                    .values_list("label", flat=True)
            )
        return d


class WorkstationFieldOption(models.Model):
    """One selectable option for a Dropdown WorkstationField. Soft-deleted
    only, same as AssetFieldOption, so existing Workstation records that
    stored an option value are never silently corrupted."""
    field = models.ForeignKey(
        WorkstationField, on_delete=models.CASCADE, related_name="options"
    )
    label = models.CharField(max_length=200)
    value = models.CharField(
        max_length=200,
        help_text="The value stored in Workstation.extra_details. Defaults to label if left blank.",
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "id"]

    def save(self, *args, **kwargs):
        if not self.value:
            self.value = self.label
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.field.label} → {self.label}"


class SiteSetting(models.Model):
    """Small key/value store for Super Admin display settings
    (e.g. the Workstation module's display name)."""
    key = models.CharField(max_length=100, unique=True)
    value = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.key} = {self.value}"
