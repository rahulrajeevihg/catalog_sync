"""ERP-owned attribution and commercial assignment; no HR write permissions."""

import re

import frappe

COMMERCIAL_FIELDS = (
    "referral_employee",
    "referral_employee_name",
    "referral_employee_company",
    "effective_referral_code",
    "referral_status",
    "assigned_sales_person",
    "sales_person_source",
    "primary_price_list",
    "sales_assignment_required",
)


def eligible_list(name):
    row = frappe.db.get_value(
        "Price List", name, ["currency", "selling", "buying", "enabled"], as_dict=True
    )
    return bool(
        row
        and row.currency == "AED"
        and row.selling
        and row.enabled
        and name != "Promo"
        and (not row.buying or name == "Trade Price")
    )


def valid_sales_person(name):
    row = (
        frappe.db.get_value("Sales Person", name, ["enabled", "is_group"], as_dict=True)
        if name
        else None
    )
    return bool(row and row.enabled and not row.is_group)


def populate(doc, previous):
    code = str(doc.get("submitted_referral_code") or "")
    if code and not re.fullmatch(r"[0-9]{5}", code):
        frappe.throw("Employee referral code must contain exactly five digits")
    if (
        previous
        and (previous.get("referral_locked") or previous.trader_requested)
        and code != str(previous.get("submitted_referral_code") or "")
    ):
        frappe.throw("The original submitted referral code cannot be changed")
    if previous and (previous.get("referral_locked") or previous.trader_requested):
        doc.referral_locked = 1
    if previous:
        for field in (
            "referral_employee_name",
            "referral_employee_company",
            "effective_referral_code",
            "referral_status",
            "sales_person_source",
        ):
            setattr(doc, field, previous.get(field))
    first_submission = bool(doc.trader_requested and not doc.get("referral_locked"))
    employee_changed = bool(
        previous and doc.get("referral_employee") != previous.get("referral_employee")
    )
    salesperson_changed = bool(
        previous and doc.get("assigned_sales_person") != previous.get("assigned_sales_person")
    )
    if salesperson_changed:
        doc.sales_person_source = "Manual" if doc.assigned_sales_person else ""
    if first_submission:
        doc.referral_locked = 1
        if code and not doc.get("referral_employee"):
            matches = [
                r
                for r in frappe.get_all("Employee", filters={"status": "Active"}, fields=["name"])
                if re.search(r"[0-9]{5}$", r.name) and r.name[-5:] == code
            ]
            doc.referral_status = (
                "Valid" if len(matches) == 1 else "Ambiguous" if matches else "Invalid"
            )
            if len(matches) == 1:
                doc.referral_employee = matches[0].name
        elif not code and not doc.get("referral_employee"):
            doc.referral_status = "Not supplied"
    # Snapshots change only on initial attribution or an explicit staff correction.
    if doc.get("referral_employee") and (first_submission or employee_changed):
        employee = frappe.db.get_value(
            "Employee",
            doc.referral_employee,
            ["name", "employee_name", "company", "status"],
            as_dict=True,
        )
        if (
            not employee
            or employee.status != "Active"
            or not re.search(r"[0-9]{5}$", employee.name)
        ):
            frappe.throw("Choose an active employee with a five-digit referral suffix")
        doc.referral_employee_name = employee.employee_name
        doc.referral_employee_company = employee.company
        doc.effective_referral_code = employee.name[-5:]
        doc.referral_status = "Valid"
        if doc.get("sales_person_source") != "Manual":
            people = frappe.get_all(
                "Sales Person",
                filters={"employee": employee.name, "enabled": 1, "is_group": 0},
                fields=["name"],
            )
            doc.assigned_sales_person = people[0].name if len(people) == 1 else None
            doc.sales_person_source = "Automatic" if len(people) == 1 else ""
    elif employee_changed and not doc.get("referral_employee"):
        doc.referral_employee_name = ""
        doc.referral_employee_company = ""
        doc.effective_referral_code = ""
        doc.referral_status = "Cleared by staff"
    doc.primary_price_list = doc.get("primary_price_list") or "Trade Price"
    if (
        not previous or doc.primary_price_list != previous.get("primary_price_list")
    ) and not eligible_list(doc.primary_price_list):
        frappe.throw(
            "Choose an enabled AED selling price list; Promo and cost lists cannot be assigned"
        )
    doc.sales_assignment_required = int(not valid_sales_person(doc.get("assigned_sales_person")))
    if (
        previous
        and salesperson_changed
        and doc.get("assigned_sales_person")
        and doc.sales_assignment_required
    ):
        frappe.throw("Choose an enabled, non-group Sales Person")
    doc.commercial_revision = int(previous.get("commercial_revision") or 0) if previous else 0
    if not previous or any(doc.get(k) != previous.get(k) for k in COMMERCIAL_FIELDS):
        doc.commercial_revision = (
            int(previous.get("commercial_revision") or 0) + 1 if previous else 1
        )


def validate_approval(doc):
    if doc.get("submitted_referral_code") and doc.get("referral_status") != "Valid":
        frappe.throw("Resolve the employee referral before approving this application")
    if (
        doc.get("referral_employee")
        and frappe.db.get_value("Employee", doc.referral_employee, "status") != "Active"
    ):
        frappe.throw("The referring employee is no longer active; review the referral")
    if not valid_sales_person(doc.get("assigned_sales_person")):
        frappe.throw("Assign an enabled, non-group Sales Person before approval")
    if not eligible_list(doc.get("primary_price_list")):
        frappe.throw("Assign an eligible primary price list before approval")


@frappe.whitelist(methods=["GET"])
def get_referral_directory(cursor="", limit=1000):
    if frappe.session.user == "Guest" or "Catalogue Account Integration" not in frappe.get_roles():
        frappe.throw("Account integration access required", frappe.PermissionError)
    limit = int(limit)
    if not 1 <= limit <= 1000 or len(cursor) > 255:
        frappe.throw("Invalid directory page")
    rows = frappe.get_all(
        "Employee",
        filters={"name": [">", cursor]},
        fields=["name", "employee_name", "company", "status"],
        order_by="name asc",
        limit_page_length=limit,
    )
    people = (
        frappe.get_all(
            "Sales Person",
            filters={"employee": ["in", [r.name for r in rows]], "enabled": 1, "is_group": 0},
            fields=["name", "employee"],
        )
        if rows
        else []
    )
    return {
        "rows": [
            {
                "employee_id": r.name,
                "code": r.name[-5:] if re.search(r"[0-9]{5}$", r.name) else "",
                "employee_name": r.employee_name,
                "company": r.company,
                "active": r.status == "Active",
                "sales_people": [p.name for p in people if p.employee == r.name],
            }
            for r in rows
        ],
        "has_more": len(rows) == limit,
        "next_cursor": rows[-1].name if rows else cursor,
    }


@frappe.whitelist(methods=["GET"])
def capabilities():
    if frappe.session.user == "Guest" or "Catalogue Account Integration" not in frappe.get_roles():
        frappe.throw("Account integration access required", frappe.PermissionError)
    meta = frappe.get_meta("Catalogue Account")
    fields_ready = all(
        meta.has_field(name)
        for name in (
            *COMMERCIAL_FIELDS,
            "commercial_revision",
            "submitted_referral_code",
            "referral_locked",
        )
    )
    read_permission = any(
        p.role == "Catalogue Account Integration" and p.permlevel == 4 and p.read
        for p in meta.permissions
    )
    return {"version": "0.3.0", "commercial_accounts": bool(fields_ready and read_permission)}


def _staff():
    if not set(frappe.get_roles()).intersection({"System Manager", "Catalogue Trader Approver"}):
        frappe.throw("Trader approver access required", frappe.PermissionError)


@frappe.whitelist()
def price_list_query(doctype, txt, searchfield, start, page_len, filters=None):
    _staff()
    names = frappe.get_all(
        "Price List",
        filters={"currency": "AED", "enabled": 1, "selling": 1},
        fields=["name", "buying"],
        order_by="name asc",
    )
    rows = [
        [r.name]
        for r in names
        if r.name != "Promo"
        and (not r.buying or r.name == "Trade Price")
        and str(txt).casefold() in r.name.casefold()
    ]
    start = max(0, int(start))
    return rows[start : start + min(50, max(1, int(page_len)))]


@frappe.whitelist()
def employee_query(doctype, txt, searchfield, start, page_len, filters=None):
    _staff()
    rows = frappe.get_all(
        "Employee",
        filters={"status": "Active"},
        fields=["name", "employee_name", "company"],
        order_by="name asc",
    )
    selected = [
        [r.name, r.employee_name, r.company]
        for r in rows
        if str(txt).casefold() in (r.name + " " + r.employee_name).casefold()
    ]
    start = max(0, int(start))
    return selected[start : start + min(50, max(1, int(page_len)))]


@frappe.whitelist()
def sales_person_query(doctype, txt, searchfield, start, page_len, filters=None):
    _staff()
    rows = frappe.get_all(
        "Sales Person", filters={"enabled": 1, "is_group": 0}, fields=["name"], order_by="name asc"
    )
    selected = [[r.name] for r in rows if str(txt).casefold() in r.name.casefold()]
    start = max(0, int(start))
    return selected[start : start + min(50, max(1, int(page_len)))]
