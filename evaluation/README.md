# ContribCompass accuracy benchmark

Run the benchmark from the repository root:

```bash
python -m evaluation.runners.run_evaluation
```

This fetches each commit-pinned public repository tree and a bounded set of important files, then applies the same deterministic analyzer, evidence normalization, retrieval metrics, and repository-specificity checks used by the application. GitHub authentication is optional; `GITHUB_TOKEN` increases the API quota. Use `--with-model` to generate/evaluate guides with the configured local Ollama or vLLM provider. `--model qwen3:latest` selects a local model tag.

Reports are written to `reports/current.json`, `reports/current.csv`, and `reports/current.html`. Each run compares aggregate metrics with the preceding JSON report and prints before/after deltas. The JSON contains per-repository facts, pins, latency, retrieval details, annotation status, failure details, and dataset digest.

## Dataset status

`datasets/repositories.json` contains 20 public repositories covering Python, JavaScript, TypeScript, React, FastAPI, Node, Rust, Go, libraries, CLIs, and small through large projects. Every entry is pinned to a 40-character commit SHA. `golden_facts.json` was seeded from GitHub metadata and the corresponding pinned repository tree. Its `auto_seeded_needs_review` status is deliberate: these labels are useful for smoke/regression comparisons but are not yet a manually audited gold set. Do not interpret their aggregate score as a validated accuracy claim.

`golden_issues.json` currently has no human-labeled relevant issue IDs. `golden_answers.json` is an empty scaffold for reviewed reference answers. Consequently issue precision/recall and model answer correctness are unavailable until these labels are curated. Generation metrics are also unavailable in deterministic mode. The runner reports unavailable values as `null` and keeps `production_ready` false whenever its hard gates cannot be measured.

## Reproducibility and regression

Repository source snapshots are reproducible by commit SHA. GitHub issue state is mutable and must be saved as a reviewed snapshot before it is suitable for regression evaluation. Reports include the dataset SHA-256, model tag, evaluation version, and source commit. Run the benchmark before and after prompt, parser, retrieval, ranking, or model changes; inspect each metric and the per-repository errors, not only the weighted score.

The current runner does not claim token counts, peak memory, independent human answer relevance/correctness, or issue-ranking accuracy without their required annotations and instrumentation. Review or replace auto-seeded labels before using the score for release decisions.
