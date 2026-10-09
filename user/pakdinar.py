"""Compatibility imports for the historical Pakdinar migrations.

The project now uses the Pragmadinar names. Keep this module until all existing
migration histories that import it have been applied.
"""

from django.db import connection

from user.pragmadinar import (
    grant_supplier_welcome_pragmadinar,
    normalize_legacy_welcome_grant as _normalize_legacy_welcome_grant,
    seed_pragmadinar_catalog,
)


def _renamed_tables_exist():
    return "user_pragmadinarservice" in connection.introspection.table_names()


def seed_pakdinar_catalog():
    if _renamed_tables_exist():
        return seed_pragmadinar_catalog()


def grant_supplier_welcome_pakdinar(supplier, *, actor=None):
    if _renamed_tables_exist():
        return grant_supplier_welcome_pragmadinar(supplier, actor=actor)


def normalize_legacy_welcome_grant(supplier):
    if _renamed_tables_exist():
        return _normalize_legacy_welcome_grant(supplier)
