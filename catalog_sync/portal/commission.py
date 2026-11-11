"""Personal commission bridge; all business rules remain in IGH Search."""

import importlib
import inspect
import re

import frappe

ROLE = "Catalogue Commission Integration"
METHODS = {
    "scope": "get_commission_scope_info",
    "summary": "get_my_commission_summary",
    "entries": "get_my_commission_entries",
    "months": "get_claimable_commission_months",
    "preview": "preview_commission_claim",
    "create": "create_commission_claim",
    "claims": "get_my_commission_claims",
    "payouts": "get_payout_history",
}
PARAMETERS = {
    "scope": set(),
    "summary": {"from_date", "to_date"},
    "entries": {"from_date", "to_date", "status", "limit", "offset"},
    "months": set(),
    "preview": {"month"},
    "create": {"month", "idempotency_key"},
    "claims": {"limit", "offset"},
    "payouts": {"limit"},
}


def unavailable(code, message):
    return {"ok": False, "code": code, "message": message}


def resolve_employee(employee_id, expected_user):
    employees = frappe.get_all(
        "Employee",
        filters={"user_id": expected_user},
        fields=["name", "status"],
        limit_page_length=2,
    )
    if (
        not expected_user
        or expected_user in ("Guest", "Administrator")
        or len(employees) != 1
        or employees[0].name != employee_id
        or employees[0].status != "Active"
        or not frappe.db.get_value("User", expected_user, "enabled")
    ):
        return None
    people = frappe.get_all(
        "Sales Person",
        filters={"employee": employee_id},
        fields=["name", "enabled", "is_group"],
        limit_page_length=2,
    )
    if len(people) != 1 or not people[0].enabled or people[0].is_group:
        return None
    return people[0].name


@frappe.whitelist(methods=["POST"])
def request(payload):
    # Authenticate the service before accepting any asserted employee identity.
    if frappe.session.user == "Guest" or ROLE not in frappe.get_roles():
        frappe.throw("Catalogue commission integration access required", frappe.PermissionError)
    if isinstance(payload, str):
        payload = frappe.parse_json(payload)
    if not isinstance(payload, dict) or set(payload) - {
        "employee_id",
        "user_id",
        "action",
        "parameters",
    }:
        frappe.throw("Invalid commission request")
    action, parameters = payload.get("action"), payload.get("parameters", {})
    if (
        action not in METHODS
        or not isinstance(parameters, dict)
        or set(parameters) - PARAMETERS[action]
    ):
        frappe.throw("Invalid commission action or parameters")
    employee_id, user = payload.get("employee_id"), payload.get("user_id")
    if not isinstance(employee_id, str) or not isinstance(user, str):
        frappe.throw("Invalid commission identity")
    person = resolve_employee(employee_id, user)
    if not person:
        return unavailable(
            "employee_not_linked",
            "Ask HR to check your active User, Employee and Sales Person links.",
        )
    try:
        api = importlib.import_module("igh_search.igh_search.commission.api")
        for name in METHODS.values():
            if not callable(getattr(api, name, None)):
                raise ImportError(name)
        # These endpoints support wider management scope unless a target is explicit.
        for name in ("summary", "entries", "payouts"):
            if "sales_person" not in inspect.signature(getattr(api, METHODS[name])).parameters:
                raise ImportError("Personal commission scope unsupported")
    except ImportError:
        return unavailable(
            "commission_unavailable",
            "The ERP commission module needs an update before this dashboard can be used.",
        )

    original_user = frappe.session.user
    # Roll back partial claim writes before returning a handled validation error.
    savepoint = "catalogue_commission"
    frappe.db.savepoint(savepoint)
    try:
        frappe.set_user(user)
        scope = api.get_commission_scope_info()
        if scope.get("sales_person") != person:
            return unavailable(
                "employee_not_linked",
                "Your commission identity could not be confirmed. "
                "Ask HR to check your account links.",
            )
        if action == "scope":
            return {
                "ok": True,
                "data": {
                    "sales_person": person,
                    "claims_enabled": bool(scope.get("claims_enabled")),
                    "unrestricted": False,
                },
            }
        # Copies prevent caller data or management roles from widening the query.
        args = dict(parameters)
        if action in ("summary", "entries", "payouts"):
            args["sales_person"] = person
        data = getattr(api, METHODS[action])(**args)
        return {"ok": True, "data": data}
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback(save_point=savepoint)
        # Only the employee's own business validation is returned, never a traceback.
        message = re.sub(r"<[^>]*>", "", str(error))[:500]
        return unavailable(
            "commission_validation", message or "ERP could not complete this commission request."
        )
    finally:
        frappe.set_user(original_user)
