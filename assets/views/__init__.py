"""Views package. Split from the former single views.py; every public name is re-exported
so `from . import views` / `views.<name>` in urls.py keeps working."""

from .common import (  # noqa: F401
    User,
    superuser_required,
    INFO_REGISTER_CATEGORIES,
    NO_REAL_NAME_CATEGORIES,
    TAG_LABEL_OVERRIDES,
    NON_ASSET_CATEGORIES,
    WORKSTATION_CATEGORY_NAME,
    is_workstation_category,
    order_branch_wise,
    builtin_category_names,
)

from .dashboard import (  # noqa: F401
    BrandedLoginView,
    CATEGORY_ICONS,
    DEFAULT_CATEGORY_ICON,
    clear_all_assets,
    ATTENTION_STATUSES,
    DASHBOARD_CARD_ORDER,
    DASHBOARD_GROUPS,
    DASHBOARD_OTHER_GROUP,
    STATUS_BAR_COLORS,
    CHANGE_FIELD_LABELS,
    dashboard,
    search_assets,
    search_suggestions,
    history_list,
)

from .accounts import (  # noqa: F401
    branch_list,
    branch_create,
    branch_update,
    admin_list,
    admin_create,
    admin_update,
    account_settings,
    export_password_settings,
)

from .assets import (  # noqa: F401
    LIST_SUMMARY_EXCLUDED_FIELDS,
    LIST_SUMMARY_FIELD_OVERRIDES,
    HARDWARE_STATUS_VALUES,
    STATUS_VALUES_BY_CATEGORY,
    category_detail,
    asset_create,
    asset_update,
    asset_detail,
    asset_delete,
    asset_lookup,
    category_edit,
    category_delete,
    category_create,
    deactivated_assets,
    asset_reactivate,
)

from .workstations import (  # noqa: F401
    workstation_list,
    WORKSTATION_STATUS_CHOICES,
    workstation_asset_add,
    workstation_asset_edit,
    workstation_asset_view,
    workstation_rename,
)

from .imports import (  # noqa: F401
    workstation_bulk_import,
    workstation_bulk_import_confirm,
    SESSION_IMPORT_KEY,
    SESSION_IMPORT_BRANCH_KEY,
    SESSION_WORKSTATION_IMPORT_KEY,
    SESSION_WORKSTATION_IMPORT_BRANCH_KEY,
    bulk_import_sample,
    bulk_import,
    bulk_import_confirm,
)

from .exports import (  # noqa: F401
    EXPORT_COLUMNS,
    TEMPLATE_EXPORT_COLUMNS,
    export_assets,
)

from .builder import (  # noqa: F401
    category_builder,
    builder_field_add,
    builder_field_edit,
    builder_field_deactivate,
    builder_field_reactivate,
    builder_field_delete,
    builder_field_reorder,
    workstation_builder,
    workstation_field_add,
    workstation_field_edit,
    workstation_field_deactivate,
    workstation_field_reactivate,
    workstation_field_delete,
    workstation_field_reorder,
    config_audit_log,
    config_audit_log_clear,
)
