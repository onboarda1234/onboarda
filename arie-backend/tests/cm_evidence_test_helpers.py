import secrets


def attach_verified_cm_evidence(cm, db, request_id, *, doc_type="supporting_document"):
    """Attach generic verified evidence for matrix-gated CM test fixtures.

    Production code should fail closed when evidence is absent. Existing
    lifecycle/regression tests that are not about evidence gates use this helper
    to make the new approval gate explicit.
    """
    detail = cm.get_change_request_detail(db, request_id)
    if not detail:
        return
    app_id = detail.get("application_id")
    for item in detail.get("items") or []:
        if not cm._canonical_change_key(item):
            continue
        linked_doc_id = f"test-cm-doc-{secrets.token_hex(6)}"
        db.execute(
            """INSERT INTO documents
               (id, application_id, doc_type, doc_name, file_path, verification_status, review_status)
               VALUES (?, ?, ?, ?, ?, 'verified', 'pending')""",
            (linked_doc_id, app_id, doc_type, f"{doc_type}.pdf", f"/tmp/{linked_doc_id}.pdf"),
        )
        db.execute(
            """INSERT INTO change_request_documents
               (id, request_id, item_id, doc_name, doc_type, file_path, s3_key, uploaded_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"crdoc-{secrets.token_hex(6)}",
                request_id,
                item.get("id"),
                f"{doc_type}.pdf",
                doc_type,
                f"/tmp/{linked_doc_id}.pdf",
                f"document:{linked_doc_id}",
                "test-helper",
            ),
        )
    db.commit()
