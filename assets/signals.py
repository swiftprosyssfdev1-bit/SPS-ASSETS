from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from .models import Asset, AssetHistory, TRACKED_FIELDS, Workstation, WorkstationField


@receiver(pre_save, sender=Asset)
def capture_old_values(sender, instance, **kwargs):
    if not instance.pk:
        return
    try:
        old = Asset.objects.get(pk=instance.pk)
    except Asset.DoesNotExist:
        return
    # per-instance snapshot (no shared module state -> safe across threads/requests)
    instance._history_old_values = {
        f: getattr(old, f) for f in TRACKED_FIELDS
    }
    # keep the "previous" snapshot fields in sync automatically
    if old.current_assigned_to != instance.current_assigned_to:
        instance.previous_assigned_to = old.current_assigned_to
    if old.current_location != instance.current_location:
        instance.previous_location = old.current_location


@receiver(post_save, sender=Asset)
def write_history(sender, instance, created, **kwargs):
    if created:
        AssetHistory.objects.create(
            asset=instance,
            field_name="created",
            old_value="",
            new_value="Asset created",
            changed_by=instance.updated_by,
        )
        return

    old_values = instance.__dict__.pop("_history_old_values", None)
    if not old_values:
        return

    for field in TRACKED_FIELDS:
        old_val = old_values.get(field)
        new_val = getattr(instance, field)
        if old_val != new_val:
            AssetHistory.objects.create(
                asset=instance,
                field_name=field,
                old_value=str(old_val) if old_val is not None else "",
                new_value=str(new_val) if new_val is not None else "",
                changed_by=instance.updated_by,
            )
@receiver(pre_save, sender=Workstation)
def capture_workstation_old_values(sender, instance, **kwargs):
    if not instance.pk:
        return
    try:
        old = Workstation.objects.get(pk=instance.pk)
    except Workstation.DoesNotExist:
        return
    instance._history_old_extra = dict(old.extra_details)

@receiver(post_save, sender=Workstation)
def write_workstation_history(sender, instance, created, **kwargs):
    if created:
        return

    old_extra = instance.__dict__.pop("_history_old_extra", None)
    if old_extra is None:
        return

    all_keys = set(old_extra.keys()) | set(instance.extra_details.keys())
    
    # Pre-fetch field labels so we store 'Processor' instead of 'cpu_number'
    field_map = {f.key: f.label for f in WorkstationField.objects.filter(key__in=all_keys)}

    for key in all_keys:
        old_val = old_extra.get(key)
        new_val = instance.extra_details.get(key)
        
        if isinstance(old_val, list):
            old_val = ", ".join(old_val)
        if isinstance(new_val, list):
            new_val = ", ".join(new_val)
            
        old_str = str(old_val) if old_val is not None else ""
        new_str = str(new_val) if new_val is not None else ""
        
        if old_str != new_str:
            AssetHistory.objects.create(
                asset=instance.asset,
                field_name=field_map.get(key, key),
                old_value=old_str,
                new_value=new_str,
                changed_by=instance.asset.updated_by,
            )
