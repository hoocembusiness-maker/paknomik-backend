import re

from django.db import migrations, models


_OLD_TERMS = re.compile(r"pakdinar|pako", re.IGNORECASE)


def _replace_term(match):
    value = match.group(0)
    replacement = "pragmadinar"
    if value.isupper():
        return replacement.upper()
    if value[:1].isupper():
        return replacement.capitalize()
    return replacement


def _rename_nested(value):
    if isinstance(value, str):
        return _OLD_TERMS.sub(_replace_term, value)
    if isinstance(value, list):
        return [_rename_nested(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_rename_nested(item) for item in value)
    if isinstance(value, dict):
        return {
            _rename_nested(key): _rename_nested(item)
            for key, item in value.items()
        }
    return value


def rename_persisted_terms(apps, schema_editor):
    """Rename old wallet terminology throughout persisted text and JSON fields."""
    from django.db.models import CharField, JSONField, TextField

    for model in apps.get_models():
        if not model._meta.managed:
            continue
        fields = [
            field
            for field in model._meta.concrete_fields
            if isinstance(field, (CharField, TextField, JSONField))
            and not field.primary_key
        ]
        if not fields:
            continue
        field_names = [field.name for field in fields]
        pending = []
        for instance in model.objects.only("pk", *field_names).iterator(chunk_size=500):
            changed_fields = []
            for field in fields:
                old_value = getattr(instance, field.name)
                new_value = _rename_nested(old_value)
                if new_value != old_value:
                    setattr(instance, field.name, new_value)
                    changed_fields.append(field.name)
            if changed_fields:
                pending.append((instance, changed_fields))
            if len(pending) >= 500:
                _bulk_update(model, pending)
        if pending:
            _bulk_update(model, pending)


def _bulk_update(model, pending):
    fields = sorted({field for _, changed in pending for field in changed})
    model.objects.bulk_update([instance for instance, _ in pending], fields, batch_size=500)
    pending.clear()


def seed_pragmadinar_data(apps, schema_editor):
    """Seed defaults only for a fresh database; preserve configured catalog data."""
    from user.models import PragmadinarService
    from user.pragmadinar import seed_pragmadinar_catalog

    if not PragmadinarService.objects.exists():
        seed_pragmadinar_catalog()


class Migration(migrations.Migration):
    dependencies = [("user", "0090_digital_presence")]

    operations = [
        migrations.RenameModel("PakdinarService", "PragmadinarService"),
        migrations.RenameModel("PakdinarWallet", "PragmadinarWallet"),
        migrations.RenameModel("PakdinarLedger", "PragmadinarLedger"),
        migrations.RenameModel(
            "PakdinarRechargeRequest", "PragmadinarRechargeRequest"
        ),
        migrations.RenameField(
            model_name="pragmadinarservice",
            old_name="cost_pakdinar",
            new_name="cost_pragmadinar",
        ),
        migrations.RenameField(
            model_name="campaign",
            old_name="budget_pako",
            new_name="budget_pragmadinar",
        ),
        migrations.RenameField(
            model_name="campaign",
            old_name="spent_pako",
            new_name="spent_pragmadinar",
        ),
        migrations.RenameField(
            model_name="marketreport",
            old_name="cost_pako",
            new_name="cost_pragmadinar",
        ),
        migrations.RemoveConstraint(
            model_name="pragmadinarledger",
            name="pako_ledger_unique_idempotency",
        ),
        migrations.AddConstraint(
            model_name="pragmadinarledger",
            constraint=models.UniqueConstraint(
                condition=~models.Q(idempotency_key=""),
                fields=("wallet", "idempotency_key"),
                name="pragmadinar_ledger_unique_idempotency",
            ),
        ),
        migrations.RunPython(rename_persisted_terms, migrations.RunPython.noop),
        migrations.RunPython(seed_pragmadinar_data, migrations.RunPython.noop),
    ]
