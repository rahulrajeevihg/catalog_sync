"""Remove legacy invoice/payment field definitions before core DocTypes synchronize."""


def execute():
    from catalog_sync.commerce.tracking import migrate_legacy_fields

    migrate_legacy_fields()
