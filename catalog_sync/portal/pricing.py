"""Restrict ERP employee website-price grants without granting general HR access."""

import frappe

FIELD = "custom_catalogue_trade_price_enabled"


def enabled(employee_id):
    if not frappe.get_meta("Employee").has_field(FIELD):
        return False
    return bool(frappe.db.get_value("Employee", employee_id, FIELD))


def validate_employee(doc, method=None):
    previous = doc.get_doc_before_save()
    current = int(doc.get(FIELD) or 0)
    old = int(previous.get(FIELD) or 0) if previous else 0
    if current != old and not set(frappe.get_roles()).intersection(
        {"System Manager", "Catalogue Trader Approver"}
    ):
        frappe.throw(
            "Only an administrator or trader approver can change website Trade Price access",
            frappe.PermissionError,
        )
    # A transferred identity needs a fresh grant on its own subsequent save.
    if previous and doc.get("user_id") != previous.get("user_id"):
        doc.set(FIELD, 0)
