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
doc_events = {
    "Catalogue Account": {"validate": "catalog_sync.accounts.validation.validate"},
}
