"""Permission-scoped seller, enquiry, paid-order and invoice bridge."""

import base64
import hashlib
import hmac
import json
from decimal import Decimal

import frappe
from frappe.utils import nowdate

from catalog_sync.portal.api import COMPANY, ROLE, require, valid_uuid


def parsed(payload):
    value = frappe.parse_json(payload) if isinstance(payload, str) else payload
    if not isinstance(value, dict):
        frappe.throw("Invalid commerce request")
    return value


def validate_partner(doc, method=None):
    previous = doc.get_doc_before_save()
    controlled = ("custom_catalogue_email", "custom_catalogue_password", "custom_catalogue_enabled")
    if (
        previous
        and any(doc.get(k) != previous.get(k) for k in controlled)
        and "System Manager" not in frappe.get_roles()
    ):
        frappe.throw("Only System Managers can change seller access", frappe.PermissionError)
    if doc.get("custom_catalogue_email"):
        from frappe.utils import validate_email_address

        doc.custom_catalogue_email = validate_email_address(
            doc.custom_catalogue_email.strip().lower(), throw=True
        )
    password = str(doc.get("custom_catalogue_password") or "")
    if password and set(password) != {"*"} and len(password) < 8:
        frappe.throw("Use at least 8 characters for a seller password")
    if doc.get("custom_catalogue_enabled") and not password:
        frappe.throw("Set a seller password before enabling login")
    if doc.get("custom_catalogue_enabled") and not doc.get("custom_catalogue_email"):
        frappe.throw("Seller login requires an email")


def partner_view(doc):
    return {
        "partner_id": doc.name,
        "name": doc.partner_name or doc.name,
        "email": doc.custom_catalogue_email or "",
        "phone": doc.get("custom_catalogue_mobile") or "",
        "image": doc.get("custom_catalogue_image") or "",
        "active": bool(doc.get("custom_catalogue_enabled")),
        "credential_revision": hashlib.sha256(str(doc.modified).encode()).hexdigest(),
    }


@frappe.whitelist(methods=["POST"])
def partner_login(payload):
    require()
    from frappe.utils.password import get_decrypted_password

    body = parsed(payload)
    email, password = str(body.get("email") or "").strip().lower(), str(body.get("password") or "")
    if not email or not 1 <= len(password) <= 256:
        return {"partner": None}
    name = frappe.db.get_value(
        "Sales Partner", {"custom_catalogue_email": email, "custom_catalogue_enabled": 1}, "name"
    )
    if not name:
        hmac.compare_digest(hashlib.sha256(password.encode()).digest(), bytes(32))
        return {"partner": None}
    stored = (
        get_decrypted_password(
            "Sales Partner", name, "custom_catalogue_password", raise_exception=False
        )
        or ""
    )
    if not stored or not hmac.compare_digest(password.encode(), stored.encode()):
        return {"partner": None}
    return {"partner": partner_view(frappe.get_doc("Sales Partner", name))}


@frappe.whitelist(methods=["POST"])
def partner_directory(payload):
    require()
    body = parsed(payload)
    cursor = str(body.get("cursor") or "")
    rows = frappe.get_all(
        "Sales Partner",
        filters={"name": [">", cursor]},
        fields=["name"],
        order_by="name asc",
        limit_page_length=500,
    )
    return {
        "rows": [
            partner_view(frappe.get_doc("Sales Partner", r.name))
            for r in rows
            if frappe.db.get_value("Sales Partner", r.name, "custom_catalogue_email")
        ],
        "next_cursor": rows[-1].name if rows else "",
        "has_more": len(rows) == 500,
    }


@frappe.whitelist(methods=["POST"])
def readiness(payload):
    require(ROLE)
    value = parsed(payload)
    missing = []
    for doctype in ("Customer Product Enquiry", "Customer Product Enquiry Type"):
        if not frappe.db.exists("DocType", doctype):
            missing.append(doctype)
    from catalog_sync.accounts.commercial import valid_sales_person

    people = [
        value.get("default_sales_person"),
        *(value.get("partner_sales_people") or {}).values(),
    ]
    if (
        value.get("assisted_orders_enabled")
        or value.get("pickup_enabled")
        or value.get("delivery_enabled")
    ) and not valid_sales_person(people[0]):
        missing.append("default Sales Person")
    for person in filter(None, people):
        if not valid_sales_person(person):
            missing.append("Sales Person: " + person)
    for partner in value.get("partner_sales_people") or {}:
        if not frappe.db.exists("Sales Partner", partner):
            missing.append("Sales Partner: " + partner)
    if value.get("partners_enabled"):
        for field in (
            "custom_catalogue_email",
            "custom_catalogue_password",
            "custom_catalogue_enabled",
        ):
            if not frappe.get_meta("Sales Partner").has_field(field):
                missing.append("Sales Partner." + field)
    if (
        value.get("pickup_enabled")
        or value.get("delivery_enabled")
        or value.get("assisted_orders_enabled")
    ):
        from catalog_sync.portal.api import capabilities

        if not capabilities().get("orders_ready"):
            missing.append("base ERP ordering configuration")
    if value.get("delivery_enabled"):
        account_currency = (
            frappe.db.get_value("Account", value.get("payment_account"), "account_currency")
            if value.get("payment_account")
            else None
        )
        if account_currency not in (None, "", "AED"):
            missing.append("AED payment account currency")
        item = value.get("delivery_item", "Delivery Charge")
        if not frappe.db.exists(
            "Item", {"name": item, "is_stock_item": 0, "is_sales_item": 1, "disabled": 0}
        ):
            missing.append("non-stock Delivery Charge item with income account")
        if not frappe.db.exists(
            "Mode of Payment", {"name": value.get("payment_mode"), "enabled": 1}
        ):
            missing.append("enabled payment mode")
        if not frappe.db.exists(
            "Account",
            {
                "name": value.get("payment_account"),
                "company": COMPANY,
                "is_group": 0,
                "disabled": 0,
                "account_type": ["in", ["Bank", "Cash"]],
            },
        ):
            missing.append("selling-company bank/clearing account")
        for dt in ("Sales Invoice", "Payment Entry"):
            if not frappe.get_meta(dt).has_field("custom_catalogue_order_id"):
                missing.append(dt + " website identifier")
    return {"ready": not missing, "missing": missing}


def apply_types(rows):
    for index, row in enumerate(rows):
        key = str(row["id"])
        if not frappe.db.exists("Customer Product Enquiry Type", key):
            frappe.get_doc(
                {
                    "doctype": "Customer Product Enquiry Type",
                    "name": key,
                    "label": row["name"],
                    "active": int(row.get("active", True)),
                    "sort_order": index,
                }
            ).insert(ignore_permissions=True, set_name=key)
        else:
            frappe.db.set_value(
                "Customer Product Enquiry Type",
                key,
                {"label": row["name"], "active": int(row.get("active", True)), "sort_order": index},
            )


@frappe.whitelist(methods=["POST"])
def save_enquiry(payload):
    require(ROLE)
    body = parsed(payload)
    key = valid_uuid(body.get("enquiry_id"))
    account_id = valid_uuid(body.get("account_id"))
    account_name = frappe.db.get_value(
        "Catalogue Account", {"website_account_id": account_id}, "name"
    )
    if not account_name:
        return {"state": "retrying", "code": "account_sync_pending"}
    frappe.db.sql("SELECT name FROM `tabCatalogue Account` WHERE name=%s FOR UPDATE", account_name)
    old = frappe.db.get_value("Customer Product Enquiry", {"website_enquiry_id": key}, "name")
    if old:
        doc = frappe.get_doc("Customer Product Enquiry", old)
        if doc.catalogue_account != account_name:
            frappe.throw("Enquiry ownership conflict")
        return {"state": "synced", "reference": old, "status": doc.status}
    apply_types(body.get("enquiry_types") or [])
    account = frappe.get_doc("Catalogue Account", account_name)
    items = body.get("items") or []
    if not 1 <= len(items) <= 50:
        frappe.throw("Invalid enquiry items")
    doc = frappe.get_doc(
        {
            "doctype": "Customer Product Enquiry",
            "website_enquiry_id": key,
            "catalogue_account": account_name,
            "customer": account.get("existing_customer"),
            "enquiry_type": body["enquiry_type"],
            "type_name": body["type_name"],
            "customer_name": account.display_name,
            "email": account.email,
            "phone": account.phone_e164,
            "sales_person": account.get("assigned_sales_person") or body.get("sales_person"),
            "sales_partner": account.get("custom_referral_sales_partner"),
            "message": str(body.get("message") or "")[:2000],
            "status": "New",
            "items": [
                {
                    "item_code": v["item_code"],
                    "item_name": v["item_name"],
                    "qty": float(Decimal(v["qty"])),
                    "price_snapshot": json.dumps(v.get("price")),
                }
                for v in items
            ],
        }
    )
    doc.insert(ignore_permissions=True)
    return {"state": "synced", "reference": doc.name, "status": doc.status}


@frappe.whitelist(methods=["POST"])
def enquiry_status(payload):
    require(ROLE)
    body = parsed(payload)
    name = frappe.db.get_value(
        "Customer Product Enquiry",
        {"website_enquiry_id": valid_uuid(body.get("enquiry_id"))},
        "name",
    )
    return (
        {
            "state": "synced",
            "reference": name,
            "status": frappe.db.get_value("Customer Product Enquiry", name, "status"),
        }
        if name
        else {"state": "needs_attention", "code": "enquiry_missing"}
    )


@frappe.whitelist(methods=["POST"])
def checkout_preview(payload):
    require(ROLE)
    body = parsed(payload)
    from catalog_sync.portal.api import place_order

    result = place_order({**body, "validate_only": True})
    # No Sales Order or invoice is persisted during a quote.
    frappe.db.rollback()
    return result


@frappe.whitelist(methods=["POST"])
def fulfil_order(payload):
    require(ROLE)
    body = parsed(payload)
    from catalog_sync.portal.api import place_order

    key = valid_uuid(body.get("order_id"))
    result = place_order(body)
    if result.get("state") != "synced" or body.get("fulfilment_mode") != "delivery":
        return result
    if not body.get("payment_reference") or body.get("payment_status") != "paid":
        frappe.throw("Verified payment is required")
    # Serialize the entire recovery sequence, including retries after partial commits.
    order = frappe.get_doc("Sales Order", result["order"]["name"])
    frappe.db.sql("SELECT name FROM `tabSales Order` WHERE name=%s FOR UPDATE", order.name)
    order.reload()
    expected = Decimal(str(body["total"]))
    if abs(Decimal(str(order.grand_total)) - expected) > Decimal("0.005"):
        return {"state": "needs_attention", "code": "paid_total_mismatch"}
    requested = {
        v["item_code"]: (Decimal(str(v["qty"])), Decimal(str(v["price"]["amount"])))
        for v in body.get("items", [])
    }
    if Decimal(str(body.get("delivery_charge") or 0)):
        requested[body.get("delivery_item") or "Delivery Charge"] = (
            Decimal(1),
            Decimal(str(body["delivery_charge"])),
        )
    actual = {}
    for row in order.items:
        expected_line = requested.get(row.item_code)
        if not expected_line or abs(Decimal(str(row.rate)) - expected_line[1]) > Decimal(
            "0.000001"
        ):
            return {"state": "needs_attention", "code": "paid_order_lines_changed"}
        actual[row.item_code] = actual.get(row.item_code, Decimal(0)) + Decimal(str(row.qty))
    if set(actual) != set(requested) or any(actual[k] != requested[k][0] for k in requested):
        return {"state": "needs_attention", "code": "paid_order_lines_changed"}
    if order.docstatus == 0:
        order.flags.ignore_permissions = True
        order.submit()
    if order.docstatus != 1:
        return {"state": "needs_attention", "code": "paid_order_cancelled"}
    invoice_name = frappe.db.get_value("Sales Invoice", {"custom_catalogue_order_id": key}, "name")
    if invoice_name:
        invoice = frappe.get_doc("Sales Invoice", invoice_name)
    else:
        from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

        invoice = make_sales_invoice(order.name, ignore_permissions=True)
        invoice.custom_catalogue_order_id = key
        invoice.update_stock = 0
        invoice.disable_rounded_total = 1
        invoice.flags.ignore_permissions = True
        invoice.insert()
    if invoice.docstatus == 2 or abs(Decimal(str(invoice.grand_total)) - expected) > Decimal(
        "0.005"
    ):
        return {"state": "needs_attention", "code": "invoice_total_mismatch"}
    if invoice.docstatus == 0:
        invoice.flags.ignore_permissions = True
        invoice.submit()
    payment_name = frappe.db.get_value("Payment Entry", {"custom_catalogue_order_id": key}, "name")
    if payment_name:
        payment = frappe.get_doc("Payment Entry", payment_name)
    else:
        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

        payment = get_payment_entry(
            "Sales Invoice",
            invoice.name,
            bank_account=body["payment_account"],
            bank_amount=float(expected),
            ignore_permissions=True,
        )
        payment.custom_catalogue_order_id = key
        payment.mode_of_payment = body["payment_mode"]
        payment.reference_no = body["payment_reference"]
        payment.reference_date = nowdate()
        payment.flags.ignore_permissions = True
        payment.insert()
    if (
        payment.company != COMPANY
        or payment.party != invoice.customer
        or payment.reference_no != body["payment_reference"]
        or abs(Decimal(str(payment.paid_amount)) - expected) > Decimal("0.005")
    ):
        return {"state": "needs_attention", "code": "payment_entry_mismatch"}
    if not any(
        row.reference_doctype == "Sales Invoice"
        and row.reference_name == invoice.name
        and abs(Decimal(str(row.allocated_amount)) - expected) <= Decimal("0.005")
        for row in payment.references
    ):
        return {"state": "needs_attention", "code": "payment_allocation_mismatch"}
    if payment.docstatus == 0:
        payment.flags.ignore_permissions = True
        payment.submit()
    if payment.docstatus != 1:
        return {"state": "needs_attention", "code": "payment_entry_cancelled"}
    return order_documents(order, invoice, payment)


def order_documents(order, invoice=None, payment=None):
    from catalog_sync.portal.api import status

    return {
        "state": "synced",
        "order": {
            **status(order),
            "invoice": {
                "name": invoice.name,
                "docstatus": invoice.docstatus,
                "grand_total": str(invoice.grand_total),
                "outstanding_amount": str(
                    frappe.db.get_value("Sales Invoice", invoice.name, "outstanding_amount")
                ),
            }
            if invoice
            else None,
            "payment_entry": payment.name if payment else None,
        },
    }


@frappe.whitelist(methods=["POST"])
def invoice_pdf(payload):
    require(ROLE)
    body = parsed(payload)
    key = valid_uuid(body.get("order_id"))
    name = frappe.db.get_value(
        "Sales Invoice", {"custom_catalogue_order_id": key, "docstatus": 1}, "name"
    )
    if not name:
        frappe.throw("Invoice unavailable")
    previous = frappe.flags.ignore_print_permissions
    try:
        # This role-scoped RPC resolves only the invoice linked to the website UUID.
        frappe.flags.ignore_print_permissions = True
        content = frappe.get_print("Sales Invoice", name, as_pdf=True)
        return {"pdf": base64.b64encode(content).decode()}
    finally:
        frappe.flags.ignore_print_permissions = previous


@frappe.whitelist(methods=["POST"])
def update_enquiry(payload):
    require(ROLE)
    body = parsed(payload)
    if body.get("status") not in ("New", "In progress", "Responded", "Closed"):
        frappe.throw("Invalid enquiry status")
    key = valid_uuid(body.get("enquiry_id"))
    name = frappe.db.get_value("Customer Product Enquiry", {"website_enquiry_id": key}, "name")
    if not name:
        frappe.throw("Enquiry has not synchronized yet")
    doc = frappe.get_doc("Customer Product Enquiry", name)
    doc.status = body["status"]
    doc.save(ignore_permissions=True)
    return {"status": doc.status}


@frappe.whitelist(methods=["POST"])
def contact_image(payload):
    require(ROLE)
    body = parsed(payload)
    name = frappe.db.get_value(
        "Catalogue Account", {"website_account_id": valid_uuid(body.get("account_id"))}, "name"
    )
    if not name:
        frappe.throw("Account unavailable")
    account = frappe.get_doc("Catalogue Account", name)
    partner = account.get("custom_referral_sales_partner")
    if partner:
        image = frappe.db.get_value("Sales Partner", partner, "custom_catalogue_image")
    else:
        person = account.get("assigned_sales_person") or body.get("sales_person")
        employee = frappe.db.get_value("Sales Person", person, "employee") if person else None
        image = frappe.db.get_value("Employee", employee, "image") if employee else None
    # Resolve only the referenced local ERP attachment, never a caller-provided URL.
    if not image or not str(image).startswith(("/files/", "/private/files/")):
        return {"image": None}
    from frappe.utils.file_manager import get_file

    _, content = get_file(image)
    if isinstance(content, str):
        content = content.encode()
    if len(content) > 5 * 1024 * 1024:
        return {"image": None}
    return {"image": base64.b64encode(content).decode()}
