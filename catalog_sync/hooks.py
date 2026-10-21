app_name = "catalog_sync"
app_title = "Catalogue Sync"
app_publisher = "Catalogue Engineering"
app_description = "Read-only catalogue exports and permission-scoped website account approvals"
app_email = "catalogue-engineering@localhost"
app_license = "Proprietary"
required_apps = ["erpnext"]

# App migration installs metadata only; account writes use standard Frappe REST permissions.
after_install = "catalog_sync.accounts.setup.install"
after_migrate = "catalog_sync.accounts.setup.install"
extend_bootinfo = "catalog_sync.accounts.setup.extend_bootinfo"
scheduler_events = {"cron": {"* * * * *": ["catalog_sync.sales.api.tick"]}}

doc_events = {
    "Sales Invoice": {
        event: [
            "catalog_sync.sales.api.invoice_changed",
            *(["catalog_sync.commerce.tracking.document_changed"] if event != "on_update" else []),
        ]
        for event in ("on_submit", "on_cancel", "on_update_after_submit", "on_update", "on_trash")
    },
    "Payment Entry": {
        event: "catalog_sync.commerce.tracking.document_changed"
        for event in ("on_submit", "on_cancel", "on_update_after_submit", "on_trash")
    },
    "Item": {"on_update": "catalog_sync.sales.api.item_changed"},
    "Sales Partner": {"validate": "catalog_sync.commerce.api.validate_partner"},
    "Catalogue Account": {"validate": "catalog_sync.accounts.validation.validate"},
    "Employee": {"validate": "catalog_sync.portal.pricing.validate_employee"},
}

# Retained accounting history must not prevent ERP's normal cancellation/deletion checks.
ignore_links_on_delete = ["Catalogue Document Tracking"]
