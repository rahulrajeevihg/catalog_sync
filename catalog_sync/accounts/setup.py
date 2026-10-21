"""Idempotent installation during Frappe app install and migration, not website requests."""

import json
from pathlib import Path

import frappe


def install(**kwargs):
    if frappe.session.user != "Administrator":
        raise PermissionError("Account metadata installation requires Administrator")
    bundle = json.loads(Path(__file__).with_name("setup.json").read_text())
    if bundle.get("version") != 1:
        raise ValueError("Unsupported account metadata version")
    identities = {
        "Role": "role_name",
        "Workflow State": "workflow_state_name",
        "Workflow Action Master": "workflow_action_name",
        "Workflow": "workflow_name",
    }
    for values in bundle["objects"]:
        doctype = values["doctype"]
        name = values.get("name") or values[identities[doctype]]
        if frappe.db.exists(doctype, name):
            if doctype not in ("DocType", "Workflow", "Workspace"):
                continue
            document = frappe.get_doc(doctype, name)
            if doctype == "DocType":
                if not document.custom:
                    raise ValueError("Refusing to replace a standard ERP DocType")
                # Keep additive site fields and unrelated roles when updating app-owned metadata.
                fieldnames = {field["fieldname"] for field in values["fields"]}
                values = dict(values)
                values["fields"] = values["fields"] + [
                    field.as_dict()
                    for field in document.fields
                    if field.fieldname not in fieldnames
                ]
                roles = {row["role"] for row in values["permissions"]}
                values["permissions"] = values["permissions"] + [
                    row.as_dict() for row in document.permissions if row.role not in roles
                ]
            document.update(values)
            document.save()
        else:
            frappe.get_doc(values).insert()
    # Initialize only new commercial defaults; preserve profiles, proof and approval.
    frappe.db.sql(
        "UPDATE `tabCatalogue Account` SET primary_price_list='MRP' "
        "WHERE IFNULL(primary_price_list, '')=''"
    )
    frappe.db.sql(
        "UPDATE `tabCatalogue Account` SET commercial_revision=1 "
        "WHERE IFNULL(commercial_revision, 0)=0"
    )
    frappe.db.sql(
        "UPDATE `tabCatalogue Account` SET sales_assignment_required=1 "
        "WHERE trader_requested=1 AND IFNULL(assigned_sales_person, '')=''"
    )
    frappe.db.sql(
        "UPDATE `tabCatalogue Account` SET referral_locked=1 "
        "WHERE trader_requested=1 OR application_revision>0"
    )
    script_values = {
        "doctype": "Client Script",
        "name": "Catalogue Account Password Controls",
        "dt": "Catalogue Account",
        "view": "Form",
        "enabled": 1,
        "script": Path(__file__).with_name("password_controls.js").read_text(),
    }
    if frappe.db.exists("Client Script", script_values["name"]):
        script = frappe.get_doc("Client Script", script_values["name"])
        if script.dt != "Catalogue Account":
            raise ValueError("Password controls script belongs to a different DocType")
        script.update(script_values)
        script.save()
    else:
        frappe.get_doc(script_values).insert()
    from frappe.permissions import add_permission, update_permission_property

    for role in ("Catalogue Account Integration", "Catalogue Trader Approver"):
        add_permission("File", role, 0)
        for field in ("read", "write", "create", "delete", "share", "export", "print", "email"):
            value = int(field == "read" or (field == "create" and role.endswith("Integration")))
            update_permission_property("File", role, 0, field, value)
    from catalog_sync.portal.setup import install as install_portal

    install_portal()
    from catalog_sync.sales.setup import install as install_sales

    install_sales()
    frappe.clear_cache(doctype="Catalogue Account")
    frappe.clear_cache(doctype="File")


def extend_bootinfo(bootinfo):
    """Expose only the store origin; password editing stays in the owner website UI."""
    from urllib.parse import urlsplit

    configured = frappe.conf.get("catalogue_account_store_url") or "https://store.ledworld.com"
    parsed = urlsplit(configured)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("catalogue_account_store_url must be an HTTPS origin")
    bootinfo.catalogue_account_store_url = configured.rstrip("/")
