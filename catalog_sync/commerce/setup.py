"""Additive commerce metadata, installed by migration; never posts business documents."""

import frappe


def install():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    from frappe.permissions import add_permission, update_permission_property

    from catalog_sync.commerce.tracking import migrate_legacy_fields

    # Also recover sites where 0.6.0 saved Custom Field metadata before its DDL failed.
    migrate_legacy_fields()

    def data(name, label, **extra):
        return {"fieldname": name, "label": label, "fieldtype": "Data", "no_copy": 1, **extra}

    create_custom_fields(
        {
            "Sales Partner": [
                data("custom_catalogue_email", "Seller Login Email", unique=1, permlevel=4),
                data(
                    "custom_catalogue_password",
                    "Seller Password",
                    fieldtype="Password",
                    permlevel=4,
                ),
                data(
                    "custom_catalogue_enabled",
                    "Enable Seller Login",
                    fieldtype="Check",
                    permlevel=4,
                    default="0",
                ),
                data("custom_catalogue_mobile", "Seller Contact Mobile", permlevel=4),
                data(
                    "custom_catalogue_image", "Seller Photo", fieldtype="Attach Image", permlevel=4
                ),
            ],
            "Catalogue Account": [
                data(
                    "submitted_referral_sales_partner",
                    "Original Referring Sales Partner",
                    fieldtype="Link",
                    options="Sales Partner",
                    read_only=1,
                ),
                data(
                    "custom_referral_sales_partner",
                    "Referring Sales Partner",
                    fieldtype="Link",
                    options="Sales Partner",
                    permlevel=1,
                ),
            ],
            "Sales Order": [
                data("custom_catalogue_fulfilment", "Website Fulfilment", read_only=1),
                data("custom_catalogue_pickup", "Website Pickup Location", read_only=1),
            ],
            "Address": [
                data(
                    "custom_catalogue_latitude", "Delivery Latitude", fieldtype="Float", read_only=1
                ),
                data(
                    "custom_catalogue_longitude",
                    "Delivery Longitude",
                    fieldtype="Float",
                    read_only=1,
                ),
            ],
        },
        update=True,
    )
    add_permission("Sales Partner", "System Manager", 4)
    for permission in ("read", "write"):
        update_permission_property("Sales Partner", "System Manager", 4, permission, 1)
    fields = [
        (
            "Customer Product Enquiry Type",
            False,
            [
                data("label", "Enquiry Type", reqd=1),
                data("active", "Active", fieldtype="Check", default="1"),
                data("sort_order", "Sort Order", fieldtype="Int"),
            ],
        ),
        (
            "Customer Product Enquiry Item",
            True,
            [
                data("item_code", "Item", fieldtype="Link", options="Item", reqd=1),
                data("item_name", "Item Name"),
                data("qty", "Quantity", fieldtype="Float", reqd=1),
                data("price_snapshot", "Website Price Snapshot", fieldtype="Code", options="JSON"),
            ],
        ),
        (
            "Customer Product Enquiry",
            False,
            [
                data("website_enquiry_id", "Website Enquiry ID", unique=1, reqd=1, read_only=1),
                data(
                    "catalogue_account",
                    "Catalogue Account",
                    fieldtype="Link",
                    options="Catalogue Account",
                    reqd=1,
                ),
                data("customer", "Customer", fieldtype="Link", options="Customer"),
                data(
                    "enquiry_type",
                    "Enquiry Type",
                    fieldtype="Link",
                    options="Customer Product Enquiry Type",
                    reqd=1,
                ),
                data("type_name", "Enquiry Type at Submission", read_only=1),
                data("customer_name", "Customer Name"),
                data("email", "Email"),
                data("phone", "Phone"),
                data("sales_person", "Sales Person", fieldtype="Link", options="Sales Person"),
                data("sales_partner", "Sales Partner", fieldtype="Link", options="Sales Partner"),
                data("message", "Message", fieldtype="Small Text"),
                data(
                    "status",
                    "Status",
                    fieldtype="Select",
                    options="New\nIn progress\nResponded\nClosed",
                    default="New",
                ),
                data(
                    "items",
                    "Products",
                    fieldtype="Table",
                    options="Customer Product Enquiry Item",
                    reqd=1,
                ),
            ],
        ),
    ]
    for name, child, rows in fields:
        if not frappe.db.exists("DocType", name):
            frappe.get_doc(
                {
                    "doctype": "DocType",
                    "name": name,
                    "module": "Custom",
                    "custom": 1,
                    "istable": int(child),
                    "autoname": "hash",
                    "track_changes": 1,
                    "fields": rows,
                    "permissions": []
                    if child
                    else [
                        {
                            "role": "System Manager",
                            "read": 1,
                            "write": 1,
                            "create": 1,
                            "report": 1,
                            "print": 1,
                        },
                        {"role": "Sales Manager", "read": 1, "write": 1, "report": 1, "print": 1},
                    ],
                }
            ).insert(ignore_permissions=True)
    for index, (key, label) in enumerate(
        [
            ("quotation", "Request quotation"),
            ("bulk-order", "Request bulk order"),
            ("negotiate-price", "Negotiate price"),
            ("product-details", "Ask for product details"),
        ]
    ):
        if not frappe.db.exists("Customer Product Enquiry Type", key):
            frappe.get_doc(
                {
                    "doctype": "Customer Product Enquiry Type",
                    "name": key,
                    "label": label,
                    "active": 1,
                    "sort_order": index,
                }
            ).insert(ignore_permissions=True, set_name=key)
    frappe.clear_cache()
