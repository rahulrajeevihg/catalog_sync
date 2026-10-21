"""Transactional invoice/payment references and lifecycle history, outside core tables.

No hook commits independently: tracking is rolled back with the source document.
Checkout keys remain occupied after cancellation to prevent a retry from posting twice.
"""

import hashlib
import json
from uuid import UUID

import frappe

TRACKING = "Catalogue Document Tracking"
EVENT = "Catalogue Document Tracking Event"
SOURCES = ("Sales Invoice", "Payment Entry")
LEGACY_FIELD = "custom_catalogue_order_id"


def digest(*parts):
    payload = json.dumps(parts, default=str, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def checkout_key(doctype, order_id):
    if doctype not in SOURCES:
        frappe.throw("Unsupported accounting document")
    return digest("checkout", doctype, str(UUID(str(order_id))))


def install():
    def field(name, kind="Data", **extra):
        return {
            "fieldname": name,
            "label": name.replace("_", " ").title(),
            "fieldtype": kind,
            "read_only": 1,
            **extra,
        }

    event_fields = [
        field("event_key", length=64, unique=1, hidden=1),
        field("event"),
        field("occurred_at", "Datetime"),
        field("actor", "Link", options="User"),
        field("source_docstatus", "Int"),
        field("currency", "Link", options="Currency"),
        field("grand_total", "Currency", options="currency", precision="9"),
        field("outstanding_amount", "Currency", options="currency", precision="9"),
        field("returned_total", "Currency", options="currency", precision="9"),
    ]
    parent_fields = [
        field("source_doctype", "Link", options="DocType", reqd=1),
        field("source_name", "Dynamic Link", options="source_doctype", reqd=1),
        field("website_order_id", length=36, search_index=1),
        field("checkout_key", length=64, unique=1, hidden=1),
        field("sales_order", "Link", options="Sales Order"),
        field("company", "Link", options="Company"),
        field("customer", "Link", options="Customer"),
        field("currency", "Link", options="Currency"),
        field("posting_date", "Date"),
        field("source_docstatus", "Int"),
        field("source_status", "Select", options="Draft\nSubmitted\nCancelled\nDeleted"),
        field("source_deleted", "Check"),
        field("is_return", "Check"),
        field("return_against", "Link", options="Sales Invoice"),
        field("amended_from"),
        field("grand_total", "Currency", options="currency", precision="9"),
        field("outstanding_amount", "Currency", options="currency", precision="9"),
        field("returned_total", "Currency", options="currency", precision="9"),
        field("source_modified", "Datetime"),
        field("invoice_references", "Code", options="JSON"),
        field("events", "Table", options=EVENT),
    ]
    for name, child, fields in ((EVENT, True, event_fields), (TRACKING, False, parent_fields)):
        if frappe.db.exists("DocType", name):
            continue
        frappe.get_doc(
            {
                "doctype": "DocType",
                "name": name,
                "module": "Custom",
                "custom": 1,
                "istable": int(child),
                "autoname": "hash" if child else "prompt",
                "title_field": None if child else "source_name",
                "fields": fields,
                "permissions": []
                if child
                else [
                    {"role": role, "read": 1, "report": 1, "print": 1}
                    for role in ("System Manager", "Accounts Manager")
                ],
            }
        ).insert(ignore_permissions=True)


def _order_link(doc):
    """Resolve a single website order through existing invoice-item Sales Order links."""
    if doc.doctype != "Sales Invoice":
        return None, None
    names = sorted({r.sales_order for r in doc.get("items", []) if r.get("sales_order")})
    if not names:
        return None, None
    rows = frappe.get_all(
        "Sales Order",
        filters={"name": ["in", names]},
        fields=["name", LEGACY_FIELD],
        limit_page_length=len(names),
    )
    links = [(r.get(LEGACY_FIELD), r.name) for r in rows if r.get(LEGACY_FIELD)]
    # Consolidated invoices are still tracked, but never assigned to an arbitrary customer order.
    return links[0] if len(links) == 1 else (None, None)


def find_checkout_document(doctype, order_id):
    """The unique key is kept for drafts and cancelled documents as well as paid ones."""
    return frappe.db.get_value(
        TRACKING, {"checkout_key": checkout_key(doctype, order_id)}, "source_name", for_update=True
    )


def record(doc, method=None, *, order_id=None, sales_order=None, checkout=False):
    if doc.doctype not in SOURCES:
        frappe.throw("Unsupported accounting document")
    if not frappe.db.exists("DocType", TRACKING):
        if checkout:
            frappe.throw("Deploy and migrate Catalogue Document Tracking before checkout")
        return
    name = digest(doc.doctype, doc.name)
    # Insert a shell without a checkout key. A duplicate source is harmless; a checkout-key
    # collision on another source must fail instead of overwriting the original mapping.
    if not frappe.db.exists(TRACKING, name):
        frappe.get_doc(
            {
                "doctype": TRACKING,
                "source_doctype": doc.doctype,
                "source_name": doc.name,
            }
        ).insert(
            ignore_permissions=True, ignore_links=True, set_name=name, ignore_if_duplicate=True
        )
    # A current read (including event children) is essential under MariaDB REPEATABLE READ.
    tracked = frappe.get_doc(TRACKING, name, for_update=True)
    if order_id and tracked.get("website_order_id") not in (None, "", order_id):
        frappe.throw("Accounting document website order conflict")
    linked_id, linked_order = _order_link(doc)
    if order_id and linked_id and str(UUID(str(order_id))) != str(UUID(str(linked_id))):
        frappe.throw("Invoice Sales Order does not match its website reference")
    order_id = order_id or tracked.get("website_order_id") or linked_id
    sales_order = sales_order or tracked.get("sales_order") or linked_order
    original = None
    if doc.doctype == "Sales Invoice" and doc.get("is_return") and doc.get("return_against"):
        original = frappe.get_doc("Sales Invoice", doc.return_against, for_update=True)
        old = frappe.db.get_value(
            TRACKING, digest("Sales Invoice", original.name), "website_order_id"
        )
        original_id, original_order = _order_link(original)
        order_id = order_id or old or original_id
        sales_order = sales_order or original_order
    if order_id:
        order_id = str(UUID(str(order_id)))
    if checkout and (not order_id or doc.get("is_return")):
        frappe.throw("Only original website documents can claim a checkout reference")
    refs = [
        {"invoice": r.reference_name, "allocated_amount": str(r.allocated_amount)}
        for r in doc.get("references", [])
        if r.reference_doctype == "Sales Invoice"
    ]
    returned = 0
    if doc.doctype == "Sales Invoice":
        returned = frappe.db.sql(
            "SELECT COALESCE(SUM(grand_total),0) FROM `tabSales Invoice` "
            "WHERE return_against=%s AND is_return=1 AND docstatus=1 FOR UPDATE",
            doc.name,
        )[0][0]
    values = {
        "website_order_id": order_id,
        "sales_order": sales_order,
        "company": doc.get("company"),
        "customer": doc.get("customer")
        or (doc.get("party") if doc.get("party_type") == "Customer" else None),
        "currency": doc.get("currency") or doc.get("paid_from_account_currency"),
        "posting_date": doc.get("posting_date"),
        "source_docstatus": int(doc.docstatus),
        "source_status": "Deleted"
        if method == "on_trash"
        else {0: "Draft", 1: "Submitted", 2: "Cancelled"}[int(doc.docstatus)],
        "source_deleted": int(method == "on_trash"),
        "is_return": int(bool(doc.get("is_return"))),
        "return_against": doc.get("return_against"),
        "amended_from": doc.get("amended_from"),
        "grand_total": doc.get("grand_total")
        if doc.doctype == "Sales Invoice"
        else doc.get("paid_amount"),
        "outstanding_amount": doc.get("outstanding_amount") or 0,
        "returned_total": returned,
        "source_modified": doc.get("modified"),
        "invoice_references": json.dumps(refs, sort_keys=True),
    }
    if checkout:
        values["checkout_key"] = checkout_key(doc.doctype, order_id)
    action = {
        "on_submit": "Return submitted" if doc.get("is_return") else "Submitted",
        "on_cancel": "Return cancelled" if doc.get("is_return") else "Cancelled",
        "on_trash": "Deleted",
        "legacy_import": "Imported",
        "return_changed": "Return balance updated",
    }.get(method, "Draft linked" if doc.docstatus == 0 else "Updated")
    # A repeated source callback with the same snapshot cannot duplicate its event.
    event_key = digest(name, action, {k: v for k, v in values.items() if k != "checkout_key"})
    tracked.update(values)
    if method != "refresh" and not any(r.event_key == event_key for r in tracked.get("events", [])):
        tracked.append(
            "events",
            {
                "event_key": event_key,
                "event": action,
                "occurred_at": frappe.utils.now_datetime(),
                "actor": frappe.session.user,
                **{
                    k: values[k]
                    for k in (
                        "source_docstatus",
                        "currency",
                        "grand_total",
                        "outstanding_amount",
                        "returned_total",
                    )
                },
            },
        )
    # Historical references must survive cancellation/deletion of their source document.
    tracked.flags.ignore_links = True
    tracked.save(ignore_permissions=True)
    if original and not original.get("is_return"):
        record(original, "return_changed")
    return tracked


def document_changed(doc, method=None):
    record(doc, method)
    if doc.doctype == "Payment Entry":
        for invoice in sorted(
            {
                r.reference_name
                for r in doc.get("references", [])
                if r.reference_doctype == "Sales Invoice"
            }
        ):
            record(
                frappe.get_doc("Sales Invoice", invoice, for_update=True), "on_update_after_submit"
            )


def migrate_legacy_fields():
    """Recover failed installs and backfill successful old installs before removing metadata.

    Frappe retains any physical legacy columns; their values are never dropped or truncated.
    Removing Custom Field metadata before model sync prevents its failed ADD COLUMN retry.
    """
    install()
    for doctype in SOURCES:
        field = frappe.db.get_value(
            "Custom Field",
            {"dt": doctype, "fieldname": LEGACY_FIELD},
            ["name", "label", "fieldtype", "read_only", "no_copy"],
            as_dict=True,
        )
        if not field:
            continue
        if not (
            field.label == "Website Order ID"
            and field.fieldtype == "Data"
            and field.read_only
            and field.no_copy
        ):
            frappe.throw("Review conflicting Custom Field before migrating: " + field.name)
        if frappe.db.has_column(doctype, LEGACY_FIELD):
            cursor = ""
            while True:
                rows = frappe.get_all(
                    doctype,
                    filters={"name": [">", cursor], LEGACY_FIELD: ["is", "set"]},
                    fields=["name", LEGACY_FIELD],
                    order_by="name asc",
                    limit_page_length=200,
                )
                for row in rows:
                    document = frappe.get_doc(doctype, row.name)
                    record(
                        document,
                        "legacy_import",
                        order_id=row.get(LEGACY_FIELD),
                        checkout=not document.get("is_return"),
                    )
                if len(rows) < 200:
                    break
                cursor = rows[-1].name
        frappe.delete_doc("Custom Field", field.name, ignore_permissions=True)
        frappe.clear_cache(doctype=doctype)
