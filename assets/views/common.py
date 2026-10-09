"""Shared helpers and constants used by more than one views module."""
import json

from django.contrib.auth import get_user_model
from django.db.models import F

from ..models import ConfigAuditLog
from ..permissions import (
    super_admin_required,
)


pass



User = get_user_model()

# Backwards-compatible aliases (kept so nothing else in this file has to
# change its decorator name): superuser_required === Super Admin only.
superuser_required = super_admin_required


# Categories that are plain information registers — they don't represent
# physical assets so hardware-specific columns (Status, Brand, Serial No,
# Asset Tag, Current/Previous User/Location) are hidden in the UI and export.
INFO_REGISTER_CATEGORIES = {
    "it vendor", "incident register", "employee", "employee list", "workstation",
    "cpu / system unit", "software / os license", "project details", "project backup",
}

# Categories whose legacy sheet has no real "Name"-like column at all (just
# a Tag/ID + Brand/Type columns) — Asset.name for these is only ever the
# auto-generated "{Category} {tag}" filler (see import_utils._cell_text /
# the "Name" fallback), so the list table shouldn't show it as if it were
# real data. The Tag + Brand/Model (or the category's own extra fields)
# already say everything there is to say about these rows.
NO_REAL_NAME_CATEGORIES = {
    "keyboard", "monitor", "mouse", "ups", "workstation", "software / os license",
}

# The list table's Tag column reads "Tag" by default. For categories whose
# own sheet had its own ID-column name, show that instead (e.g. Hard Disk's
# "Hard disk Number" column, Keyboard's "Keyboard Id" column) — it's the
# same value either way, just labeled the way that category's own data
# actually calls it.
TAG_LABEL_OVERRIDES = {
    "hard disk": "Hard Disk Number",
    "keyboard": "Keyboard Id",
    "monitor": "Monitor Id",
    "mouse": "Mouse Id",
    "ups": "UPS Id",
}

# Records that live in the register but are NOT physical assets. They are left
# out of the dashboard's "Total Active Assets" and status cards.
NON_ASSET_CATEGORIES = {"it vendor", "incident register", "employee", "employee list"}
# Workstation is spec'd as its own first-class module, not a normal Asset
# Category — even though (for now) its Asset rows still carry a real
# AssetCategory("Workstation") FK for backward compatibility with existing
# data (see the inspect_workstation_category management command). Every
# place that lists/offers Asset Categories for browsing, counting, the
# Category Builder, new-asset creation, or exports/imports should exclude
# it; only the dedicated workstation_list view and relations.py's
# cross-reference panels are meant to query it directly by name.
WORKSTATION_CATEGORY_NAME = "workstation"


def is_workstation_category(name):
    return (name or "").strip().lower() == WORKSTATION_CATEGORY_NAME


def order_branch_wise(qs):
    """Records grouped branch by branch — all of the first branch's, then the
    next branch's, and so on (branches in the order they were created:
    Chennai, Tindivanam, ...) — with no-branch records last, and by asset tag
    inside each branch. Used by the category list pages and the Excel export,
    so both show the same order. With a single branch selected it's just
    tag order."""
    return qs.order_by(F("branch_id").asc(nulls_last=True), "asset_tag")


def builtin_category_names():
    """Lower-case names of categories the code treats specially (sheet
    templates, curated fields, status/tag rules...). Their NAME can't change
    and they can't be deleted, or list / import / export logic would break."""
    from ..category_fields import CATEGORY_FIELDS, STATUS_DRIVER_COLUMN, STATUS_ONLY_CATEGORIES
    from ..sheet_templates import CATEGORY_TO_TEMPLATE
    names = {"workstation", "hard disk", "air conditioner", "inside cupboard", "inside the cupboard"}
    for group in (INFO_REGISTER_CATEGORIES, TAG_LABEL_OVERRIDES, NO_REAL_NAME_CATEGORIES,
                  CATEGORY_TO_TEMPLATE, CATEGORY_FIELDS, STATUS_DRIVER_COLUMN, STATUS_ONLY_CATEGORIES):
        names.update(str(n).strip().lower() for n in group)
    return names



def _log_config_change(user, action, model_name, obj_id, obj_repr, diff=None):

    ConfigAuditLog.objects.create(

        changed_by=user,

        action=action,

        model_name=model_name,

        object_id=obj_id,

        object_repr=str(obj_repr)[:255],

        diff=json.dumps(diff or {}),

    )
