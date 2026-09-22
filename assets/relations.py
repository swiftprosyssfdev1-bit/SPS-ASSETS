"""
Asset Relationship & Linking Engine.

Provides centralized resolution of inter-asset relationships:
- Forward resolution: from an asset's fields/extra_details to referenced assets/employees.
- Reverse resolution: finding all other assets across categories that reference this asset.
"""

import re
from typing import List, Dict, Any, Optional
from django.db.models import Q
from .models import Asset, AssetCategory


# Forward relationship mappings: {source_category_normalized: [(source_field_key, label, target_category_name, is_multi)]}
FORWARD_RELATION_CONFIG = {
    "project backup": [
        ("hard disk name", "Stored On (Hard Disk)", "Hard Disk", False),
        ("project manager", "Project Manager", "Employee", True),
        ("backup available hdd", "Backup Mirror HDD", "Hard Disk", False),
    ],
    "workstation": [
        ("cpu number", "Connected CPU", "CPU / System Unit", True),
        ("monitor number", "Connected Monitor(s)", "Monitor", True),
        ("keyboard number", "Connected Keyboard", "Keyboard", False),
        ("mouse number", "Connected Mouse", "Mouse", False),
        ("ups no.", "Connected UPS", "UPS", True),
        ("ups no", "Connected UPS", "UPS", True),
        ("employee id", "Assigned Employee", "Employee", False),
        ("employee name", "Assigned Employee", "Employee", False),
    ],
    "software / os license": [
        ("system no", "Installed On (CPU)", "CPU / System Unit", False),
    ],
    "bluetooth device": [
        ("user name", "Assigned User", "Employee", False),
    ],
}


def _clean_ref_tokens(raw_value: str) -> List[str]:
    """Splits comma/slash/pipe separated reference values into clean individual tokens.
    e.g. '007(195), 032(190)' -> ['007', '032']
    e.g. 'M056 / M063' -> ['M056', 'M063']
    e.g. 'Vinoth | Sreenivasulu' -> ['Vinoth', 'Sreenivasulu']
    e.g. 'CRC006 8TB' -> ['CRC006 8TB', 'CRC006']
    """
    if not raw_value or not str(raw_value).strip():
        return []
    
    text = str(raw_value).strip()
    # Split by commas, slashes, or pipes, but not inside parentheses
    parts = [p.strip() for p in re.split(r'[,/|]', text) if p.strip()]
    cleaned = []
    for part in parts:
        # Strip trailing notes in parentheses e.g. "007(195)" -> "007"
        token = re.sub(r'\(.*?\)', '', part).strip()
        if token and token not in cleaned:
            cleaned.append(token)
    return cleaned


def resolve_asset_reference(token: str, target_category_name: Optional[str] = None) -> Optional[Asset]:
    """Finds an Asset matching a reference token (by asset_tag or name)."""
    if not token or not str(token).strip():
        return None
    
    token = str(token).strip()
    # Ignore generic placeholders
    if token.lower() in {"idle", "none", "not avail", "-", "in office", "in house", "server cabin"}:
        return None
    
    qs = Asset.objects.filter(is_active=True)
    if target_category_name:
        # Match category name case-insensitively
        cat = AssetCategory.objects.filter(name__iexact=target_category_name).first()
        if cat:
            qs = qs.filter(category=cat)
    
    # 1. Exact tag match (case-insensitive)
    match = qs.filter(asset_tag__iexact=token).first()
    if match:
        return match
    
    # 2. If token has capacity suffix like "CRC006 8TB", try stripping " 8TB"
    stripped_token = re.sub(r'\s+\d+(?:tb|gb|mb)$', '', token, flags=re.I).strip()
    if stripped_token != token:
        match = qs.filter(asset_tag__iexact=stripped_token).first()
        if match:
            return match
    
    # 3. For Employee: match name or asset_tag
    if target_category_name == "Employee":
        match = qs.filter(name__iexact=token).first()
        if match:
            return match
        # Match first name or contains
        match = qs.filter(name__icontains=token).first()
        if match:
            return match

    # 4. CPU units sometimes zero-padded or stripped (e.g. '49' vs '049')
    if target_category_name == "CPU / System Unit":
        if token.isdigit():
            padded = token.zfill(3)
            match = qs.filter(asset_tag__iexact=padded).first()
            if match:
                return match
            unpadded = str(int(token))
            match = qs.filter(asset_tag__iexact=unpadded).first()
            if match:
                return match

    return None


def get_forward_relationships(asset: Asset) -> List[Dict[str, Any]]:
    """Returns all outgoing reference links from this asset to other records."""
    if not asset or not asset.category:
        return []
    
    cat_key = asset.category.name.strip().lower()
    configs = FORWARD_RELATION_CONFIG.get(cat_key, [])
    if not configs:
        return []
    
    extra = asset.extra_details or {}
    # Lowercase lookup map for extra_details
    extra_lower = {str(k).strip().lower(): (k, v) for k, v in extra.items()}
    
    results = []
    for field_key, label, target_cat, is_multi in configs:
        raw_val = None
        orig_key = field_key
        
        # Check in extra_details
        if field_key in extra_lower:
            orig_key, raw_val = extra_lower[field_key]
        # Check standard model fields
        elif hasattr(asset, field_key):
            raw_val = getattr(asset, field_key)
        
        if not raw_val or not str(raw_val).strip():
            continue
        
        raw_str = str(raw_val).strip()
        tokens = _clean_ref_tokens(raw_str) if is_multi else [raw_str]
        
        matched_assets = []
        unmatched_tokens = []
        
        for tok in tokens:
            resolved = resolve_asset_reference(tok, target_cat)
            if resolved:
                if resolved not in matched_assets:
                    matched_assets.append(resolved)
            else:
                unmatched_tokens.append(tok)
        
        if matched_assets or unmatched_tokens:
            results.append({
                "field_key": orig_key,
                "label": label,
                "target_category": target_cat,
                "raw_value": raw_str,
                "matched_assets": matched_assets,
                "unmatched_tokens": unmatched_tokens,
            })
            
    return results


def get_reverse_relationships(asset: Asset) -> List[Dict[str, Any]]:
    """Returns all incoming links from other records that reference this asset."""
    if not asset or not asset.category:
        return []
    
    cat_name = asset.category.name.strip()
    tag = asset.asset_tag.strip()
    name = asset.name.strip()
    
    reverse_groups = []
    
    # 1. If this is a Hard Disk -> Find Project Backups stored on it or using it as mirror
    if cat_name == "Hard Disk":
        # Search Project Backups where extra_details has this tag
        backups = []
        for pb in Asset.objects.filter(category__name="Project Backup", is_active=True):
            ex = pb.extra_details or {}
            hdd = str(ex.get("Hard disk Name") or "").strip()
            mirror = str(ex.get("Backup Available HDD") or "").strip()
            
            is_stored_on = (resolve_asset_reference(hdd, "Hard Disk") == asset)
            is_mirror = (resolve_asset_reference(mirror, "Hard Disk") == asset)
            
            if is_stored_on or is_mirror:
                role = "Stored Backup" if is_stored_on else "Backup Mirror"
                pm_text = ex.get("Project Manager", "-")
                date_text = ex.get("Date", "-")
                backups.append({
                    "asset": pb,
                    "title": ex.get("Projects", pb.name),
                    "subtitle": f"PM: {pm_text} | Date: {date_text}" if pm_text != "-" or date_text != "-" else "",
                    "role": role,
                })
        if backups:
            reverse_groups.append({
                "title": "Stored Project Backups",
                "badge": "Project Backup",
                "icon": "bi-archive",
                "items": backups,
            })

    # 2. If this is a CPU / System Unit -> Find connected Workstations and installed Software
    elif cat_name == "CPU / System Unit":
        workstations = []
        for ws in Asset.objects.filter(category__name="Workstation", is_active=True):
            ex = ws.extra_details or {}
            cpu_val = str(ex.get("CPU Number") or "")
            for tok in _clean_ref_tokens(cpu_val):
                if resolve_asset_reference(tok, "CPU / System Unit") == asset:
                    workstations.append({
                        "asset": ws,
                        "title": ws.name,
                        "subtitle": f"User: {ex.get('Employee Name') or ex.get('Employee ID') or '-'}",
                        "role": "Host Workstation",
                    })
                    break
        if workstations:
            reverse_groups.append({
                "title": "Connected Workstations",
                "badge": "Workstation",
                "icon": "bi-pc-display",
                "items": workstations,
            })
            
        software = []
        for sw in Asset.objects.filter(category__name="Software / OS License", is_active=True):
            ex = sw.extra_details or {}
            sys_val = str(ex.get("System No") or "")
            if resolve_asset_reference(sys_val, "CPU / System Unit") == asset:
                software.append({
                    "asset": sw,
                    "title": f"{ex.get('Type', '')} {ex.get('Version', '')}".strip() or sw.name,
                    "subtitle": f"Product Key: {ex.get('Product Key', '-')}",
                    "role": "Installed License",
                })
        if software:
            reverse_groups.append({
                "title": "Installed Software & OS Licenses",
                "badge": "Software / OS",
                "icon": "bi-code-square",
                "items": software,
            })

    # 3. If this is Monitor / Keyboard / Mouse / UPS -> Find connected Workstations
    elif cat_name in {"Monitor", "Keyboard", "Mouse", "UPS"}:
        field_map = {
            "Monitor": "Monitor Number",
            "Keyboard": "Keyboard Number",
            "Mouse": "Mouse Number",
            "UPS": "UPS No.",
        }
        field_key = field_map[cat_name]
        workstations = []
        for ws in Asset.objects.filter(category__name="Workstation", is_active=True):
            ex = ws.extra_details or {}
            val = str(ex.get(field_key) or ex.get(field_key.replace(".", "")) or "")
            for tok in _clean_ref_tokens(val):
                if resolve_asset_reference(tok, cat_name) == asset:
                    workstations.append({
                        "asset": ws,
                        "title": ws.name,
                        "subtitle": f"User: {ex.get('Employee Name') or ex.get('Employee ID') or '-'}",
                        "role": f"Attached {cat_name}",
                    })
                    break
        if workstations:
            reverse_groups.append({
                "title": "Connected Workstations",
                "badge": "Workstation",
                "icon": "bi-pc-display",
                "items": workstations,
            })

    # 4. If this is Employee -> Find assigned Workstations, managed Project Backups, Bluetooth
    elif cat_name == "Employee":
        # Workstations
        workstations = []
        for ws in Asset.objects.filter(category__name="Workstation", is_active=True):
            ex = ws.extra_details or {}
            eid = str(ex.get("Employee ID") or "").strip()
            ename = str(ex.get("Employee Name") or "").strip()
            if (eid and resolve_asset_reference(eid, "Employee") == asset) or \
               (ename and resolve_asset_reference(ename, "Employee") == asset):
                workstations.append({
                    "asset": ws,
                    "title": ws.name,
                    "subtitle": f"Purpose: {ex.get('Purposes', '-')}",
                    "role": "Assigned Workstation",
                })
        if workstations:
            reverse_groups.append({
                "title": "Assigned Workstations",
                "badge": "Workstation",
                "icon": "bi-pc-display",
                "items": workstations,
            })

        # Project Backups managed
        backups = []
        for pb in Asset.objects.filter(category__name="Project Backup", is_active=True):
            ex = pb.extra_details or {}
            pm = str(ex.get("Project Manager") or "").strip()
            for tok in _clean_ref_tokens(pm):
                if resolve_asset_reference(tok, "Employee") == asset:
                    date_text = ex.get("Date", "-")
                    backups.append({
                        "asset": pb,
                        "title": ex.get("Projects", pb.name),
                        "subtitle": f"Date: {date_text}" if date_text != "-" else "",
                        "role": "Project Manager",
                    })
                    break
        if backups:
            reverse_groups.append({
                "title": "Managed Project Backups",
                "badge": "Project Backup",
                "icon": "bi-archive",
                "items": backups,
            })

        # Bluetooth
        bluetooth = []
        for bt in Asset.objects.filter(category__name="Bluetooth Device", is_active=True):
            ex = bt.extra_details or {}
            user = str(ex.get("User Name") or "").strip()
            if resolve_asset_reference(user, "Employee") == asset:
                bluetooth.append({
                    "asset": bt,
                    "title": bt.name,
                    "subtitle": f"Brand: {ex.get('Brand', '-')}",
                    "role": "Assigned Device",
                })
        if bluetooth:
            reverse_groups.append({
                "title": "Assigned Bluetooth Devices",
                "badge": "Bluetooth",
                "icon": "bi-bluetooth",
                "items": bluetooth,
            })

    return reverse_groups
