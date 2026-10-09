from django.db import migrations

COMMISSIONS_READ = "commissions.read"
COMMISSIONS_MANAGE = "commissions.manage"
ROLE_PERMISSIONS = {
    "ULTRA_ADMIN": [COMMISSIONS_READ, COMMISSIONS_MANAGE],
    "ADMIN_FINANCE": [COMMISSIONS_READ, COMMISSIONS_MANAGE],
    "ADMIN_OPERATIONS": [COMMISSIONS_READ],
}


def forwards(apps, schema_editor):
    Order = apps.get_model("user", "Order")
    PlatformRole = apps.get_model("user", "PlatformRole")
    PlatformSetting = apps.get_model("user", "PlatformSetting")

    # Every existing order was recorded through a Paknomik flow. No commission
    # line is created retroactively: tracking starts with new orders.
    Order.objects.filter(quote_proposal__isnull=False).update(origin="paknomik_quote")
    Order.objects.filter(quote_proposal__isnull=True, quote_response__isnull=False).update(
        origin="paknomik_rfq"
    )
    Order.objects.filter(quote_proposal__isnull=True, quote_response__isnull=True).update(
        origin="paknomik_product"
    )

    for code, extra in ROLE_PERMISSIONS.items():
        role = PlatformRole.objects.filter(code=code).first()
        if role is None:
            continue
        perms = list(role.permissions or [])
        changed = False
        for perm in extra:
            if perm not in perms:
                perms.append(perm)
                changed = True
        if changed:
            role.permissions = perms
            role.save(update_fields=["permissions"])

    PlatformSetting.objects.filter(
        key__in=["order_commission_enabled", "order_commission_percent"]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0087_order_origin_export_commission"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
