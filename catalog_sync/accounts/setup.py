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
    from frappe.permissions import add_permission, update_permission_property

    for role in ("Catalogue Account Integration", "Catalogue Trader Approver"):
        add_permission("File", role, 0)
        for field in ("read", "write", "create", "delete", "share", "export", "print", "email"):
            value = int(field == "read" or (field == "create" and role.endswith("Integration")))
            update_permission_property("File", role, 0, field, value)
    frappe.clear_cache(doctype="Catalogue Account")
    frappe.clear_cache(doctype="File")
