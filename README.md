# Catalogue Sync for ERPNext

Read-only catalogue exports for Frappe/ERPNext v14 and Python 3.10+.
The app provides deterministic, permission-checked batches for a separate
catalogue read model. It does not modify items, prices, stock or core ERP files.

## Install

Use this repository's `main` branch. Install ERPNext before this app.

On a development bench:

```sh
bench get-app --branch main https://github.com/rahulrajeevihg/catalog_sync.git
bench --site your-site.example.com install-app catalog_sync
```

On Frappe Cloud, add this repository and `main` branch to the site's compatible
v14 private bench group, deploy/update the bench and site, then install the app
on the site. Follow the official guides for
[installing an app](https://docs.frappe.io/cloud/installing-an-app) and
[updating an app/site on a private bench](https://docs.frappe.io/cloud/sites/how-to-update-an-app-site-on-a-private-bench).

## Integration access

All endpoints accept authenticated GET requests only. Access requires both:

1. The exact integration user in the site's JSON configuration:
   `"catalog_sync_users": ["catalogue-integration@example.com"]`.
2. Ordinary Frappe read permissions and user restrictions for each source DocType.

The allowlist defaults to empty. Use a dedicated API user and configure this
allowlist through Frappe Cloud's site configuration UI or your development bench.
The app creates no users, roles, API tokens, scheduled jobs or event hooks. It
provides no write endpoints. API tokens belong in protected server configuration.

Authentication follows Frappe's `Authorization: token <api_key>:<api_secret>`
header. Validate access with a one-row export before starting a large import.

## Export endpoints

| Method after `/api/method/catalog_sync.` | Source |
| --- | --- |
| `get_items` | Item plus permitted catalogue custom fields and variant attributes |
| `get_item_prices` | Item Price, including eligibility restrictions |
| `get_bin_updates` | Bin quantities; valuation fields excluded |
| `get_brands` | Brand |
| `get_item_groups` | Item Group |
| `get_attributes` | Item Attribute and allowed values |
| `get_warehouses` | Warehouse identity/hierarchy, company and enabled status |
| `get_price_lists` | Price List identity, currency and selling/buying flags |

Each endpoint accepts:

- `modified_after`: ISO timestamp; initial imports may start at `1900-01-01T00:00:00Z`.
- `until`: optional fixed upper timestamp, returned by the first page.
- `limit`: 1–2000; use 500–2000 for imports.
- `cursor`: JSON object returned by the previous page.
- `manifest=1`: identities and modification times only.

Frappe's response `message` contains `rows`, `has_more`, `next_cursor`,
`modified_after`, `until`, `server_timestamp`, `timezone` and `contract_version=1`.
Exported timestamps use UTC; naive ERP database values use the site's timezone.
Ordering is `(modified, name)`. Subsequent pages must retain the original window.
Children are fetched in one query for the permission-filtered parent batch.

The item field list is explicit in `catalog_sync/api.py`. Optional custom fields
are selected only when present on the site. Review the list when adding fields.
Item master exports exclude buying rates, valuation and accounting data.
Source Item Price rows include customer, supplier, batch and selling/buying
restrictions. Keep source prices in the private read model until the publication
policy has explicitly selected eligible selling prices.

## Consumer requirements

Consumers should upsert idempotently, retain raw source values, commit each batch
and its cursor atomically, and start subsequent windows with a short overlap.
Keep normal website browsing on the separate read model. Public stock warehouses,
price lists, availability rules and image processing require explicit policies.
Manifest exports support reconciliation; the app does not itself schedule it.

Removing the site allowlist disables exports without changing ERP business data.
