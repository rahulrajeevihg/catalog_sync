// Passwords remain in the website authentication service, never in ERP fields.
frappe.ui.form.on("Catalogue Account", {
  refresh(frm) {
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
