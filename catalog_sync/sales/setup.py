"""Install additive, private derived-data metadata without granting invoice access."""

import frappe

ROLE = "Catalogue Sales Insights Integration"
METRIC_FIELDS = (
    "lifetime_quantity",
    "lifetime_invoices",
    "lifetime_customers",
    "recent_quantity",
    "recent_invoices",
    "recent_customers",
)


def install():
    if not frappe.db.exists("Role", ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 0}).insert(
            ignore_permissions=True
        )

    def field(name, kind="Data", **extra):
        return {
            "fieldname": name,
            "label": name.replace("_", " ").title(),
            "fieldtype": kind,
            **extra,
        }

    metrics = [field("item_code", reqd=1, search_index=1), field("stock_uom")]
    metrics += [
        (
            field(name, "Float", default="0", precision="9")
            if name.endswith("quantity")
            else field(name, "Int", default="0")
        )
        for name in METRIC_FIELDS
    ]
    definitions = [
        ("Catalogue Sales Metric", False, metrics),
        (
            "Catalogue Sales Update",
            False,
            metrics + [field("generation", "Int", reqd=1, search_index=1)],
        ),
        ("Catalogue Sales Dirty", False, [field("item_code", reqd=1), field("version", reqd=1)]),
        (
            "Catalogue Sales Generation",
            False,
            [
                field("completed_at", reqd=1),
                field("reconciliation_token"),
                field("period_start", "Date"),
                field("period_end", "Date"),
            ],
        ),
        (
            "Catalogue Sales State",
            True,
            [
                field("completed_generation", "Int", default="0"),
                field("building_generation", "Int", default="0"),
                field("build_date", "Date"),
                field("reconciled_on", "Date"),
                field("reconcile_requested"),
                field("build_request"),
                field("reconciled_request"),
                field("force_publication", "Check", default="0"),
            ],
        ),
    ]
    for name, single, fields in definitions:
        if not frappe.db.exists("DocType", name):
            frappe.get_doc(
                {
                    "doctype": "DocType",
                    "name": name,
                    "module": "Custom",
                    "custom": 1,
                    "issingle": int(single),
                    "autoname": "hash",
                    "fields": fields,
                    "permissions": [{"role": "System Manager", "read": 1, "report": 1}],
                }
            ).insert(ignore_permissions=True)
    frappe.db.add_index("Catalogue Sales Update", ["generation", "name"], "sales_generation_cursor")
