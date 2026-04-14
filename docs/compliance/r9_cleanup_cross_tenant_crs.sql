-- ============================================================================
-- Round 9 QA — Staging cleanup for cross-tenant change requests
-- CR-260414-49F1465C and CR-260414-8AB70D4D
--
-- Delete child rows first (FK safe order), then parent change_requests row.
-- Uses exact id filters only — no wildcards.
-- ============================================================================

-- 1. CR-260414-49F1465C
DELETE FROM change_request_items     WHERE change_request_id = 'CR-260414-49F1465C';
DELETE FROM change_request_reviews   WHERE change_request_id = 'CR-260414-49F1465C';
DELETE FROM change_request_documents WHERE change_request_id = 'CR-260414-49F1465C';
DELETE FROM change_requests          WHERE id                = 'CR-260414-49F1465C';

-- 2. CR-260414-8AB70D4D
DELETE FROM change_request_items     WHERE change_request_id = 'CR-260414-8AB70D4D';
DELETE FROM change_request_reviews   WHERE change_request_id = 'CR-260414-8AB70D4D';
DELETE FROM change_request_documents WHERE change_request_id = 'CR-260414-8AB70D4D';
DELETE FROM change_requests          WHERE id                = 'CR-260414-8AB70D4D';
