import hashlib
import json
from decimal import Decimal, InvalidOperation
from uuid import UUID

import frappe
from frappe.utils import getdate, nowdate

COMPANY = "LED WORLD LLC"
BRANCH = "LEDWORLD - ONLINE"
GROUP = "Website Customers"
SUBGROUP = "Website Registration"
ROLE = "Catalogue Order Integration"


def require(role="Catalogue Account Integration"):
    if frappe.session.user == "Guest" or role not in frappe.get_roles():
        frappe.throw("Catalogue integration access required", frappe.PermissionError)


@frappe.whitelist(methods=["GET"])
def directory(cursor="", limit=500):
    require()
    limit = int(limit)
    if not 1 <= limit <= 500 or len(cursor) > 255:
        frappe.throw("Invalid directory request")
    rows = frappe.get_all(
        "Employee",
        filters={"name": [">", cursor]},
        fields=["name", "employee_name", "company", "user_id", "status"],
        order_by="name asc",
        limit_page_length=limit,
    )
    result = []
    for employee in rows:
        user = employee.user_id
        enabled = bool(user and frappe.db.get_value("User", user, "enabled"))
        subject = (
            frappe.db.get_value(
                "User Social Login", {"parent": user, "provider": "frappe"}, "userid"
            )
            if user
            else ""
        )
        people = frappe.get_all(
            "Sales Person",
            filters={"employee": employee.name, "enabled": 1, "is_group": 0},
            pluck="name",
        )
        result.append(
            {
                "employee_id": employee.name,
                "user_id": user or "",
                "subject": subject or "",
                "employee_name": employee.employee_name,
                "company": employee.company,
                "active": employee.status == "Active" and enabled,
                "sales_people": people,
            }
        )
    return {
        "rows": result,
        "has_more": len(rows) == limit,
        "next_cursor": rows[-1].name if rows else cursor,
    }


@frappe.whitelist(methods=["GET"])
def resolve_identity(subject):
    require()
    if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
        frappe.throw("Invalid identity")
    users = frappe.get_all(
        "User Social Login", filters={"provider": "frappe", "userid": subject}, pluck="parent"
    )
    if len(users) != 1 or not frappe.db.get_value("User", users[0], "enabled"):
        return {"employee": None}
    matches = frappe.get_all(
        "Employee",
        filters={"user_id": users[0], "status": "Active"},
        fields=["name", "employee_name", "company"],
    )
    if len(matches) != 1:
        return {"employee": None}
    employee = matches[0]
    people = frappe.get_all(
        "Sales Person",
        filters={"employee": employee.name, "enabled": 1, "is_group": 0},
        pluck="name",
    )
    return {
        "employee": {
            "employee_id": employee.name,
            "user_id": users[0],
            "subject": subject,
            "employee_name": employee.employee_name,
            "company": employee.company,
            "active": True,
            "sales_people": people,
        }
    }


@frappe.whitelist(methods=["GET"])
def capabilities():
    require()
    missing = []
    for dt, name in [
        ("Company", COMPANY),
        ("Branch", BRANCH),
        ("Customer Group", GROUP),
        ("Customer Sub Groups", SUBGROUP),
        ("Payment Terms Template", "TBD"),
        ("Sales Taxes and Charges Template", "UAE VAT 5 Percent - LDW"),
    ]:
        if not frappe.db.exists(dt, name):
            missing.append(dt)
    meta = frappe.get_meta("Catalogue Account")
    fields = all(meta.has_field(f) for f in ("existing_customer", "submitted_referral_employee_id"))
    tax = (
        frappe.get_doc("Sales Taxes and Charges Template", "UAE VAT 5 Percent - LDW")
        if "Sales Taxes and Charges Template" not in missing
        else None
    )
    taxes = (
        [
            {
                "charge_type": r.charge_type,
                "rate": r.rate,
                "included_in_print_rate": r.included_in_print_rate,
            }
            for r in tax.taxes
        ]
        if tax
        else []
    )
    safe_tax = bool(
        taxes
        and all(
            r["charge_type"] == "On Net Total" and not r["included_in_print_rate"] for r in taxes
        )
    )
    return {
        "version": "0.4.0",
        "employee_portal": fields,
        "orders_ready": fields and not missing and ROLE in frappe.get_roles() and safe_tax,
        "missing": missing,
        "order_permission": ROLE in frappe.get_roles(),
        "company": COMPANY,
        "branch": BRANCH,
        "tax_rate": str(sum(Decimal(str(r["rate"])) for r in taxes)) if safe_tax else None,
    }


@frappe.whitelist(methods=["POST"])
def configure_login(origin):
    # Explicit owner setup permission, never granted to ordinary employees.
    if frappe.session.user == "Guest" or not set(frappe.get_roles()) & {
        "System Manager",
        "Catalogue Portal Setup",
    }:
        frappe.throw("Portal setup permission required", frappe.PermissionError)
    from urllib.parse import urlsplit

    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        frappe.throw("A valid HTTPS store origin is required")
    callback = origin.rstrip("/") + "/api/v1/employee/auth/callback"
    name = frappe.db.get_value(
        "OAuth Client", {"app_name": "LEDWorld Employee Portal", "redirect_uris": callback}, "name"
    )
    if name:
        client = frappe.get_doc("OAuth Client", name)
    else:
        client = frappe.get_doc(
            {
                "doctype": "OAuth Client",
                "app_name": "LEDWorld Employee Portal",
                "redirect_uris": callback,
                "default_redirect_uri": callback,
                "scopes": "openid",
                "grant_type": "Authorization Code",
                "response_type": "Code",
                "skip_authorization": 0,
            }
        )
        client.insert(ignore_permissions=True)
    return {"client_id": client.client_id, "client_secret": client.client_secret}


def valid_uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        frappe.throw("Invalid website identifier")


def status(doc):
    return {
        "name": doc.name,
        "customer": doc.customer,
        "docstatus": doc.docstatus,
        "status": doc.status,
        "currency": doc.currency,
        "net_total": str(doc.net_total),
        "tax_total": str(doc.total_taxes_and_charges),
        "grand_total": str(doc.grand_total),
        "delivery_date": str(doc.delivery_date or ""),
        "modified": str(doc.modified),
        "per_delivered": doc.per_delivered,
        "per_billed": doc.per_billed,
        "sales_people": [r.sales_person for r in doc.sales_team],
        "items": [
            {
                "item_code": r.item_code,
                "qty": str(r.qty),
                "rate": str(r.rate),
                "amount": str(r.amount),
            }
            for r in doc.items
        ],
    }


def ensure_customer(account, payload):
    linked = account.get("existing_customer")
    if linked:
        if not frappe.db.exists("Customer", linked) or frappe.db.get_value(
            "Customer", linked, "disabled"
        ):
            frappe.throw("Linked customer is unavailable")
        return frappe.get_doc("Customer", linked)
    key = account.website_account_id
    existing = frappe.db.get_value("Customer", {"custom_catalogue_account_id": key}, "name")
    if existing:
        customer = frappe.get_doc("Customer", existing)
    else:
        address = payload["shipping_address"]
        city = address["city"].strip()
        territory = frappe.db.get_value("Territory", {"name": city, "is_group": 0}, "name")
        if not territory and address["country"] != "AE":
            territory = frappe.db.get_value(
                "Territory", {"name": "Rest Of The World", "is_group": 0}, "name"
            )
        if not territory:
            frappe.throw("Customer territory needs staff review")
        customer = frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": account.organisation_name or account.display_name,
                "customer_type": "Company" if account.organisation_name else "Individual",
                "customer_group": GROUP,
                "custom_sub_group": SUBGROUP,
                "territory": territory,
                "custom_catalogue_account_id": key,
                "assign_to_branch": [{"branch_list": BRANCH}],
            }
        )
        # Apply actual field defaults (e.g. GST category) without bypassing mandatory validation.
        customer.insert(ignore_permissions=True)
    account.existing_customer = customer.name
    account.save(ignore_permissions=True)
    return customer


def ensure_contact_address(account, customer, payload):
    key = account.website_account_id
    contact_name = frappe.db.get_value("Contact", {"custom_catalogue_account_id": key}, "name")
    if not contact_name:
        contact = frappe.get_doc(
            {
                "doctype": "Contact",
                "first_name": account.display_name,
                "email_ids": [{"email_id": account.email, "is_primary": 1}],
                "phone_nos": [{"phone": account.phone_e164, "is_primary_mobile_no": 1}],
                "links": [{"link_doctype": "Customer", "link_name": customer.name}],
                "custom_catalogue_account_id": key,
            }
        )
        contact.insert(ignore_permissions=True)
        contact_name = contact.name
    else:
        if not frappe.db.exists(
            "Dynamic Link",
            {
                "parenttype": "Contact",
                "parent": contact_name,
                "link_doctype": "Customer",
                "link_name": customer.name,
            },
        ):
            contact = frappe.get_doc("Contact", contact_name)
            contact.append("links", {"link_doctype": "Customer", "link_name": customer.name})
            contact.save(ignore_permissions=True)
    address = payload["shipping_address"]
    address_key = hashlib.sha256(
        (customer.name + json.dumps(address, sort_keys=True)).encode()
    ).hexdigest()
    address_name = frappe.db.get_value(
        "Address", {"custom_catalogue_address_key": address_key}, "name"
    )
    if not address_name:
        country = frappe.db.get_value("Country", {"code": address["country"].lower()}, "name")
        if not country:
            frappe.throw("Delivery country needs staff review")
        row = frappe.get_doc(
            {
                "doctype": "Address",
                "address_title": account.organisation_name or account.display_name,
                "address_type": "Shipping",
                "address_line1": address["address"],
                "address_line2": address.get("unit") or "",
                "city": address["city"],
                "country": country,
                "pincode": address.get("postcode") or "",
                "phone": address["phone"],
                "email_id": account.email,
                "links": [{"link_doctype": "Customer", "link_name": customer.name}],
                "custom_catalogue_address_key": address_key,
            }
        )
        row.insert(ignore_permissions=True)
        address_name = row.name
    return contact_name, address_name


def allocate_stock(item, qty, requested_warehouses):
    if not item.is_stock_item:
        return [(None, qty)]
    allowed = list(requested_warehouses or [])
    if not allowed:
        default = frappe.db.get_value("Company", COMPANY, "default_warehouse")
        allowed = [default] if default else []
    warehouses = (
        frappe.get_all(
            "Warehouse",
            filters={"name": ["in", allowed], "company": COMPANY, "disabled": 0, "is_group": 0},
            pluck="name",
        )
        if allowed
        else []
    )
    bins = (
        frappe.get_all(
            "Bin",
            filters={"item_code": item.name, "warehouse": ["in", warehouses]},
            fields=["warehouse", "actual_qty", "reserved_qty"],
            order_by="warehouse asc",
        )
        if warehouses
        else []
    )
    remaining, allocations = qty, []
    for row in bins:
        available = max(
            Decimal(str(row.actual_qty or 0)) - Decimal(str(row.reserved_qty or 0)), Decimal(0)
        )
        take = min(available, remaining)
        if take > 0:
            allocations.append((row.warehouse, take))
            remaining -= take
        if remaining <= 0:
            break
    if remaining > 0:
        frappe.throw("Configured selling-company warehouse stock needs staff review")
    return allocations


@frappe.whitelist(methods=["POST"])
def place_order(payload):
    require(ROLE)
    payload = frappe.parse_json(payload) if isinstance(payload, str) else payload
    if not isinstance(payload, dict):
        frappe.throw("Invalid order")
    order_id = valid_uuid(payload.get("order_id"))
    account_id = valid_uuid(payload.get("account_id"))
    existing = frappe.db.get_value("Sales Order", {"custom_catalogue_order_id": order_id}, "name")
    if existing:
        doc = frappe.get_doc("Sales Order", existing)
        if (
            str(
                frappe.db.get_value(
                    "Catalogue Account", doc.custom_catalogue_account, "website_account_id"
                )
            )
            != account_id
        ):
            frappe.throw("Order identity conflict")
        return {"state": "synced", "order": status(doc)}
    account_name = frappe.db.get_value(
        "Catalogue Account", {"website_account_id": account_id}, "name"
    )
    if not account_name:
        return {"state": "waiting_account", "code": "account_sync_pending"}
    # Serialize Customer creation and orders for this account in the same ERP transaction.
    frappe.db.sql("SELECT name FROM `tabCatalogue Account` WHERE name=%s FOR UPDATE", account_name)
    existing = frappe.db.get_value("Sales Order", {"custom_catalogue_order_id": order_id}, "name")
    if existing:
        return {"state": "synced", "order": status(frappe.get_doc("Sales Order", existing))}
    account = frappe.get_doc("Catalogue Account", account_name)
    if not account.email_verified or account.website_disabled:
        return {"state": "needs_attention", "code": "account_ineligible"}
    from catalog_sync.accounts.commercial import valid_sales_person

    if not valid_sales_person(account.get("assigned_sales_person")):
        return {"state": "waiting_assignment", "code": "salesperson_required"}
    if payload.get("pricing_tier") == "trade" and account.workflow_state != "Approved Trader":
        return {"state": "needs_attention", "code": "trader_approval_changed"}
    if not capabilities()["orders_ready"]:
        return {"state": "needs_attention", "code": "erp_setup_required"}
    items = payload.get("items") or []
    if not 1 <= len(items) <= 50:
        frappe.throw("Invalid order lines")
    delivery = getdate(payload.get("requested_delivery_date"))
    if delivery < getdate(nowdate()):
        return {"state": "needs_attention", "code": "delivery_date_passed"}
    try:
        customer = ensure_customer(account, payload)
        contact, address = ensure_contact_address(account, customer, payload)
        doc = frappe.get_doc(
            {
                "doctype": "Sales Order",
                "company": COMPANY,
                "branch": BRANCH,
                "customer": customer.name,
                "contact_person": contact,
                "shipping_address_name": address,
                "order_type": "Sales",
                "transaction_date": nowdate(),
                "delivery_date": delivery,
                "currency": "AED",
                "conversion_rate": 1,
                "selling_price_list": payload["primary_price_list"],
                "price_list_currency": "AED",
                "plc_conversion_rate": 1,
                "ignore_pricing_rule": 1,
                "custom_catalogue_order_id": order_id,
                "custom_catalogue_account": account.name,
                "custom_catalogue_referrer": account.get("referral_employee"),
                "custom_catalogue_notes": str(payload.get("notes") or "")[:1000],
                "po_no": payload.get("customer_po") or "",
                "sales_team": [
                    {"sales_person": account.assigned_sales_person, "allocated_percentage": 100}
                ],
            }
        )
        for line in items:
            qty, rate = Decimal(str(line["qty"])), Decimal(str(line["price"]["amount"]))
            if (
                not qty.is_finite()
                or not rate.is_finite()
                or qty <= 0
                or rate <= 0
                or qty > 1000000
            ):
                frappe.throw("Invalid order quantity or price")
            item = frappe.get_cached_doc("Item", line["item_code"])
            if item.disabled or not item.is_sales_item:
                frappe.throw("Item no longer available")
            for warehouse, allocated_qty in allocate_stock(item, qty, payload.get("warehouses")):
                doc.append(
                    "items",
                    {
                        "item_code": item.name,
                        "item_name": item.item_name,
                        "description": item.description or item.item_name,
                        "qty": float(allocated_qty),
                        "warehouse": warehouse,
                        "uom": item.stock_uom,
                        "stock_uom": item.stock_uom,
                        "conversion_factor": 1,
                        "delivery_date": delivery,
                        "price_list_rate": float(rate),
                        "rate": float(rate),
                        "discount_percentage": 0,
                        "custom_catalogue_primary_list": line["price"].get("primary_price_list")
                        or payload["primary_price_list"],
                    },
                )
        doc.flags.ignore_permissions = True
        doc.set_missing_values()
        tax_template = getattr(customer, "taxes_and_charges", None) or "UAE VAT 5 Percent - LDW"
        # Resolve an actual tax-category default when the customer is classified.
        if getattr(customer, "tax_category", None):
            tax_template = (
                frappe.db.get_value(
                    "Sales Taxes and Charges Template",
                    {"company": COMPANY, "tax_category": customer.tax_category, "disabled": 0},
                    "name",
                )
                or tax_template
            )
        tax = frappe.get_doc("Sales Taxes and Charges Template", tax_template)
        if tax.company != COMPANY or tax.disabled:
            frappe.throw("Tax template needs staff review")
        doc.taxes_and_charges = tax.name
        doc.set("taxes", [])
        for row in tax.taxes:
            values = row.as_dict()
            for key in ("name", "parent", "parentfield", "parenttype", "idx", "doctype"):
                values.pop(key, None)
            doc.append("taxes", values)
        doc.payment_terms_template = customer.get("payment_terms") or "TBD"
        doc.calculate_taxes_and_totals()
        doc.set_payment_schedule()
        doc.flags.ignore_permissions = True
        doc.insert()
        # Enforce the quoted unit rates after all site validations have executed.
        requested = {line["item_code"]: line for line in items}
        actual_quantities = {code: Decimal(0) for code in requested}
        for actual in doc.items:
            line = requested.get(actual.item_code)
            if not line or abs(
                Decimal(str(actual.rate)) - Decimal(str(line["price"]["amount"]))
            ) > Decimal("0.000001"):
                frappe.throw("ERP pricing changed; review required")
            actual_quantities[actual.item_code] += Decimal(str(actual.qty))
        if any(
            actual_quantities[code] != Decimal(str(line["qty"])) for code, line in requested.items()
        ):
            frappe.throw("ERP quantities changed; review required")
        if doc.docstatus != 0:
            frappe.throw("Website orders must remain drafts")
        return {"state": "synced", "order": status(doc)}
    except (
        frappe.ValidationError,
        frappe.MandatoryError,
        frappe.LinkValidationError,
        frappe.DuplicateEntryError,
        InvalidOperation,
    ):
        # Some existing ERP Contact hooks commit their own link updates.
        # A full request rollback also works after that commit; unique website IDs
        # allow the worker to reuse any partially created masters on retry.
        frappe.db.rollback()
        return {"state": "needs_attention", "code": "erp_validation_required"}


@frappe.whitelist(methods=["GET"])
def order_status(order_id):
    require(ROLE)
    order_id = valid_uuid(order_id)
    name = frappe.db.get_value("Sales Order", {"custom_catalogue_order_id": order_id}, "name")
    return {"order": status(frappe.get_doc("Sales Order", name)) if name else None}
