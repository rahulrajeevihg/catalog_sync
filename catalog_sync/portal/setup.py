"""Additive portal fields and real website classification masters."""

import frappe


def install():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    from catalog_sync.portal.api import BRANCH, COMPANY, GROUP, SUBGROUP

    for role in ("Catalogue Order Integration", "Catalogue Portal Setup"):
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role}).insert()
    fields = {
        "Employee": [
            {
                "fieldname": "custom_catalogue_trade_price_enabled",
                "label": "Allow Website Trade Price",
                "fieldtype": "Check",
                "permlevel": 4,
                "default": "0",
                "no_copy": 1,
                "description": (
                    "Website trade pricing; defaults to MRP. "
                    "Website admin overrides take precedence until reset."
                ),
            }
        ],
        "Customer": [
            {
                "fieldname": "custom_catalogue_account_id",
                "label": "Website Account ID",
                "fieldtype": "Data",
                "unique": 1,
                "read_only": 1,
                "no_copy": 1,
            }
        ],
        "Contact": [
            {
                "fieldname": "custom_catalogue_account_id",
                "label": "Website Account ID",
                "fieldtype": "Data",
                "unique": 1,
                "read_only": 1,
                "no_copy": 1,
            }
        ],
        "Address": [
            {
                "fieldname": "custom_catalogue_address_key",
                "label": "Website Address Key",
                "fieldtype": "Data",
                "unique": 1,
                "read_only": 1,
                "no_copy": 1,
            }
        ],
        "Sales Order": [
            {
                "fieldname": "custom_catalogue_notes",
                "label": "Website Order Notes",
                "fieldtype": "Small Text",
                "read_only": 1,
                "no_copy": 1,
            },
            {
                "fieldname": "custom_catalogue_order_id",
                "label": "Website Order ID",
                "fieldtype": "Data",
                "unique": 1,
                "read_only": 1,
                "no_copy": 1,
            },
            {
                "fieldname": "custom_catalogue_account",
                "label": "Catalogue Account",
                "fieldtype": "Link",
                "options": "Catalogue Account",
                "read_only": 1,
                "no_copy": 1,
            },
            {
                "fieldname": "custom_catalogue_referrer",
                "label": "Referring Employee",
                "fieldtype": "Link",
                "options": "Employee",
                "read_only": 1,
                "no_copy": 1,
            },
        ],
        "Sales Order Item": [
            {
                "fieldname": "custom_catalogue_primary_list",
                "label": "Website Primary Price List",
                "fieldtype": "Data",
                "read_only": 1,
            }
        ],
    }
    create_custom_fields(fields, update=True)
    from frappe.permissions import add_permission, update_permission_property

    add_permission("Employee", "System Manager", 4)
    for permission in ("read", "write"):
        update_permission_property("Employee", "System Manager", 4, permission, 1)
    if frappe.db.exists("Company", COMPANY):
        exists = frappe.db.exists("Branch", BRANCH)
        branch = (
            frappe.get_doc("Branch", BRANCH)
            if exists
            else frappe.get_doc({"doctype": "Branch", "branch": BRANCH})
        )
        defaults = {
            "company": COMPANY,
            "branch_abbr": "ONLINE",
            "cost_center": frappe.db.get_value("Company", COMPANY, "cost_center"),
        }
        changed = False
        for key, value in defaults.items():
            if branch.meta.has_field(key) and not branch.get(key) and value:
                branch.set(key, value)
                changed = True
        if not exists:
            branch.insert()
        elif changed:
            branch.save()
        if not frappe.db.exists("Customer Group", GROUP):
            frappe.get_doc(
                {
                    "doctype": "Customer Group",
                    "customer_group_name": GROUP,
                    "parent_customer_group": "All Customer Groups",
                    "is_group": 0,
                }
            ).insert()
        if frappe.db.exists("DocType", "Customer Sub Groups") and not frappe.db.exists(
            "Customer Sub Groups", SUBGROUP
        ):
            meta = frappe.get_meta("Customer Sub Groups")
            values = {"doctype": "Customer Sub Groups", "customer_group": GROUP}
            # The site's custom subgroup naming field is discovered, not guessed.
            naming = meta.autoname[6:] if str(meta.autoname).startswith("field:") else ""
            if naming:
                values[naming] = SUBGROUP
            else:
                values["name"] = SUBGROUP
            frappe.get_doc(values).insert()

    from catalog_sync.commerce.setup import install as install_commerce

    install_commerce()
