-- KYC party/document integrity columns are applied by db.py's idempotent
-- cross-dialect startup migration. This ledger marker keeps the file-migration
-- sequence explicit without duplicating PostgreSQL/SQLite-specific ALTER logic.
SELECT 1;
