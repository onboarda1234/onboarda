def test_seed_initial_data_ensures_deterministic_qa_smoke_user(temp_db):
    from db import (
        QA_SMOKE_USER_ID,
        QA_SMOKE_USER_NAME,
        QA_SMOKE_USER_ROLE,
        get_db,
        seed_initial_data,
    )

    db = get_db()
    try:
        seed_initial_data(db)
        row = db.execute(
            "SELECT id, full_name, role, status FROM users WHERE id = ?",
            (QA_SMOKE_USER_ID,),
        ).fetchone()
    finally:
        db.close()

    assert row is not None
    assert row["full_name"] == QA_SMOKE_USER_NAME
    assert row["role"] == QA_SMOKE_USER_ROLE
    assert row["status"] == "active"
