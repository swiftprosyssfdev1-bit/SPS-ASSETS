from django import template

register = template.Library()


@register.filter
def get_item(d, key):
    """Usage: {{ some_dict|get_item:key_variable }}"""
    if not d:
        return ""
    return d.get(key, "")
