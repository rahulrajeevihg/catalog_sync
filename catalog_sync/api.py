"""Read-only exports. Both the site allowlist and ordinary Frappe permissions apply."""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import get_datetime, get_system_timezone, now_datetime

# Explicit catalogue fields only: never export valuation, buying rates, supplier or accounting data.
ITEM_FIELDS = (
    "item_code",
    "item_name",
    "item_group",
    "brand",
    "description",
    "image",
    "disabled",
    "is_stock_item",
    "stock_uom",
    "has_variants",
    "variant_of",
)
ATTRIBUTE_FIELDS = (
    "height",
    "width",
    "depth",
    "range",
    "lamp_qty",
    "safety_class",
    "eec",
    "beam_angle",
    "lumen_output",
    "reflector",
    "mounting",
    "ip_rate",
    "output_signal",
    "power_factor",
    "working_temp",
    "life_time",
    "output_current",
    "output_voltage",
    "light_intensity",
    "color_temp_",
    "light_source",
    "lamp_type",
    "cri",
    "power",
    "input",
    "efficacy",
    "dimension",
    "operating_frequency",
    "input_signal",
    "function",
    "cut_out",
    "material",
    "body_finish",
    "shade_material",
    "shade_finish",
    "warranty_type_",
    "warranty_",
    "primary_material",
    "secondary_material",
    "capacity",
    "primary_color",
    "secondary_color",
    "diffuser",
    "short_descrition",
    "datasheet",
)
FIELDS = {
    "Item": ITEM_FIELDS + ATTRIBUTE_FIELDS,
    "Item Price": (
        "item_code",
        "price_list",
        "currency",
        "uom",
        "price_list_rate",
        "valid_from",
        "valid_upto",
        "selling",
        "buying",
        "customer",
        "supplier",
        "batch_no",
        "packing_unit",
    ),
    "Bin": ("item_code", "warehouse", "actual_qty", "reserved_qty", "projected_qty"),
    "Brand": ("brand", "image"),
    "Item Group": ("item_group_name", "parent_item_group", "is_group", "disable", "image"),
    "Item Attribute": ("attribute_name", "numeric_values", "from_range", "to_range", "increment"),
    "Warehouse": ("warehouse_name", "parent_warehouse", "is_group", "disabled", "company"),
    "Price List": ("price_list_name", "currency", "enabled", "buying", "selling"),
}


def _authorize(doctype):
    users = frappe.conf.get("catalog_sync_users") or []
    if (
        not isinstance(users, list)
        or frappe.session.user == "Guest"
        or frappe.session.user not in users
    ):
        frappe.throw("Catalogue integration access is not configured", frappe.PermissionError)
    frappe.has_permission(doctype, "read", throw=True)


def _local(value, timezone):
    parsed = (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, str)
        else get_datetime(value)
    )
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone).replace(tzinfo=None)
    return parsed


def _iso(value, source_timezone):
    return get_datetime(value).replace(tzinfo=source_timezone).astimezone(timezone.utc).isoformat()


def _children(rows, doctype, parenttype, fields):
    if not rows:
        return
    names = [row["name"] for row in rows]
    # Parent names came from a permission-filtered query; child read checks use the parent doctype.
    children = frappe.get_list(
        doctype,
        parent_doctype=parenttype,
        filters={"parent": ["in", names], "parenttype": parenttype},
        fields=["parent", "idx"] + fields,
        order_by="parent asc, idx asc",
        limit_page_length=0,
    )
    grouped = {}
    for child in children:
        grouped.setdefault(child.pop("parent"), []).append(dict(child))
    for row in rows:
        row["attributes" if parenttype == "Item" else "values"] = grouped.get(row["name"], [])


def _page(doctype, modified_after=None, limit=1000, cursor=None, until=None, manifest=False):
    _authorize(doctype)
    limit = int(limit)
    if limit < 1 or limit > 2000:
        frappe.throw("Limit must be between 1 and 2000")
    timezone = ZoneInfo(get_system_timezone())
    lower = _local(modified_after or "1900-01-01T00:00:00+00:00", timezone)
    upper = _local(until, timezone) if until else now_datetime()
    if upper > now_datetime() or lower > upper:
        frappe.throw("Invalid synchronization window")
    position = json.loads(cursor) if isinstance(cursor, str) else cursor
    if position and (
        position.get("doctype") != doctype
        or position.get("after") != lower.isoformat()
        or position.get("until") != upper.isoformat()
    ):
        frappe.throw("Cursor belongs to a different synchronization window")
    fields = ["name", "modified"]
    if not manifest:
        meta = frappe.get_meta(doctype)
        fields += [field for field in FIELDS[doctype] if meta.has_field(field)]
    filters = [["modified", ">=", lower], ["modified", "<=", upper]]
    rows = []
    if position:
        stamp = _local(position["modified"], timezone)
        if stamp < lower or stamp > upper or not isinstance(position.get("name"), str):
            frappe.throw("Invalid cursor position")
        rows = frappe.get_list(
            doctype,
            fields=fields,
            filters=filters + [["modified", "=", stamp], ["name", ">", position["name"]]],
            order_by="modified asc, name asc",
            limit_page_length=limit + 1,
        )
        filters.append(["modified", ">", stamp])
    if len(rows) < limit + 1:
        rows += frappe.get_list(
            doctype,
            fields=fields,
            filters=filters,
            order_by="modified asc, name asc",
            limit_page_length=limit + 1 - len(rows),
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more:
        next_cursor = {
            "doctype": doctype,
            "after": lower.isoformat(),
            "until": upper.isoformat(),
            "modified": _iso(rows[-1]["modified"], timezone),
            "name": rows[-1]["name"],
        }
    rows = [dict(row) for row in rows]
    if not manifest and doctype == "Item":
        _children(rows, "Item Variant Attribute", "Item", ["attribute", "attribute_value"])
    if not manifest and doctype == "Item Attribute":
        _children(rows, "Item Attribute Value", "Item Attribute", ["attribute_value", "abbr"])
    for row in rows:
        row["modified"] = _iso(row["modified"], timezone)
    return {
        "rows": rows,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "server_timestamp": _iso(now_datetime(), timezone),
        "until": _iso(upper, timezone),
        "modified_after": _iso(lower, timezone),
        "timezone": str(timezone),
        "contract_version": 1,
    }


@frappe.whitelist(methods=["GET"])
def get_items(modified_after=None, limit=1000, cursor=None, until=None, manifest=False):
    return _page("Item", modified_after, limit, cursor, until, bool(int(manifest)))


@frappe.whitelist(methods=["GET"])
def get_item_prices(modified_after=None, limit=1000, cursor=None, until=None, manifest=False):
    return _page("Item Price", modified_after, limit, cursor, until, bool(int(manifest)))


@frappe.whitelist(methods=["GET"])
def get_bin_updates(modified_after=None, limit=1000, cursor=None, until=None, manifest=False):
    return _page("Bin", modified_after, limit, cursor, until, bool(int(manifest)))


@frappe.whitelist(methods=["GET"])
def get_brands(modified_after=None, limit=1000, cursor=None, until=None, manifest=False):
    return _page("Brand", modified_after, limit, cursor, until, bool(int(manifest)))


@frappe.whitelist(methods=["GET"])
def get_item_groups(modified_after=None, limit=1000, cursor=None, until=None, manifest=False):
    return _page("Item Group", modified_after, limit, cursor, until, bool(int(manifest)))


@frappe.whitelist(methods=["GET"])
def get_attributes(modified_after=None, limit=1000, cursor=None, until=None, manifest=False):
    return _page("Item Attribute", modified_after, limit, cursor, until, bool(int(manifest)))


@frappe.whitelist(methods=["GET"])
def get_warehouses(modified_after=None, limit=1000, cursor=None, until=None, manifest=False):
    return _page("Warehouse", modified_after, limit, cursor, until, bool(int(manifest)))


@frappe.whitelist(methods=["GET"])
def get_price_lists(modified_after=None, limit=1000, cursor=None, until=None, manifest=False):
    return _page("Price List", modified_after, limit, cursor, until, bool(int(manifest)))
