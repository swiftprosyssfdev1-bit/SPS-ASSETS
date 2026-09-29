from django import template
from django.utils.safestring import mark_safe
from django.urls import reverse

from assets.category_fields import workstation_lookup_category, resolve_field_value, _norm_key
from assets.relations import resolve_asset_reference, _clean_ref_tokens

register = template.Library()


@register.filter
def get_item(d, key):
    """Usage: {{ some_dict|get_item:key_variable }}"""
    if not d:
        return ""
    return d.get(key, "")


@register.filter
def xget(d, key):
    """Usage: {{ a.extra_details|xget:"Incident Type" }} — like get_item, but
    also finds the value if it is stored under a differently spelled key
    ("incident_type", "Incident  Type"...)."""
    if not d:
        return ""
    if key in d:
        return d.get(key, "")
    target = _norm_key(key)
    for k, v in d.items():
        if _norm_key(k) == target and str(v or "").strip():
            return v
    return ""


@register.filter
def field_value(asset, field):
    """Usage: {{ asset|field_value:f }} -> the value of category field f."""
    return resolve_field_value(asset, field)


@register.filter
def workstation_lookup_cat(field_label):
    """Usage: {{ field.label|workstation_lookup_cat }} -> AssetCategory name
    to search (e.g. 'Monitor'), or '' if this field is plain text."""
    return workstation_lookup_category(field_label) or ""


@register.filter
def resolve_asset(value, category_name=None):
    """Usage: {{ value|resolve_asset:'Hard Disk' }}"""
    return resolve_asset_reference(value, category_name)


@register.filter
def resolve_tokens(value, category_name=None):
    """Usage: {{ value|resolve_tokens:'Monitor' }}
    Returns list of dicts: [{'token': tok, 'asset': asset_or_none}]
    """
    if not value or not str(value).strip():
        return []
    tokens = _clean_ref_tokens(str(value))
    if not tokens:
        tokens = [str(value).strip()]
    results = []
    for tok in tokens:
        asset = resolve_asset_reference(tok, category_name)
        results.append({'token': tok, 'asset': asset})
    return results

import json
from django.utils.html import escape

@register.filter
def format_config_diff(diff_str):
    if not diff_str or diff_str.strip() == "{}" or diff_str.strip() == "[]":
        return "-"
    try:
        diff_dict = json.loads(diff_str)
        if not diff_dict:
            return "-"
        
        parts = []
        for key, values in diff_dict.items():
            if isinstance(values, list) and len(values) == 2:
                old_val, new_val = values
                old_str = "Empty" if old_val is None or old_val == "" else str(old_val)
                new_str = "Empty" if new_val is None or new_val == "" else str(new_val)
                
                if old_val is None or old_val == "":
                    parts.append(f"<b>{escape(key)}</b>: {escape(new_str)}")
                else:
                    parts.append(f"<b>{escape(key)}</b>: <del class='text-danger'>{escape(old_str)}</del> &rarr; <ins class='text-success text-decoration-none'>{escape(new_str)}</ins>")
            else:
                parts.append(f"<b>{escape(key)}</b>: {escape(str(values))}")
                
        return mark_safe("<br>".join(parts))
    except Exception:
        return diff_str
