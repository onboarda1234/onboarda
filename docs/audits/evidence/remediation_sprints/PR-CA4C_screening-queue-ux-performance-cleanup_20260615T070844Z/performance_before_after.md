# PR-CA4C Performance Evidence

## Controlled Local Payload Fixture

Fixture:

- 50 applications.
- 100 queue subject rows with CA-like provider evidence.
- First page requested with `limit=50`.

Raw evidence:

- `runtime_json/local_queue_payload_perf.json`

## Results

| Mode | Elapsed ms | Payload bytes | Returned rows | Total rows | Heavy provider evidence in list | Screening evidence items in list |
|---|---:|---:|---:|---:|---|---|
| Full evidence mode | 30.29 | 422208 | 50 | 100 | yes | yes |
| Summary mode | 29.22 | 318596 | 50 | 100 | no | no |

Payload reduction:

```text
24.54%
```

## Interpretation

PR-CA4C reduces default queue payload and browser render weight by removing full provider evidence and full screening evidence items from the default list response. Full evidence remains available through explicit detail loading.

The backend still builds and enriches recent application rows before filtering/pagination. If staging or production datasets show backend compute time, a later optimization should move more search/count work into scoped SQL or indexed summary tables. That deeper change is outside this focused PR.

