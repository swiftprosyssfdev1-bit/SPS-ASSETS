from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from .models import Asset, AssetHistory, TRACKED_FIELDS


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
