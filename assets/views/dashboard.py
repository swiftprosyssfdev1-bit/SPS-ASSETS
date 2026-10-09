"""Login, dashboard, global search and the change-history list."""
import re
import types

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render

from ..category_fields import (
    get_builder_layout,
)
from ..context_processors import workstation_labels
from ..models import Asset, AssetCategory, AssetHistory, Workstation, prime_display_tags
from ..permissions import (
    filter_assets_to_branch,
    get_accessible_branches,
    is_super_admin,
    resolve_selected_branch,
)
from .common import (
    NON_ASSET_CATEGORIES,
    WORKSTATION_CATEGORY_NAME,
    is_workstation_category,
    superuser_required,
)


class BrandedLoginView(LoginView):
    template_name = "assets/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
            user = form.get_user()

            # Only staff users can access the Asset Register
            if not user.is_staff:
                form.add_error(None, "You do not have permission to access the Asset Register.")
                return self.form_invalid(form)

            response = super().form_valid(form)

            if self.request.POST.get("remember_me"):
                # Keep the session alive for 2 weeks
                self.request.session.set_expiry(1209600)
            else:
                # Expire when the browser closes
                self.request.session.set_expiry(0)
            return response


# Fallback icon per category name (Bootstrap Icons class) — only used when a
# category doesn't have its own "icon" set via the Add Category form.
CATEGORY_ICONS = {
    "Air Conditioner": "bi-snow",
    "Biometric Device": "bi-fingerprint",
    "Bluetooth Device": "bi-bluetooth",
    "CPU / System Unit": "bi-cpu",
    "Hard Disk": "bi-device-hdd",
    "Keyboard": "bi-keyboard",
    "Laptop": "bi-laptop",
    "Monitor": "bi-display",
    "Mouse": "bi-mouse2",
    "Networking Equipment": "bi-router",
    "Other Asset": "bi-box-seam",
    "Software / OS License": "bi-file-earmark-lock",
    "UPS": "bi-battery-charging",
    "Workstation": "bi-pc-display-horizontal",
}
DEFAULT_CATEGORY_ICON = "bi-hdd-stack"


@login_required
@superuser_required
def clear_all_assets(request):
    if not getattr(settings, "ALLOW_CLEAR_ALL", True):
        messages.error(request, "Clear all records is disabled in this environment.")
        return redirect("assets:dashboard")

    if request.method == "POST":
        confirm_text = request.POST.get("confirm_text", "").strip()
        admin_password = request.POST.get("admin_password", "")

        if confirm_text != "DELETE ALL ASSETS":
            messages.error(request, "Confirmation text did not match 'DELETE ALL ASSETS'. Nothing was deleted.")
            return redirect("assets:dashboard")

        if not request.user.check_password(admin_password):
            messages.error(request, "Incorrect administrator password. Deletion cancelled.")
            return redirect("assets:dashboard")

        with transaction.atomic():
            count = Asset.objects.count()
            # Workstation rows hold a PROTECTed FK to Asset, so they must be
            # cleared first or the bulk Asset delete below raises
            # ProtectedError and the whole "Clear All" action fails.
            Workstation.objects.all().delete()
            Asset.objects.all().delete()
        messages.success(request, f"Cleared all {count} asset record(s) and their audit history.")
        return redirect("assets:dashboard")
    return redirect("assets:dashboard")


# Statuses that genuinely need someone's attention. Active / Idle / Running /
# Open etc. are normal states and must NOT be counted in the red badge.
ATTENTION_STATUSES = ["not_working", "service", "missing"]


# Order of the category cards on the dashboard (normalized names). Categories
# not listed here (e.g. custom ones) go after these, A-Z, but before Other
# Asset, which is always last. Edit this list to reorder the cards.
DASHBOARD_CARD_ORDER = [
    "workstation",
    "cpu / system unit",
    "monitor",
    "keyboard",
    "mouse",
    "ups",
    "laptop",
    "hard disk",
    "software / os license",
    "software - os license",
]


def _is_hand_built_category(category):
    """Categories whose list / View / export are still hand-built (not driven
    by the Category Builder yet)."""
    n = category.name.strip().lower()
    return is_workstation_category(category.name)


def _dashboard_card_sort_key(cat):
    name = cat.name.strip().lower()
    if name.rstrip("s") == "other asset":
        return (2, 0, "")
    if name in DASHBOARD_CARD_ORDER:
        return (0, DASHBOARD_CARD_ORDER.index(name), "")
    return (1, 0, name)


# --- Dashboard layout helpers (grouped cards, charts, change text) ---------
DASHBOARD_GROUPS = [
    ("Computing", ["workstation", "cpu / system unit", "monitor", "laptop"]),
    ("Peripherals and power", ["keyboard", "mouse", "ups", "bluetooth device"]),
]
DASHBOARD_OTHER_GROUP = "Storage, software and other"

STATUS_BAR_COLORS = {
    "working": "#2e7d32", "active": "#2e7d32", "running": "#2e7d32",
    "not_working": "#c62828", "stopped": "#c62828", "resigned": "#c62828",
    "idle": "#9e9e9e", "scrap": "#424242", "missing": "#ad1457",
    "service": "#F7941D", "open": "#fd7e14", "resolved": "#0d6efd",
    "completed": "#0d6efd",
}
CHANGE_FIELD_LABELS = {
    "status": "Status", "current_assigned_to": "Assigned to",
    "current_location": "Location", "brand": "Brand", "model_number": "Model",
    "serial_number": "Serial no.", "is_active": "Active",
}


def _short(value, limit=32):
    value = (value or "").strip()
    if not value:
        return "\u2014"
    return value if len(value) <= limit else value[: limit - 1] + "\u2026"


def _change_text(h):
    """'Status: Working \u2192 Not Working' for one AssetHistory row."""
    if h.field_name == "created":
        return (h.new_value or "").strip() or "Asset created"
    if h.field_name == "is_active" and (h.new_value or "").strip().lower() == "false":
        return "Asset deactivated"
    if h.field_name == "is_active" and (h.new_value or "").strip().lower() == "true":
        return "Asset activated"
    label = CHANGE_FIELD_LABELS.get(h.field_name) or h.field_name.replace("_", " ").capitalize()
    old, new = h.old_value, h.new_value
    if h.field_name == "status":
        names = dict(Asset.STATUS_CHOICES)
        old, new = names.get(old, old), names.get(new, new)
    return f"{label}: {_short(old)} \u2192 {_short(new)}"


@login_required
def dashboard(request):
    selected_branch, accessible_branches = resolve_selected_branch(request)
    if not is_super_admin(request.user) and not accessible_branches.exists():
        messages.warning(
            request,
            "Your account has no branches assigned yet. Contact the Super Admin.",
        )

    if selected_branch is not None:
        branch_filter = Q(assets__branch=selected_branch)
    else:
        branch_filter = Q(assets__branch__in=accessible_branches)

    categories = (
        AssetCategory.objects.exclude(name__iexact=WORKSTATION_CATEGORY_NAME).annotate(
            total=Count("assets", filter=Q(assets__is_active=True) & branch_filter),
            not_working=Count(
                "assets",
                filter=Q(assets__is_active=True) & Q(assets__status__in=ATTENTION_STATUSES) & branch_filter,
            ),
        ).order_by("name")
    )

    def normalized(name):
        return name.strip().lower().rstrip("s")

    main_categories = []
    grouped_categories = []
    for cat in categories:
        # Known categories always use their mapped icon (avoids bad/blank
        # values sitting in the DB from before this field existed). Only a
        # brand-new custom category falls through to whatever icon it was
        # given in the Add Category form.
        cat.icon_class = CATEGORY_ICONS.get(cat.name) or (cat.icon or "").strip() or DEFAULT_CATEGORY_ICON
        cat.sub_categories = []
        # The "Other Asset(s)" category is always the container itself, even
        # if its own "show under other" box was accidentally ticked — it can
        # never be one of the items grouped inside itself.
        if cat.show_under_other and normalized(cat.name) != "other asset":
            grouped_categories.append(cat)
        else:
            main_categories.append(cat)

    # Attach the grouped categories as a dropdown on the "Other Asset" card
    # instead of showing them under a separate synthetic "Other" card.
    other_asset_card = next(
        (c for c in main_categories if normalized(c.name) == "other asset"), None
    )
    if other_asset_card is not None:
        other_asset_card.sub_categories = grouped_categories
    else:
        # No "Other Asset" category exists yet — fall back to its own card
        # so the grouped categories are still reachable.
        for cat in grouped_categories:
            main_categories.append(cat)

    # A builder category with no Status field has no visible status, so it must
    # not show a "need attention" badge driven by the hidden one.
    for _c in main_categories:
        if _c.not_working and not _is_hand_built_category(_c):
            _lay = get_builder_layout(_c)
            if _lay is not None and "status" not in _lay["common_fields"]:
                _c.not_working = 0
    main_categories.sort(key=_dashboard_card_sort_key)

    branch_scoped = Asset.objects.filter(is_active=True)
    branch_scoped = filter_assets_to_branch(
        branch_scoped, selected_branch, accessible_branches,
        include_unassigned=is_super_admin(request.user) and selected_branch is None,
    )

    total_records = branch_scoped.count()
    # Workstation gets its own summary card, computed straight from Asset
    # rows (same pattern relations.py uses) instead of coming from the
    # AssetCategory queryset above, since that queryset now deliberately
    # excludes Workstation.
    workstation_qs = branch_scoped.filter(category__name__iexact=WORKSTATION_CATEGORY_NAME)
    workstation_summary = {
        "total": workstation_qs.count(),
        "not_working": workstation_qs.filter(status__in=ATTENTION_STATUSES).count(),
    }
    non_asset_ids = [
        c.id for c in AssetCategory.objects.all()
        if c.name.strip().lower() in NON_ASSET_CATEGORIES
    ]
    real_assets = branch_scoped.exclude(category_id__in=non_asset_ids)
    total_assets = real_assets.count()
    status_breakdown = (
        real_assets
        .values("status")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    branch_scoped_all = filter_assets_to_branch(
        Asset.objects.all(), selected_branch, accessible_branches,
        include_unassigned=is_super_admin(request.user) and selected_branch is None,
    )
    recent_changes = (
        AssetHistory.objects
        .select_related("asset", "changed_by", "asset__branch")
        .filter(asset__in=branch_scoped_all)
        # Dashboard is a clean, at-a-glance summary — leave out entries for
        # assets whose tag was auto-suffixed to dodge a collision during
        # import (e.g. "7" and "7-2" from two stacked vendor lists in one
        # sheet). That's real data, not an error, but showing the raw "-2"
        # tag here reads as a glitch. Nothing is hidden from the full
        # history: /history has every row, unfiltered, with full detail.
        [:60]
    )

    # Leave out entries whose SHOWN tag still ends in -2 / -3 (a same-sheet
    # collision, reads as a glitch). A tag shared with another category, e.g.
    # SPS011-2 shown as SPS011, is fine and stays. /history has everything.
    prime_display_tags([h.asset for h in recent_changes])
    recent_changes = [h for h in recent_changes if not re.search(r"-\d+$", h.asset.display_tag or "")][:15]
    # ---- Redesigned dashboard: grouped cards, attention list, charts ----
    ws_name = workstation_labels()[0]
    workstation_card = types.SimpleNamespace(
        id=None, name=ws_name, total=workstation_summary["total"],
        not_working=workstation_summary["not_working"],
        icon_class="bi-pc-display", sub_categories=[], is_workstation=True,
    )
    all_cards = list(main_categories)
    for _c in main_categories:
        _c.is_workstation = False
        all_cards.extend(_c.sub_categories)
    for _c in all_cards:
        _c.is_workstation = False

    def _group_key(cat):
        return re.sub(r"\s+", " ", cat.name.strip().lower())

    group_buckets = {title: [] for title, _ in DASHBOARD_GROUPS}
    group_buckets[DASHBOARD_OTHER_GROUP] = []
    pool = list(main_categories)
    if workstation_summary["total"] or workstation_summary["not_working"]:
        pool.insert(0, workstation_card)
    for cat in pool:
        key = "workstation" if getattr(cat, "is_workstation", False) else _group_key(cat)
        placed = False
        for title, names in DASHBOARD_GROUPS:
            if key in names:
                group_buckets[title].append((names.index(key), cat))
                placed = True
                break
        if not placed:
            group_buckets[DASHBOARD_OTHER_GROUP].append((999, cat))
    category_groups = []
    for title in [t for t, _ in DASHBOARD_GROUPS] + [DASHBOARD_OTHER_GROUP]:
        items = [c for _, c in sorted(group_buckets[title], key=lambda x: x[0])]
        if items:
            category_groups.append({"title": title, "items": items})

    attention_total = sum(c.not_working for c in all_cards) + workstation_summary["not_working"]
    attention_cat_ids = [c.id for c in all_cards if c.not_working]
    if workstation_summary["not_working"]:
        attention_cat_ids += list(
            AssetCategory.objects.filter(name__iexact=WORKSTATION_CATEGORY_NAME)
            .values_list("id", flat=True)
        )
    attention_rows = []
    if attention_cat_ids:
        attention_rows = prime_display_tags(
            real_assets.filter(status__in=ATTENTION_STATUSES, category_id__in=attention_cat_ids)
            .select_related("category", "branch")
            .order_by("-updated_at", "-id")[:5]
        )
    attention_items = [
        {
            "id": a.id, "name": a.display_name, "tag": a.display_tag,
            "category": a.category.name, "status": a.get_status_display(),
            "branch": a.branch.name if a.branch_id else "",
        }
        for a in attention_rows
    ]

    status_names = dict(Asset.STATUS_CHOICES)
    status_rows = list(status_breakdown)[:6]
    status_max = max([r["count"] for r in status_rows] or [1])
    status_chart = [
        {
            "label": status_names.get(r["status"], r["status"] or "Not set"),
            "count": r["count"],
            "pct": max(4, round(r["count"] * 100 / status_max)),
            "color": STATUS_BAR_COLORS.get(r["status"], "#6c757d"),
        }
        for r in status_rows
    ]
    # ---- KPI strip + status donut (dashboard header) ----
    healthy_total = sum(r["count"] for r in status_breakdown if r["status"] in ("working", "active"))
    _shown_total = sum(r["count"] for r in status_chart) or 1
    _acc, _stops = 0.0, []
    for r in status_chart:
        r["share"] = round(r["count"] * 100 / _shown_total)
        _end = _acc + r["count"] * 100 / _shown_total
        _stops.append("%s %.2f%% %.2f%%" % (r["color"], _acc, _end))
        _acc = _end
    donut_css = "conic-gradient(" + ", ".join(_stops) + ")" if _stops else ""
    branch_counts = {
        r["branch_id"]: r["count"]
        for r in real_assets.values("branch_id").annotate(count=Count("id"))
    }
    branch_rows = [
        {"label": b.name, "count": branch_counts.get(b.id, 0)}
        for b in accessible_branches
    ]
    if is_super_admin(request.user) and selected_branch is None and branch_counts.get(None):
        branch_rows.append({"label": "Unassigned", "count": branch_counts[None]})
    branch_rows.sort(key=lambda r: -r["count"])
    branch_max = max([r["count"] for r in branch_rows] or [1]) or 1
    branch_chart = [
        {
            "label": r["label"], "count": r["count"],
            "pct": max(3, round(r["count"] * 100 / branch_max)) if r["count"] else 0,
        }
        for r in branch_rows
    ]
    for h in recent_changes:
        h.change_text = _change_text(h)

    allow_clear_all = bool(getattr(settings, "ALLOW_CLEAR_ALL", True) and request.user.is_superuser)
    context = {
        "main_categories": main_categories,
        "category_groups": category_groups,
        "attention_items": attention_items,
        "attention_total": attention_total,
        "attention_more": max(0, attention_total - len(attention_items)),
        "status_chart": status_chart,
        "donut_css": donut_css,
        "kpi_total": total_assets,
        "kpi_healthy": healthy_total,
        "kpi_healthy_pct": round(healthy_total * 100 / total_assets) if total_assets else 0,
        "kpi_categories": sum(len(g["items"]) for g in category_groups),
        "kpi_branches": 1 if selected_branch else (len(branch_rows) or accessible_branches.count()),
        "branch_chart": branch_chart if len(branch_chart) > 1 else [],
        "workstation_summary": workstation_summary,
        "total_assets": total_assets,
        "total_records": total_records,
        "status_breakdown": status_breakdown,
        "recent_changes": recent_changes,
        "allow_clear_all": allow_clear_all,
        "accessible_branches": accessible_branches,
        "selected_branch": selected_branch,
        "is_super_admin": is_super_admin(request.user),
    }
    return render(request, "assets/dashboard.html", context)


@login_required
def search_assets(request):
    """Quick search across assets and categories by category name, tag,
    name, brand, serial number, model number, assigned user or location."""
    query = request.GET.get("q", "").strip()
    results = []
    matching_categories = []
    accessible_branches = get_accessible_branches(request.user)
    branch_scope_q = Q(branch__in=accessible_branches)
    if is_super_admin(request.user):
        branch_scope_q |= Q(branch__isnull=True)
    if query:
        matching_categories = list(
            AssetCategory.objects.filter(name__icontains=query)
            .annotate(total=Count(
                "assets", filter=Q(assets__is_active=True) & (Q(assets__branch__in=accessible_branches) | Q(assets__branch__isnull=True) if is_super_admin(request.user) else Q(assets__branch__in=accessible_branches))
            ))
            .order_by("name")
        )
        for cat in matching_categories:
            cat.icon_class = CATEGORY_ICONS.get(cat.name, cat.icon or DEFAULT_CATEGORY_ICON)

        results = (
            Asset.objects.filter(is_active=True).filter(branch_scope_q)
            .filter(
                Q(category__name__icontains=query)
                | Q(asset_tag__icontains=query)
                | Q(name__icontains=query)
                | Q(brand__icontains=query)
                | Q(serial_number__icontains=query)
                | Q(model_number__icontains=query)
                | Q(current_assigned_to__icontains=query)
                | Q(current_location__icontains=query)
                | Q(notes__icontains=query)
            )
            .select_related("category", "branch")
            .order_by("category__name", "asset_tag")[:100]
        )
    results = prime_display_tags(results)
    return render(request, "assets/search_results.html", {
        "query": query,
        "results": results,
        "matching_categories": matching_categories,
    })


@login_required
def search_suggestions(request):
    """JSON API endpoint providing instant autocomplete suggestions for categories and assets."""
    query = request.GET.get("q", "").strip()
    if not query:
        return JsonResponse({"categories": [], "assets": []})

    accessible_branches = get_accessible_branches(request.user)

    categories = list(
        AssetCategory.objects.filter(name__icontains=query)
        .annotate(total=Count(
            "assets", filter=Q(assets__is_active=True) & Q(assets__branch__in=accessible_branches)
        ))
        .order_by("name")[:5]
    )
    cat_data = [
        {
            "id": c.id,
            "name": c.name,
            "total": c.total,
            "icon": CATEGORY_ICONS.get(c.name, c.icon or DEFAULT_CATEGORY_ICON),
        }
        for c in categories
    ]

    assets = (
        Asset.objects.filter(is_active=True, branch__in=accessible_branches)
        .filter(
            Q(asset_tag__icontains=query)
            | Q(name__icontains=query)
            | Q(brand__icontains=query)
            | Q(serial_number__icontains=query)
            | Q(model_number__icontains=query)
            | Q(current_assigned_to__icontains=query)
            | Q(current_location__icontains=query)
            | Q(category__name__icontains=query)
        )
        .select_related("category")
        .order_by("category__name", "asset_tag")[:8]
    )
    asset_data = [
        {
            "id": a.id,
            "tag": a.asset_tag,
            "name": a.name,
            "category": a.category.name,
            "assigned_to": a.current_assigned_to or "",
            "location": a.current_location or "",
            "status": a.get_status_display(),
        }
        for a in assets
    ]

    return JsonResponse({
        "query": query,
        "categories": cat_data,
        "assets": asset_data,
    })


@login_required
def history_list(request):
    """Full change history ("View all" from the dashboard's Recent Changes),
    newest first, paginated, with optional search / category / field filters."""
    query = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "").strip()
    field = request.GET.get("field", "").strip()

    selected_branch, accessible_branches = resolve_selected_branch(request)
    if selected_branch:
        entries = AssetHistory.objects.filter(asset__branch=selected_branch)
    else:
        branch_q = Q(asset__branch__in=accessible_branches)
        if is_super_admin(request.user):
            branch_q |= Q(asset__branch__isnull=True)
        entries = AssetHistory.objects.filter(branch_q)

    entries = (
        entries
        .select_related("asset", "asset__category", "asset__branch", "changed_by")
    )
    if query:
        entries = entries.filter(
            Q(asset__asset_tag__icontains=query) | Q(asset__name__icontains=query)
            | Q(old_value__icontains=query) | Q(new_value__icontains=query)
        )
    if category_id.isdigit():
        entries = entries.filter(asset__category_id=int(category_id))
    if field:
        entries = entries.filter(field_name=field)
    entries = entries.order_by("-changed_at", "-id")

    page = Paginator(entries, 50).get_page(request.GET.get("page"))
    prime_display_tags([h.asset for h in page])

    # keep filters when moving between pages
    params = request.GET.copy()
    params.pop("page", None)

    return render(request, "assets/history.html", {
        "page": page,
        "query": query,
        "category_id": category_id,
        "field": field,
        "categories": AssetCategory.objects.order_by("name"),
        "fields": AssetHistory.objects.order_by().values_list("field_name", flat=True).distinct(),
        "querystring": params.urlencode(),
    })
