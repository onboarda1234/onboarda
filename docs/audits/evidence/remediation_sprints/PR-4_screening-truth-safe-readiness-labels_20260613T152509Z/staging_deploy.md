# Staging Deploy

Not available at branch stage.

Required after merge:

- Deploy merged main to staging.
- Confirm `/api/version` `git_sha` equals merged main SHA.
- Confirm `/api/version` `image_tag` equals merged main SHA.
- Save raw redacted response in `runtime_json/`.

Current closure status before merge/staging:

```text
PARTIALLY FIXED - branch implementation and local targeted tests complete; merged-main staging proof pending.
```
