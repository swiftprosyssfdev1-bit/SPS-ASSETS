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
