# Shared Components Catalogue

Reusable libraries with stable contracts, centralized so they change in one place (GENERAL_INSTRUCTIONS Rule 3). No block reaches into another's internals; data flows via the gold-table contract.

## Built (Sprint 0)

| Component | Path | Contract / purpose |
|---|---|---|
| `constants` | `src/common/constants.py` | Catalog/schema/table names, SF object names, enums (DealType, PaymentFrequency, BalanceSource, Verdict), no-surface set, RTR tolerance, reserved Appendix A/B thresholds. Pure Python. |
| `field_maps` | `src/common/field_maps.py` | SPRINT_0 bronze→silver maps as `FieldSpec` data (deals, field_history) + DQ-derived columns. The rename/typing spec the transform reads. |
| `contract` | `src/common/contract.py` | Loads the authoritative Data Contract xlsx; exposes Deal/Merchant-Gold field→verdict maps for drift tests. |
| `dq.predicates` | `src/common/dq/predicates.py` | Pure-Python DQ semantics: missing-implausible-zero, date-sanity, RTR check. Tier-1 testable; the canonical spec. |
| `dq.rules` | `src/common/dq/rules.py` | Native Spark column expressions mirroring the predicates (no UDFs). |
| `schemas.silver` | `src/common/schemas/silver.py` | Spark schemas for `silver.deals` / `silver.field_history`, derived from `field_maps` (single source). |
| `io.guards` | `src/common/io/guards.py` | No-surface guard — makes CLAUDE.md §2.1 executable (`assert_no_surface`). |

## Reserved homes (later sprints — do NOT implement early)

| Component | Path | Sprint |
|---|---|---|
| Amortization clock (Appendix A) | `src/common/clock/` | S2 |
| Identity resolution (AATM-style) | `src/common/identity/` | S1 |
| Lifecycle gate + rung waterfall (Appendix B) | `src/common/rung/` | S3 |
| Append-only event log emitter | `src/common/eventlog/` | S3 |

## Principles

- **Schema is derived from / validated against the Data Contract xlsx** — never a parallel hand-maintained copy.
- **Thresholds live once** in `constants.Thresholds` (calibration in one place).
- **The four validation merchants are canonical fixtures**, reused everywhere.
- A utility used in ≥2 places is promoted to `src/common/` immediately.
