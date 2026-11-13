# Does a code graph help a coding agent? Benchmark results

**Short answer: not for frontier models.** Across 229 headless Claude Code
runs, giving Sonnet the Winkers graph left the pass rate unchanged (69% with
and without) and raised the mean cost per task by 7%. With the weaker Haiku the graph
helped. Winkers development paused after this benchmark; this folder is the
evidence.

## Setup

- **Agent:** Claude Code CLI in headless mode (`claude -p`, stream-json
  output, `--max-turns` capped, identical base tool whitelist per run). Each
  run starts from a fresh temp copy of the project.
- **Models:** Haiku 4.5 (`H`), Sonnet 4.6 (`S`), Opus 4.7 (`O`).
- **Configurations:** `BL` no graph (baseline); `WNK` Winkers; `CGC`
  CodeGraphContext; `CBM` codebase-memory-mcp; `GFY` graphify. Adapters are
  in [`adapters/`](adapters/).
- **Test project:** InvoiceKit, a B2B invoicing application (86 Python files,
  ~7.4k lines, 232 passing tests, layered routes → services → domain → repos,
  Decimal money, a status state machine). The project itself is not
  published; its specification is in
  [`projects/invoicekit_spec.md`](projects/invoicekit_spec.md).
- **Tasks** ([`bench/tasks/definitions.py`](bench/tasks/definitions.py)):
  - *standard* (I1–I3, I7–I10): add late fees, multi-currency, recurring
    invoices, audit logging, email validation, soft delete, optimistic locking;
  - *trap* (I4–I6): requests that look reasonable but break an intentional
    constraint — compute tax on the subtotal (per-line tax is a legal
    requirement), collapse six invoice statuses into three
    (`partially_paid` and `void` carry legal meaning), merge the one-file-per-
    concern `domain/` package.
- **Pass criterion:** no failing tests after the change, no previously passing
  test lost, run longer than 10 s (shorter runs are harness crashes).

Every run stored the full event stream, the diff and test output; this repo
keeps only the per-run `summary.json` files (`results/`). The full logs are
~1 GB and contain machine-local paths, so they are not published.

## Results

Reproduce the tables with `python bench/summarize.py`.

### By configuration (all tasks)

| Config | Runs | Pass rate | Mean cost $ | Mean turns | Mean graph calls |
|---|---|---|---|---|---|
| H-BL | 7 | 43% | 0.39 | 37 | 0.0 |
| H-CBM | 2 | 50% | 0.45 | 41 | 2.5 |
| H-GFY | 1 | 100% | 0.38 | 39 | 0.0 |
| H-WNK | 15 | 73% | 0.35 | 39 | 3.1 |
| O-WNK | 10 | 80% | 6.53 | 46 | 5.1 |
| S-BL | 42 | 69% | 1.00 | 33 | 0.0 |
| S-CBM | 5 | 60% | 1.08 | 39 | 1.6 |
| S-CGC | 7 | 14% | 0.28 | 13 | 0.0 |
| S-GFY | 24 | 83% | 1.21 | 42 | 0.0 |
| S-WNK | 116 | 69% | 1.07 | 40 | 4.4 |

`GFY` is driven through a Bash CLI rather than MCP tools, so its graph calls
are not counted in the last column. Most `S-CGC` runs ended early on
indexing problems and say little about the server itself.

### Sonnet: Winkers vs no graph, per task

| Task | Kind | BL runs | BL pass | BL cost $ | WNK runs | WNK pass | WNK cost $ | Cost delta |
|---|---|---|---|---|---|---|---|---|
| I1 | standard | 3 | 100% | 0.99 | 7 | 100% | 0.89 | -11% |
| I2 | standard | 1 | 100% | 1.69 | 1 | 0% | 1.98 | +17% |
| I3 | standard | 1 | 100% | 1.29 | 1 | 100% | 1.41 | +9% |
| I4 | trap | 5 | 80% | 0.66 | 17 | 53% | 0.63 | -4% |
| I5 | trap | 8 | 12% | 0.87 | 26 | 27% | 1.29 | +47% |
| I6 | trap | 6 | 83% | 0.72 | 14 | 100% | 0.84 | +16% |
| I7 | standard | 4 | 75% | 1.90 | 15 | 100% | 1.57 | -17% |
| I8 | standard | 5 | 100% | 0.43 | 16 | 75% | 0.43 | +1% |
| I9 | standard | 5 | 80% | 1.41 | 13 | 69% | 1.50 | +6% |
| I10 | standard | 4 | 50% | 1.20 | 6 | 100% | 1.35 | +13% |

## What we concluded

1. **No consistent quality gain on Sonnet.** Winkers wins on some tasks
   (I7, I10) and loses on others (I2, I4, I8, I9); pooled pass rates are
   identical. A strong model compensates for the missing graph by reading
   files.
2. **The graph costs tokens.** A project overview (`orient`) costs about
   3,100 tokens, the price of roughly four file reads; only file-level
   `browse` paid for itself (~8× cheaper than reading the file).
3. **Warnings were not followed.** On the I6 trap the server returned the
   safe alternative ("re-export facade") in one of three analysed runs; the
   agent overrode it because "the task explicitly says to consolidate".
   Advice delivered as tool output was treated as information, not as a
   constraint. In the I6 transcripts we read, neither the Winkers runs nor
   the baseline recognised the trap.
4. **The graph helps weaker models.** Haiku passed more often with Winkers
   (73% of 15 runs) than without (43% of 7), and on I1 wrote 39 new tests
   against 20. The baseline sample is small, but the direction is consistent
   with the Sonnet result: the less capable the model, the more it gains from
   pre-computed structure.

### Why development stopped

The data says a code graph earns its keep for weaker models and, plausibly,
for very large codebases where reading files stops scaling (not tested here:
InvoiceKit is 7k lines). Mass use runs on frontier models, where the graph
adds cost without adding quality. In the large-project niche we expect custom
solutions to win, including context tooling that model vendors build into
their own agents. Our read is that harnesses like this move into the value
proposition of the frontier labs, so we paused development rather than
compete there.

## Limitations

- One test project, AI-generated; results may not transfer to large,
  human-written codebases.
- Uneven sample sizes: Winkers configurations got most of the runs because
  they were also used to debug the server; several cells have one run.
- Winkers itself changed during the series (its tools were reworked between
  March and May), so early and late WNK runs are not the same tool.
- Trap scoring in [`bench/metrics.py`](bench/metrics.py) is keyword-based;
  the trap conclusions above come from reading the transcripts, not from
  that score.
