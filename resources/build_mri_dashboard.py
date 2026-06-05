"""Build the MRI AI/BI (Lakeview) dashboard spec as code (C-021).

A READ-ONLY renderer over PROD `mca_mri.gold` `_current` views (Framework §5.4/§5.8 — the
dashboard is a renderer over the gold table; no writes, no SF). Three pages: Book Health
scoreboard (management), Daily Queue (floor), Merchant 360 (per-merchant drill incl.
predictions). Run locally (pure Python, no deps): writes
  - resources/mri_dashboard.lvdash.json   (the serialized dashboard — the as-code artifact)
  - .dashboard_body.json                  (the Lakeview create-request body; gitignored/temp)
then deploy with:
  databricks api post /api/2.0/lakeview/dashboards --json @.dashboard_body.json
"""

from __future__ import annotations

import json
import os

WAREHOUSE_ID = "526a06bbae2df35b"  # Starter Warehouse (read-only queries)
PARENT_PATH = "/Workspace/Users/venkat@morgancash.com"
DISPLAY_NAME = "Morgan Cash MRI — Merchant Intelligence"
G = "mca_mri.gold"


def _ds(name, display, sql):
    return {"name": name, "displayName": display, "queryLines": [sql]}


DATASETS = [
    _ds("ds_counts", "Rung counts",
        f"SELECT count(*) AS total_merchants, "
        f"sum(case when lifecycle_state='active' then 1 else 0 end) AS active_merchants, "
        f"sum(case when direction_of_travel='sliding' then 1 else 0 end) AS sliding_merchants, "
        f"sum(case when lifecycle_state='defaulted' then 1 else 0 end) AS defaulted_merchants, "
        f"sum(case when is_unclassified then 1 else 0 end) AS unclassified_merchants "
        f"FROM {G}.merchant_rung_current"),
    _ds("ds_state_counts", "State counts",
        f"SELECT sum(case when current_state='approaching' then 1 else 0 end) AS approaching, "
        f"sum(case when current_state='in-market' then 1 else 0 end) AS in_market "
        f"FROM {G}.merchant_activation_current"),
    _ds("ds_rung_dist", "Rung distribution",
        f"SELECT coalesce(cast(rung as string),'(gated/unclassified)') AS rung_label, count(*) AS n "
        f"FROM {G}.merchant_rung_current GROUP BY 1 ORDER BY 1"),
    _ds("ds_lifecycle_dist", "Lifecycle distribution",
        f"SELECT lifecycle_state, count(*) AS n FROM {G}.merchant_rung_current GROUP BY 1 ORDER BY 2 DESC"),
    _ds("ds_play_dist", "Play distribution",
        f"SELECT active_play, count(*) AS n FROM {G}.merchant_activation_current GROUP BY 1 ORDER BY 2 DESC"),
    _ds("ds_bookhealth", "Book Health metrics",
        f"SELECT view, metric, dimension_value, value_num, value_pct FROM {G}.book_health "
        f"WHERE report_date=(SELECT max(report_date) FROM {G}.book_health) ORDER BY view, metric, dimension_value"),
    _ds("ds_queue", "Daily queue",
        f"SELECT q.queue_rank, m.business_name, q.merchant_id, q.rung, q.lifecycle_state, "
        f"q.current_state, q.active_play, q.play_sla_due, q.direction_of_travel, q.confidence, "
        f"q.next_tactical_action "
        f"FROM {G}.daily_queue q LEFT JOIN {G}.merchants m ON q.merchant_id=m.merchant_id "
        f"ORDER BY q.queue_rank LIMIT 250"),
    _ds("ds_360", "Merchant 360",
        f"SELECT m.business_name, r.merchant_id, r.rung, r.lifecycle_state, r.confidence, "
        f"r.direction_of_travel, r.route, c.est_paydown_pct, c.is_eligible_now, "
        f"c.est_renewal_eligible_date, a.current_state, a.active_play, a.play_sla_due, "
        f"a.next_tactical_action, a.next_strategic_nudge, p.p_alive, p.p_defection, "
        f"p.predicted_next_event_date, p.predicted_clv, p.prediction_confidence, p.insufficient_history "
        f"FROM {G}.merchant_rung_current r "
        f"LEFT JOIN {G}.merchant_clock_current c ON r.merchant_id=c.merchant_id "
        f"LEFT JOIN {G}.merchant_activation_current a ON r.merchant_id=a.merchant_id "
        f"LEFT JOIN {G}.merchant_predictions_current p ON r.merchant_id=p.merchant_id "
        f"LEFT JOIN {G}.merchants m ON r.merchant_id=m.merchant_id "
        f"ORDER BY r.confidence ASC LIMIT 500"),
]


def _counter(name, dataset, field, display, x, y):
    return {
        "widget": {
            "name": name,
            "queries": [{"name": "main", "query": {
                "datasetName": dataset,
                "fields": [{"name": field, "expression": f"`{field}`"}],
                "disaggregated": True,
            }}],
            "spec": {"version": 2, "widgetType": "counter",
                     "encodings": {"value": {"fieldName": field, "displayName": display}}},
        },
        "position": {"x": x, "y": y, "width": 1, "height": 3},
    }


def _bar(name, dataset, xf, yf, xlabel, ylabel, x, y, w=2, h=6):
    return {
        "widget": {
            "name": name,
            "queries": [{"name": "main", "query": {
                "datasetName": dataset,
                "fields": [{"name": xf, "expression": f"`{xf}`"}, {"name": yf, "expression": f"`{yf}`"}],
                "disaggregated": True,
            }}],
            "spec": {"version": 3, "widgetType": "bar",
                     "encodings": {
                         "x": {"fieldName": xf, "scale": {"type": "categorical"}, "displayName": xlabel},
                         "y": {"fieldName": yf, "scale": {"type": "quantitative"}, "displayName": ylabel},
                     }},
        },
        "position": {"x": x, "y": y, "width": w, "height": h},
    }


def _table(name, dataset, cols, x, y, w=6, h=10):
    return {
        "widget": {
            "name": name,
            "queries": [{"name": "main", "query": {
                "datasetName": dataset,
                "fields": [{"name": c, "expression": f"`{c}`"} for c in cols],
                "disaggregated": True,
            }}],
            "spec": {"version": 1, "widgetType": "table",
                     "encodings": {"columns": [{"fieldName": c, "displayName": c} for c in cols]}},
        },
        "position": {"x": x, "y": y, "width": w, "height": h},
    }


_QUEUE_COLS = ["queue_rank", "business_name", "rung", "lifecycle_state", "current_state",
               "active_play", "play_sla_due", "direction_of_travel", "confidence", "next_tactical_action"]
_360_COLS = ["business_name", "rung", "lifecycle_state", "confidence", "direction_of_travel",
             "est_paydown_pct", "is_eligible_now", "est_renewal_eligible_date", "current_state",
             "active_play", "play_sla_due", "next_tactical_action", "p_alive", "p_defection",
             "predicted_next_event_date", "predicted_clv", "prediction_confidence", "insufficient_history"]
_BH_COLS = ["view", "metric", "dimension_value", "value_num", "value_pct"]

PAGES = [
    {
        "name": "page_book_health", "displayName": "Book Health",
        "layout": [
            _counter("c_total", "ds_counts", "total_merchants", "Total merchants", 0, 0),
            _counter("c_active", "ds_counts", "active_merchants", "Active", 1, 0),
            _counter("c_sliding", "ds_counts", "sliding_merchants", "Sliding", 2, 0),
            _counter("c_approaching", "ds_state_counts", "approaching", "Approaching", 3, 0),
            _counter("c_defaulted", "ds_counts", "defaulted_merchants", "Defaulted", 4, 0),
            _counter("c_unclassified", "ds_counts", "unclassified_merchants", "Unclassified", 5, 0),
            _bar("b_rung", "ds_rung_dist", "rung_label", "n", "Rung", "Merchants", 0, 3, 2, 6),
            _bar("b_lifecycle", "ds_lifecycle_dist", "lifecycle_state", "n", "Lifecycle", "Merchants", 2, 3, 2, 6),
            _bar("b_play", "ds_play_dist", "active_play", "n", "Play", "Merchants", 4, 3, 2, 6),
            _table("t_bookhealth", "ds_bookhealth", _BH_COLS, 0, 9, 6, 8),
        ],
    },
    {
        "name": "page_daily_queue", "displayName": "Daily Queue",
        "layout": [_table("t_queue", "ds_queue", _QUEUE_COLS, 0, 0, 6, 16)],
    },
    {
        "name": "page_merchant_360", "displayName": "Merchant 360",
        "layout": [_table("t_360", "ds_360", _360_COLS, 0, 0, 6, 18)],
    },
]

DASHBOARD = {"datasets": DATASETS, "pages": PAGES}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    spec_path = os.path.join(here, "mri_dashboard.lvdash.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(DASHBOARD, f, indent=2)
    body = {
        "display_name": DISPLAY_NAME,
        "warehouse_id": WAREHOUSE_ID,
        "parent_path": PARENT_PATH,
        "serialized_dashboard": json.dumps(DASHBOARD),
    }
    body_path = os.path.join(os.path.dirname(here), ".dashboard_body.json")
    with open(body_path, "w", encoding="utf-8") as f:
        json.dump(body, f)
    print(f"wrote {spec_path}")
    print(f"wrote {body_path}")


if __name__ == "__main__":
    main()
