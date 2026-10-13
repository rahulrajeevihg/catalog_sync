"""Approval must refer to a verified applicant with real, private attached proof."""

import frappe


def validate(doc, method=None):
    previous = doc.get_doc_before_save()
    if str(doc.workflow_state or "").casefold() != "approved trader":
        return
    if previous and str(previous.workflow_state or "").casefold() == "approved trader":
        return
    if not (
        doc.trader_requested
        and doc.email_verified
        and doc.documents_ready
        and doc.organisation_name
        and not doc.website_disabled
        and doc.proof_1
    ):
        frappe.throw("Trader approval requires a verified organisation and checked proof")
    proofs = [doc.get("proof_" + str(index)) for index in range(1, 4)]
    for proof in filter(None, proofs):
        if not proof.startswith("/private/files/") or not frappe.db.exists(
            "File",
            {
                "file_url": proof,
                "is_private": 1,
                "attached_to_doctype": "Catalogue Account",
                "attached_to_name": doc.name,
            },
        ):
            frappe.throw("Approval proof must be a private file attached to this account")
