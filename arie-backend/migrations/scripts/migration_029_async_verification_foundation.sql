-- PR6 async verification foundation marker.
--
-- Fresh schemas include verification_jobs directly in db.py schema DDL.
-- Long-lived deployments are repaired by db.py inline migration v2.38
-- because CREATE TABLE IF NOT EXISTS in init_db is not enough on its own
-- once schema_version pre-marks file migrations as covered by init_db.
--
-- Runtime behavior remains synchronous unless FF_ASYNC_VERIFY is explicitly
-- enabled outside this PR.
SELECT 1;
