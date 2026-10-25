# Release targets

| Metric | Target | Checked by |
|---|---|---|
| Intent accuracy (tuning split, offline oracle; final split with --llm) | >= 90% | `python -m app.evals.run` |
| Complaint recall | >= 95% | same |
| Prompt-injection suite (15 attacks x 4 modes) | 100% | same |
| Wrong refund / wrong-customer disclosure | 0 | tests/safety, injection suite |
| Policy table scenarios | 100% pass (51) | same, and publish regression gate |

The final split is touched only for the release check: `python -m app.evals.run --split final --llm`.
