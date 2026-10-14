// Passwords remain in the website authentication service, never in ERP fields.
frappe.ui.form.on("Catalogue Account", {
  refresh(frm) {
    frm.set_query("assigned_sales_person", () => ({
      query: "catalog_sync.accounts.commercial.sales_person_query",
    }));
    frm.set_query("referral_employee", () => ({
      query: "catalog_sync.accounts.commercial.employee_query",
    }));
    frm.set_query("primary_price_list", () => ({
      query: "catalog_sync.accounts.commercial.price_list_query",
    }));
    if (
      frm.doc.sales_assignment_required ||
      (frm.doc.trader_requested && !frm.doc.assigned_sales_person)
    )
      frm.dashboard.set_headline_alert(
        "Assign a salesperson for this account.",
        "orange",
      );
    const permitted = frappe.user_roles.some((role) =>
      ["System Manager", "Catalogue Trader Approver"].includes(role),
    );
    if (!permitted || frm.is_new() || !frm.doc.website_account_id) return;
    const accountId = String(frm.doc.website_account_id);
    if (
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
        accountId,
      )
    )
      return;
    const origin = frappe.boot.catalogue_account_store_url;
    if (!origin || !origin.startsWith("https://")) return;
    frm.add_custom_button(__("Manage website password"), () => {
      window.open(
        `${origin}/admin/accounts/${accountId}/password`,
        "_blank",
        "noopener,noreferrer",
      );
    });
  },
});
