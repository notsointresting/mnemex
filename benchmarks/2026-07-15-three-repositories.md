# Context-Delivery Microbenchmark: 2026-07-15

## Method

Each repository was shallow-cloned at the commit recorded below and measured
with this command on Windows using Python 3.14:

```text
python -m mnemex benchmark <repository> --db :memory:
```

The current CLI benchmark indexes Python files, uses at most the first 20
Python files for the raw-file baseline, creates up to ten synthetic anchored
decisions from indexed functions, and measures one session brief plus five JIT
contexts. These are measured workload-specific context-delivery microbenchmark
figures, not an agent-quality evaluation or a universal token-savings claim.

## Results

| Repository | Commit | Python files | Nodes | Edges | Index time | Baseline tokens | JIT tokens | Savings | JIT latency |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| [psf/requests](https://github.com/psf/requests) | `f361ead047be5cb873174218582f7d8b9fcd9f49` | 37 | 844 | 244 | 1.44 s | 58,330 | 316 | 99.5% | 2.9 ms |
| [pallets/click](https://github.com/pallets/click) | `b67832c2167e5b0ff6764a8c04a0a9087e697b5a` | 76 | 1,962 | 675 | 3.18 s | 94,661 | 296 | 99.7% | 3.0 ms |
| [fastapi/typer](https://github.com/fastapi/typer) | `3a3bd0f20a417835d4b4505a0bf834620e024cdb` | 634 | 3,046 | 484 | 4.11 s | 23,894 | 527 | 97.8% | 2.7 ms |

## Notes

- `sqlite-vec` availability is optional and does not affect this BM25-only
  benchmark path.
- The benchmark does not call a remote semantic provider.
- Re-run the command against the listed commits to compare future changes.
