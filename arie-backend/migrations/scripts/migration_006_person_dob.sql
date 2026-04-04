-- Migration 006: Add date_of_birth to directors and ubos tables
-- Required by Agent 1 Verification Matrix check DOC-49A (Passport DOB Match)

ALTER TABLE directors ADD COLUMN IF NOT EXISTS date_of_birth TEXT;
ALTER TABLE ubos ADD COLUMN IF NOT EXISTS date_of_birth TEXT;
