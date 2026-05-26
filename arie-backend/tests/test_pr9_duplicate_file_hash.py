"""PR9 duplicate detection stored-hash guards."""

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._migration_idempotency_helpers import fresh_migration_db


PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def _seed_application(db, app_id="app_pr9_hash"):
    db.execute(
        """
        INSERT INTO applications (id, ref, company_name, country, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            "ARF-2026-PR9",
            "PR9 Hash Ltd",
            "Mauritius",
            "draft",
            "{}",
        ),
    )


def _insert_doc(
    db,
    *,
    doc_id,
    app_id="app_pr9_hash",
    file_path="missing.pdf",
    file_sha256=None,
    is_current=1,
):
    db.execute(
        """
        INSERT INTO documents (
            id, application_id, doc_type, doc_name, file_path, file_size,
            mime_type, file_sha256, verification_status, is_current, version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            app_id,
            "cert_inc",
            f"{doc_id}.pdf",
            file_path,
            len(PDF_BYTES),
            "application/pdf",
            file_sha256,
            "pending",
            is_current,
            1,
        ),
    )


def test_document_file_hash_schema_is_fresh_and_inline_repaired(tmp_path, monkeypatch):
    import db as db_module

    assert "file_sha256 TEXT" in db_module._get_sqlite_schema()
    assert "file_sha256 TEXT" in db_module._get_postgres_schema()

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        columns = db.execute("PRAGMA table_info(documents)").fetchall()
        assert "file_sha256" in {row["name"] for row in columns}

        indexes = db.execute("PRAGMA index_list(documents)").fetchall()
        assert "idx_documents_application_file_sha256" in {row["name"] for row in indexes}


def test_duplicate_lookup_uses_indexed_stored_hash_for_new_documents(tmp_path, monkeypatch):
    import server

    digest = hashlib.sha256(PDF_BYTES).hexdigest()
    with fresh_migration_db(tmp_path, monkeypatch) as db:
        _seed_application(db)
        _insert_doc(db, doc_id="doc_current", file_sha256=digest)
        _insert_doc(db, doc_id="doc_duplicate", file_path="missing-peer.pdf", file_sha256=digest)
        db.commit()

        def fail_if_legacy_fallback_used(_file_path):
            raise AssertionError("stored-hash duplicate lookup should not read peer files")

        monkeypatch.setattr(server, "_hash_legacy_document_file", fail_if_legacy_fallback_used)

        existing_hashes = server._duplicate_hashes_for_document(
            db,
            application_id="app_pr9_hash",
            document_id="doc_current",
            file_sha256=digest,
        )

    assert existing_hashes == [digest]


def test_duplicate_lookup_keeps_controlled_legacy_fallback(tmp_path, monkeypatch):
    import server

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    legacy_file = upload_dir / "legacy.pdf"
    legacy_file.write_bytes(PDF_BYTES)
    digest = hashlib.sha256(PDF_BYTES).hexdigest()

    monkeypatch.setattr(server, "UPLOAD_DIR", str(upload_dir))

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        _seed_application(db)
        _insert_doc(db, doc_id="doc_current", file_sha256=digest)
        _insert_doc(db, doc_id="doc_legacy", file_path=legacy_file.name, file_sha256=None)
        db.commit()

        existing_hashes = server._duplicate_hashes_for_document(
            db,
            application_id="app_pr9_hash",
            document_id="doc_current",
            file_sha256=digest,
        )

    assert existing_hashes == [digest]


def test_gate_03_uses_supplied_file_hash_without_rereading_current_file():
    from document_verification import run_gate_checks

    digest = hashlib.sha256(PDF_BYTES).hexdigest()
    results = run_gate_checks(
        "/path/that/does/not/exist.pdf",
        len(PDF_BYTES),
        "application/pdf",
        [digest],
        file_sha256=digest,
    )

    duplicate_gate = next(result for result in results if result["id"] == "GATE-03")
    assert duplicate_gate["result"] == "warn"
    assert "already been uploaded" in duplicate_gate["message"]
