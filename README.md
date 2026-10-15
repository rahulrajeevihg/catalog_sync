# Catalogue Sync for ERPNext

Catalogue synchronization and website account approvals for Frappe/ERPNext v14 and Python 3.10+.
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
The exporter creates no users, API tokens or scheduled jobs and provides no write
endpoints. Version 0.2.0 adds account metadata and approval guards during app
installation/migration, described below. API tokens belong in protected server configuration.

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


## Website accounts and Traders (0.2.0)

App install and `bench migrate` create/update the custom **Catalogue Account**
DocType, **Catalogue Account Approval** workflow, private-file permissions and
**Website Accounts** workspace with a filtered **Traders** shortcut. Existing
item/price/stock exports and their allowlist remain unchanged. No core ERP files
are modified. Metadata is included in the standalone app package, so Frappe Cloud
receives it through the normal app deployment and site migration; no console
access or separate metadata upload is needed on the live site.

For an existing development installation:

```sh
bench --site your-site.example.com migrate
```

For Frappe Cloud, deploy the updated app branch/version to the compatible v14
bench and run the site's normal update/migration. Keep website account flags
disabled until the integration user, email and permission checks pass.

Give the dedicated standard-REST integration user **Catalogue Account Integration**.
Give authorized ERP reviewers **Catalogue Trader Approver**. Do not assign
System Manager or the approver role to the website integration. The auxiliary
**Catalogue Account Editor** role is used by the workflow, not as a substitute
for these access roles. The app creates roles, not users or credentials.

The workflow is:

- Ordinary completed registrations: Registered.
- Verified trader applicant with checked proof: Pending Review.
- Reviewer actions: Approve, Reject (with applicant-facing notes), Suspend and Reactivate.
- The integration can submit/resubmit/withdraw; it cannot approve/reject/reactivate.

Approval/reactivation requires a verified, enabled organisation and actual private
proof files attached to that account. Integration access can read applicant-facing
review notes but cannot edit them or read internal staff notes. Public/Guest and
unrelated ERP users cannot read accounts or their private proofs.

Account creation/update and private upload use standard Frappe REST APIs; no new
app write endpoints are introduced. Passwords and hashes never enter ERP. Account
credentials stay separate from the export credentials and its read-only allowlist.
Changing app code does not enable account synchronization or trader prices on the
website, send emails, or create sample accounts.

The installer is idempotent and preserves account records, extra site fields,
unrelated DocType roles and existing File permissions. App-owned schema fields,
workflow definitions and managed role permissions are reapplied on migration.
Back up the site before deployment. Removing the code does not remove account
records or custom metadata; do not delete these as a rollback step.

## Website password controls (0.2.1)

Saved Catalogue Account forms expose **Manage website password** to System Manager
and Catalogue Trader Approver roles. This opens the website owner administration
page for that UUID. Separate website owner authentication is required; ERP roles
never grant owner website access. Existing passwords and hashes are never exported
or stored in ERP, and setting a password never bypasses email verification.

The website owner can set a new password (six characters minimum) or queue a reset
email. Setting a password revokes website sessions and old verification/reset links;
Google identities, approval and profile data are retained. Password edits occur
only in the website authentication service, outside ERP storage and logs.

Optional site configuration `catalogue_account_store_url` overrides the default
`https://store.ledworld.com`. Set an HTTPS origin without a path, query or credentials.
Update the app and migrate the site to install its Client Script and boot hook.

## Referrals and account primary prices (0.3.0)

Trader applications may supply a five-digit employee referral suffix. Catalogue
Account retains the original code and separate referring Employee, employee name
and company snapshots. Employee attribution is independent of Assigned Sales Person.
An eligible salesperson is suggested only for one enabled, non-group link to the
employee; manual assignments are retained. Snapshots change only on first attribution
or a deliberate staff correction, rather than when an employee transfers company.

Approvers select the Primary Price List (default Trade Price). Choices are enabled
AED selling lists, excluding Promo and cost/buying lists, except the explicitly
supported dual-use Trade Price list. Approval additionally requires a resolved
entered referral and an enabled, non-group salesperson. Existing approved records
retain approval and show a warning when salesperson assignment is missing.

Commercial fields use permission level 4: Catalogue Trader Approver may edit;
Catalogue Account Integration may read. Integration remains unable to read internal
notes at level 1 or write commercial assignments. Changes are tracked by ERP and a
separate commercial revision, independent of website profile/application revisions.

Authenticated GET methods under `/api/method/catalog_sync.accounts.commercial.`:

- `capabilities`: advertises readiness only after metadata and level-4 read access exist.
- `get_referral_directory`: bounded pages of employee ID/name/company, active status,
  referral suffix and linked eligible salesperson names. Access requires the
  Catalogue Account Integration role. No HR salary, personal contacts or accounting
  fields are returned, and broad Employee REST read permissions are not required.

Desk link queries for employees and salespeople are restricted to System Manager or
Catalogue Trader Approver and return only the minimal assignment fields. Invoice,
Customer and Sales Order creation is outside this version.

Deploy/update the app and migrate the site through Frappe Cloud. The website detects
capability readiness automatically; older ERP installations continue their existing
account approval synchronization until this migration is available.

## 0.4.0 — Employee portal and draft Sales Orders

Adds restricted `catalog_sync.portal.api` methods for employee identity/directory,
OAuth client setup, Customer linking and idempotent **draft** Sales Orders.
Migration installs unique website identifiers, Existing Customer on Catalogue
Account, and the LEDWORLD - ONLINE / Website Customers / Website Registration
masters. Existing catalogue exports remain read-only and retain their permissions.

Use **Catalogue Account Integration** for the private directory and account reads,
**Catalogue Order Integration** for the draft-order RPC, and **Catalogue Portal
Setup** for initial OAuth configuration. These roles do not grant general HR or
Sales Order REST access. The bridge validates real mandatory data, selling-company
warehouse stock, rates, taxes, payment schedule and salesperson allocation. It
never submits orders, invoices, takes payments or reserves stock. Unique website
IDs allow safe retries after lost responses and partial master creation.

Website activation is separately gated. Deploy this standalone app and migrate
before connecting employee sign-in in the website owner panel. ERP users need an
enabled User linked to exactly one active Employee through `Employee.user_id`.

## 0.4.1 — Explicit employee portal upgrade patch

Adds the tracked post-schema patch `catalog_sync.patches.employee_portal_v040`.
The original accounts patch remains unchanged because earlier sites already ran it.
Both the new patch and normal migration hook use the same additive, idempotent
installer; no accounts, quotations, prices or existing orders are deleted.

The restricted portal capabilities response now reports the actual package version,
upgrade-patch status and missing fields/roles. Draft-order readiness requires all
website identifiers and order fields, in addition to the existing masters and permissions.
Deploy the latest `main` commit to the ERP site's bench and site through Frappe Cloud.
