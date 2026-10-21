"""Immutable aggregate updates, published only when an ERP generation completes."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import frappe

from catalog_sync.sales.setup import METRIC_FIELDS, ROLE

STATE = "Catalogue Sales State"


def enabled():
    return bool(frappe.conf.get("catalog_sales_insights_enabled", False))


def require():
    if (
        not enabled()
        or frappe.session.user == "Guest"
        or frappe.session.user not in (frappe.conf.get("catalog_sync_users") or [])
        or ROLE not in frappe.get_roles(frappe.session.user)
    ):
        frappe.throw("Sales aggregate integration access required", frappe.PermissionError)


def key(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def _upsert(doctype, name, values):
    # Identifiers are module constants; user input is always passed as parameters.
    columns = ["name", "creation", "modified", "owner", "modified_by", "docstatus", "idx", *values]
    now = frappe.utils.now_datetime()
    args = [name, now, now, "Administrator", "Administrator", 0, 0, *values.values()]
    updates = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in ["modified", *values])
    frappe.db.sql(
        "INSERT INTO `tab{}` ({}) VALUES ({}) ON DUPLICATE KEY UPDATE {}".format(
            doctype,
            ",".join(f"`{c}`" for c in columns),
            ",".join(["%s"] * len(columns)),
            updates,
        ),
        args,
    )


def mark_items(codes):
    if not enabled():
        return
    for code in sorted(set(c for c in codes if c)):
        _upsert("Catalogue Sales Dirty", key(code), {"item_code": code, "version": uuid4().hex})


def invoice_changed(doc, method=None):
    if not enabled():
        return
    old = doc.get_doc_before_save()
    mark_items(
        [row.item_code for source in (old, doc) if source for row in (source.get("items") or [])]
    )


def item_changed(doc, method=None):
    mark_items([doc.item_code])


def _mark_all():
    # Reconciliation includes vanished items, so their earlier totals can be cleared.
    frappe.db.sql("""
        INSERT INTO `tabCatalogue Sales Dirty`
            (name, creation, modified, owner, modified_by, docstatus, idx, item_code, version)
        SELECT SHA2(source.item_code,256), NOW(6), NOW(6), 'Administrator', 'Administrator', 0, 0,
               source.item_code, REPLACE(UUID(),'-','')
        FROM (SELECT item_code FROM `tabItem`
              UNION SELECT item_code FROM `tabCatalogue Sales Metric`) source
        ON DUPLICATE KEY UPDATE version=VALUES(version), modified=VALUES(modified)
    """)


def aggregate(codes, start, end):
    """Group by customer before counting, so returns and cross-company repeats are correct."""
    rows = frappe.db.sql(
        """
        SELECT item_code, SUM(net_qty) lifetime_quantity, SUM(recent_qty) recent_quantity,
               SUM(net_qty > 0) lifetime_customers, SUM(recent_qty > 0) recent_customers,
               SUM(invoice_count) lifetime_invoices, SUM(recent_invoice_count) recent_invoices
        FROM (
            SELECT i.item_code, s.customer,
                   SUM(i.stock_qty) net_qty,
                   SUM(CASE WHEN s.posting_date >= %(start)s
                       THEN i.stock_qty ELSE 0 END) recent_qty,
                   COUNT(DISTINCT CASE WHEN IFNULL(s.is_return,0)=0 AND i.stock_qty>0
                       THEN s.name END) invoice_count,
                   COUNT(DISTINCT CASE WHEN IFNULL(s.is_return,0)=0 AND i.stock_qty>0
                       AND s.posting_date >= %(start)s THEN s.name END) recent_invoice_count
            FROM `tabSales Invoice Item` i JOIN `tabSales Invoice` s ON s.name=i.parent
            WHERE i.parenttype='Sales Invoice' AND i.item_code IN %(codes)s
              AND s.docstatus=1 AND IFNULL(s.is_internal_customer,0)=0 AND s.posting_date <= %(end)s
            GROUP BY i.item_code, s.customer
        ) customer_sales GROUP BY item_code
    """,
        {"codes": tuple(codes), "start": start, "end": end},
        as_dict=True,
    )
    return {row["item_code"]: row for row in rows}


def _set_state(state, **values):
    # Update only owned fields: a reconciliation request arriving during work must survive.
    for field, value in values.items():
        frappe.db.set_single_value(STATE, field, value)
        setattr(state, field, value)


def process(max_batches=20):
    """Resumable bounded work. Incomplete generations are never exported."""
    if not enabled():
        return
    if not frappe.db.sql("SELECT GET_LOCK('catalogue_sales_metrics',0)")[0][0]:
        return
    try:
        state = frappe.get_single(STATE)
        today = datetime.now(ZoneInfo("Asia/Dubai")).date()
        if not int(state.building_generation or 0):
            token = state.reconcile_requested or ""
            if str(state.reconciled_on or "") != str(today) or token:
                _mark_all()
            _set_state(
                state,
                building_generation=int(state.completed_generation or 0) + 1,
                build_date=today,
                reconciled_on=today,
                force_publication=int(bool(token)),
                build_request=token,
            )
            if token:
                frappe.db.sql(
                    "DELETE FROM `tabSingles` WHERE doctype=%s AND field=%s AND value=%s",
                    (STATE, "reconcile_requested", token),
                )
            frappe.db.commit()
        generation = int(state.building_generation)
        end = frappe.utils.getdate(state.build_date)
        start = end - timedelta(days=29)
        for _ in range(max_batches):
            dirty = frappe.get_all(
                "Catalogue Sales Dirty",
                fields=["name", "item_code", "version"],
                order_by="name asc",
                limit_page_length=200,
            )
            if not dirty:
                _upsert(
                    "Catalogue Sales Generation",
                    str(generation),
                    {
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "period_start": start,
                        "period_end": end,
                        "reconciliation_token": getattr(state, "build_request", "")
                        or getattr(state, "reconciled_request", "")
                        or "",
                    },
                )
                _set_state(
                    state,
                    completed_generation=generation,
                    building_generation=0,
                    reconciled_request=getattr(state, "build_request", "")
                    or getattr(state, "reconciled_request", "")
                    or "",
                    build_request="",
                    force_publication=0,
                )
                frappe.db.commit()
                return
            codes = [row.item_code for row in dirty]
            totals = aggregate(codes, start, end)
            uoms = {
                row.item_code: row.stock_uom
                for row in frappe.get_all(
                    "Item", filters={"item_code": ["in", codes]}, fields=["item_code", "stock_uom"]
                )
            }
            previous = {
                row.item_code: row
                for row in frappe.get_all(
                    "Catalogue Sales Metric",
                    filters={"item_code": ["in", codes]},
                    fields=["item_code", "stock_uom", *METRIC_FIELDS],
                )
            }
            for row in dirty:
                value = {"item_code": row.item_code, "stock_uom": uoms.get(row.item_code, "")}
                value.update(
                    {
                        field: Decimal(str(totals.get(row.item_code, {}).get(field) or 0))
                        for field in METRIC_FIELDS
                    }
                )
                old = previous.get(row.item_code)
                changed = (
                    old is None
                    or old.stock_uom != value["stock_uom"]
                    or any(Decimal(str(old.get(f) or 0)) != value[f] for f in METRIC_FIELDS)
                )
                if changed or getattr(state, "force_publication", 0):
                    _upsert("Catalogue Sales Metric", key(row.item_code), value)
                    _upsert(
                        "Catalogue Sales Update",
                        key(str(generation) + ":" + row.item_code),
                        {**value, "generation": generation},
                    )
                # A hook arriving during aggregation changes the version and survives this deletion.
                frappe.db.sql(
                    "DELETE FROM `tabCatalogue Sales Dirty` WHERE name=%s AND version=%s",
                    (row.name, row.version),
                )
            frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK('catalogue_sales_metrics')")


def tick():
    """Scheduler runs every minute; ordinary refreshes are hourly, reconciliation is nightly."""
    if not enabled():
        return
    state = frappe.get_single(STATE)
    completed = int(state.completed_generation or 0)
    stamp = (
        frappe.db.get_value("Catalogue Sales Generation", str(completed), "completed_at")
        if completed
        else None
    )
    now = datetime.now(timezone.utc)
    if (
        state.building_generation
        or state.reconcile_requested
        or not stamp
        or now - datetime.fromisoformat(stamp) >= timedelta(hours=1)
        or str(state.reconciled_on or "") != str(now.astimezone(ZoneInfo("Asia/Dubai")).date())
    ):
        process()


@frappe.whitelist(methods=["GET"])
def get_sales_metrics(after_generation=0, until_generation=None, cursor=None, limit=500):
    require()
    after = int(after_generation)
    state = frappe.get_single(STATE)
    completed = int(state.completed_generation or 0)
    until = completed if until_generation is None else int(until_generation)
    if after < 0 or until < after or until > completed:
        frappe.throw("Invalid aggregate window")
    if until == 0:
        return {"ready": False}
    header = frappe.get_doc("Catalogue Sales Generation", str(until))
    position = json.loads(cursor) if isinstance(cursor, str) else cursor
    limit = min(2000, max(1, int(limit)))
    # Explicit keyset predicate keeps the immutable window stable while later generations build.
    params = {"after": after, "until": until, "limit": limit + 1}
    predicate = ""
    if position:
        if not isinstance(position, dict) or set(position) != {"generation", "name"}:
            frappe.throw("Invalid aggregate cursor")
        params.update(
            position_generation=int(position["generation"]), position_name=str(position["name"])
        )
        if not after < params["position_generation"] <= until:
            frappe.throw("Invalid aggregate cursor")
        predicate = (
            " AND (generation > %(position_generation)s OR "
            "(generation=%(position_generation)s AND name>%(position_name)s))"
        )
    rows = frappe.db.sql(
        """SELECT name,item_code,stock_uom,generation,{} FROM `tabCatalogue Sales Update`
           WHERE generation>%(after)s AND generation<=%(until)s{}
           ORDER BY generation,name LIMIT %(limit)s""".format(
            ",".join(METRIC_FIELDS),
            predicate,
        ),
        params,
        as_dict=True,
    )
    more = len(rows) > limit
    rows = rows[:limit]
    for row in rows:
        for field in METRIC_FIELDS:
            row[field] = str(row[field]) if field.endswith("quantity") else int(row[field])
    return {
        "ready": True,
        "contract_version": 1,
        "rows": rows,
        "has_more": more,
        "next_cursor": {"generation": int(rows[-1].generation), "name": rows[-1].name}
        if more
        else None,
        "after_generation": after,
        "until_generation": until,
        "snapshot_at": header.completed_at,
        "period_start": str(header.period_start),
        "period_end": str(header.period_end),
        "reconciliation_token": header.reconciliation_token or "",
    }


@frappe.whitelist(methods=["POST"])
def request_reconciliation():
    require()
    token = uuid4().hex
    frappe.db.set_single_value(STATE, "reconcile_requested", token)
    return {"queued": True, "token": token}
