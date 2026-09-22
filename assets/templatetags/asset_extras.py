from django import template
from django.utils.safestring import mark_safe
from django.urls import reverse

from assets.category_fields import workstation_lookup_category
from assets.relations import resolve_asset_reference, _clean_ref_tokens

register = template.Library()


@register.filter
def get_item(d, key):
    """Usage: {{ some_dict|get_item:key_variable }}"""
    if not d:
        return ""
    return d.get(key, "")


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
