# Testing Framework (Master)

The single source for test strategy, types, fixtures, and the per-sprint register (GENERAL_INSTRUCTIONS Rule 4). One consolidated suite, appended to as we build, **run in full after every build piece**. A build piece is not "done" until its tests exist and the whole suite is green.

## Two-tier strategy (driven by local tooling)

Local machine has Python 3.12 + pytest but **no Spark/Java**. So:

- **Tier 1 — local, fast (pure Python).** Constants, field-map integrity, contract↔code consistency, DQ rule *semantics* (`dq/predicates.py`), no-surface guard, fixture validity. Run on every change: `python3 -m pytest -q`.
- **Tier 2 — Databricks (Spark).** Transforms, schema, reconciliation, integration, E2E — run on the workspace against **`_test` mirror schemas** (house convention), or locally if `pyspark`+JDK are installed (uncomment in `requirements-dev.txt`). Marked `@pytest.mark.spark`.

Tier-1 pins the semantics; Tier-2 verifies the Spark implementation mirrors them.

## Test types maintained

| Type | Tier | What it covers | Status |
|---|---|---|---|
| Unit | 1 | Pure functions: DQ predicates, constants, field maps | ✅ S0 |
| Data-quality | 1/2 | 0/blank-as-missing, date-sanity, RTR cross-check produce correct flags | ✅ S0 (tier-1); tier-2 pending data |
| Data-integrity | 2 | Keys/grain/uniqueness, deal→merchant FK, **no-surface** of `_sf_stored_*` | ◑ guard ✅ (tier-1); table-level pending data |
| Reconciliation | 2 | `count(silver.deals where stage=Funded)` == SF funded count (± explained) | ⏳ needs bronze |
| Scenario | 1/2 | Four validation merchants → expected outcomes | ✅ S0 (static/DQ level) |
| Integration | 2 | bronze→silver via the contract (synthetic bronze fixture) | ⏳ needs Spark/bronze |
| E2E | 2 | Full pipeline on a sample book | ⏳ |
| Regression | 1/2 | Entire accumulated suite re-run every cycle | ✅ ongoing |

(Plus, in later sprints: point-in-time correctness for the Feature Store (S2), model backtest sanity (S6), performance.)

## How to run

```bash
# Tier 1 (local)
python3 -m pytest -q

# Tier 2 (Databricks) — run on the workspace against *_test schemas
python3 -m pytest -q -m spark        # requires pyspark+JDK locally, or run as a Databricks job
```

## Fixtures

- `tests/fixtures/validation_merchants.py` — the four canonical merchants (Starr, One Big Promotion, Tom Snell, Wolf), reused by **all** tiers/types. Synthetic until G1; real records swap in without changing tests.

## Per-sprint test register

### Sprint 0 (current)
Added: `test_constants`, `test_field_maps`, `test_contract_consistency`, `test_dq_predicates`, `test_no_surface_guard`, `test_validation_merchants`.
**Result (2026-05-29): 32 passed, 0 failed (tier-1).**
Pending (tier-2, after bronze lands): silver transform unit/integration on synthetic bronze, reconciliation, table-level no-surface, schema/type assertions.
