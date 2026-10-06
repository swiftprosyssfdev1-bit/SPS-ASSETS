"""Automatic asset names - ONE rule, used by the Add/Edit form, the create /
update views, the Excel import and the Workstation module.

Asset.name is a required column, but most categories do not have a real
"Name" field (the Category Builder only shows one when the admin adds it).
So when nobody types a name, one is generated:

    Brand (+ Model)      "Dell Latitude 5420"     when the record has a Brand
    Category + Tag       "Laptop L001"            otherwise
    Category             "Laptop"                 when there is no Tag yet
    "Asset"                                       last resort

Project Details is the exception: the project name IS the tag.

An auto-generated name is kept in step with the record: when its Tag / Brand /
Model / Category changes, a name that was still the auto-generated one is
generated again. A name somebody typed is never touched.

Nothing in this module touches the database.
"""
import re

NAME_MAX_LENGTH = 150   # Asset.name max_length

# Categories whose name is simply the tag (the project's name is its ID).
NAME_IS_TAG_CATEGORIES = {"project details"}


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def generate_asset_name(category_name, asset_tag="", brand="", model_number=""):
    """The automatic name for a record with these values."""
    cat, tag = _clean(category_name), _clean(asset_tag)
    brand, model = _clean(brand), _clean(model_number)
    if cat.lower() in NAME_IS_TAG_CATEGORIES and tag:
        name = tag
    elif brand:
        name = f"{brand} {model}".strip()
    elif cat and tag:
        name = f"{cat} {tag}"
    elif cat:
        name = cat
    else:
        name = "Asset"
    return name[:NAME_MAX_LENGTH].rstrip()


def auto_name_candidates(category_name, asset_tag="", brand="", model_number=""):
    """Every name this record could carry WITHOUT anybody having typed it.

    Besides today's rule that includes the older fillers: the Excel import
    used to write "<Category> <tag>" even when a Brand was present, a tag
    that was stored with a "-2" suffix (same ID on two sheets) gave
    "<Category> <tag without suffix>", and the bare category / "Asset"."""
    cat, tag = _clean(category_name), _clean(asset_tag)
    names = {generate_asset_name(cat, tag, brand, model_number)}
    base = re.sub(r"-\d+$", "", tag)
    for t in {tag, base}:
        if cat and t:
            names.add(f"{cat} {t}"[:NAME_MAX_LENGTH].rstrip())
    names.update({cat, "Asset"} - {""})
    return names


def is_auto_name(name, category_name, asset_tag="", brand="", model_number=""):
    """True when `name` is blank or is one the system would have generated for
    these values (so it is safe to generate it again)."""
    text = _clean(name)
    if not text:
        return True
    return text in auto_name_candidates(category_name, asset_tag, brand, model_number)


def refreshed_name(name, old, new):
    """The name to store when a record is edited.

    name: the submitted name; old / new: (category_name, tag, brand, model)
    tuples before and after the edit.

    * blank                                   -> generated from the new values
    * an auto-generated name whose inputs changed -> generated again
    * anything else (typed by a person, or nothing changed) -> kept as is"""
    if not _clean(name):
        return generate_asset_name(*new)
    if tuple(map(_clean, old)) != tuple(map(_clean, new)) and is_auto_name(name, *old):
        return generate_asset_name(*new)
    return name
