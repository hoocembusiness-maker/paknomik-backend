from django.db import migrations


def rebuild_consentrecord(apps, schema_editor):
    # SQLite kept "REFERENCES user_pakdinarledger" after the table rename, which breaks
    # every write touching ConsentRecord ledger links. Rebuilding the table fixes the DDL.
    if schema_editor.connection.vendor != 'sqlite':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'user_consentrecord'"
        )
        row = cursor.fetchone()
    if not row or 'user_pakdinarledger' not in (row[0] or ''):
        return
    schema_editor._remake_table(apps.get_model('user', 'ConsentRecord'))


class Migration(migrations.Migration):

    dependencies = [
        ('user', '0101_contact_message'),
    ]

    operations = [
        migrations.RunPython(rebuild_consentrecord, migrations.RunPython.noop),
    ]
