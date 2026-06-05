"""Build the MRI AI/BI (Lakeview) dashboard spec as code (C-021).

A READ-ONLY renderer over PROD `mca_mri.gold` `_current` views (Framework §5.4/§5.8 — the
dashboard is a renderer over the gold table; no writes, no SF). Three pages: Book Health
scoreboard (management), Daily Queue (floor), Merchant 360 (per-merchant drill incl.
predictions).

Widget pattern mirrors a known-good workspace dashboard ("Morgan Cash Leads"): datasets
return GRANULAR rows and the WIDGETS aggregate (COUNT + filters for counters, group-by COUNT
for bars; tables render raw rows). 12-column grid. queries[].name = "main_query".

Run locally (pure Python): writes resources/mri_dashboard.lvdash.json + .dashboard_body.json,
then deploy/update via the Lakeview Dashboards API (see RUNBOOK).
"""

from __future__ import annotations

import json
import os

WAREHOUSE_ID = "526a06bbae2df35b"
PARENT_PATH = "/Workspace/Users/venkat@morgancash.com"
DISPLAY_NAME = "Morgan Cash MRI — Merchant Intelligence"
G = "mca_mri.gold"


def _ds(name, display, sql):
    return {"name": name, "displayName": display, "queryLines": [sql]}


DATASETS = [
    _ds("ds_rung", "Merchant rung (current)",
        f"SELECT merchant_id, coalesce(cast(rung as string),'(gated/unclassified)') AS rung_label, "
        f"lifecycle_state, direction_of_travel, is_unclassified, confidence FROM {G}.merchant_rung_current"),
    _ds("ds_activation", "Merchant activation (current)",
        f"SELECT merchant_id, current_state, active_play FROM {G}.merchant_activation_current"),
    _ds("ds_rung_dist", "Rung distribution",
        f"SELECT coalesce(cast(rung as string),'(gated/unclassified)') AS rung_label, count(*) AS merchants "
        f"FROM {G}.merchant_rung_current GROUP BY 1 ORDER BY 1"),
    _ds("ds_lifecycle_dist", "Lifecycle distribution",
        f"SELECT lifecycle_state, count(*) AS merchants FROM {G}.merchant_rung_current GROUP BY 1 ORDER BY 2 DESC"),
    _ds("ds_play_dist", "Play distribution",
        f"SELECT active_play, count(*) AS merchants FROM {G}.merchant_activation_current GROUP BY 1 ORDER BY 2 DESC"),
    _ds("ds_bookhealth", "Book Health metrics",
        f"SELECT view, metric, dimension_value, value_num, value_pct FROM {G}.book_health "
        f"WHERE report_date=(SELECT max(report_date) FROM {G}.book_health) ORDER BY view, metric, dimension_value"),
    _ds("ds_queue", "Daily queue",
        f"SELECT q.queue_rank, m.business_name, q.merchant_id, q.rung, q.lifecycle_state, "
        f"q.current_state, q.active_play, q.play_sla_due, q.direction_of_travel, q.confidence, "
        f"q.next_tactical_action FROM {G}.daily_queue q LEFT JOIN {G}.merchants m ON q.merchant_id=m.merchant_id "
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
        f"LEFT JOIN {G}.merchants m ON r.merchant_id=m.merchant_id ORDER BY r.confidence ASC LIMIT 500"),
]


def _counter(name, dataset, display, x, y, cond=None, w=2, h=3):
    """COUNT / COUNT_IF over the full (granular) dataset, disaggregated:false. Using COUNT_IF
    (not a filter) means an empty condition still returns one row -> shows 0, not 'No data'."""
    expr = "COUNT(`merchant_id`)" if cond is None else f"COUNT_IF({cond})"
    return {
        "widget": {
            "name": name,
            "queries": [{"name": "main_query", "query": {
                "datasetName": dataset,
                "fields": [{"name": "value", "expression": expr}],
                "disaggregated": False}}],
            "spec": {"version": 2, "frame": {"showTitle": True, "title": display},
                     "widgetType": "counter",
                     "encodings": {"value": {"fieldName": "value", "displayName": display}}},
        },
        "position": {"x": x, "y": y, "width": w, "height": h},
    }


def _bar(name, dataset, group_col, xlabel, ylabel, x, y, w=4, h=6):
    """Group-by bar: x = the category field, y = COUNT(`merchant_id`); the widget groups
    (disaggregated:false)."""
    return {
        "widget": {
            "name": name,
            "queries": [{"name": "main_query", "query": {
                "datasetName": dataset,
                "fields": [{"name": group_col, "expression": f"`{group_col}`"},
                           {"name": "count(merchant_id)", "expression": "COUNT(`merchant_id`)"}],
                "disaggregated": False,
            }}],
            "spec": {"version": 3, "frame": {"showTitle": True, "title": xlabel},
                     "widgetType": "bar",
                     "encodings": {
                         "x": {"fieldName": group_col, "scale": {"type": "categorical"}, "displayName": xlabel},
                         "y": {"fieldName": "count(merchant_id)", "scale": {"type": "quantitative"}, "displayName": ylabel},
                     }},
        },
        "position": {"x": x, "y": y, "width": w, "height": h},
    }


def _table(name, dataset, cols, title, x, y, w=12, h=12):
    return {
        "widget": {
            "name": name,
            "queries": [{"name": "main_query", "query": {
                "datasetName": dataset,
                "fields": [{"name": c, "expression": f"`{c}`"} for c in cols],
                "disaggregated": True,
            }}],
            "spec": {"version": 2, "frame": {"showTitle": True, "title": title},
                     "widgetType": "table",
                     "encodings": {"columns": [{"fieldName": c} for c in cols]}},
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
            _counter("c_total", "ds_rung", "Total merchants", 0, 0),
            _counter("c_active", "ds_rung", "Active", 2, 0, "`lifecycle_state` = 'active'"),
            _counter("c_sliding", "ds_rung", "Sliding", 4, 0, "`direction_of_travel` = 'sliding'"),
            _counter("c_approaching", "ds_activation", "Approaching", 6, 0, "`current_state` = 'approaching'"),
            _counter("c_defaulted", "ds_rung", "Defaulted", 8, 0, "`lifecycle_state` = 'defaulted'"),
            _counter("c_unclassified", "ds_rung", "Unclassified", 10, 0, "`is_unclassified`"),
            _table("t_rung", "ds_rung_dist", ["rung_label", "merchants"], "Rung distribution", 0, 3, 4, 6),
            _table("t_lifecycle", "ds_lifecycle_dist", ["lifecycle_state", "merchants"], "Lifecycle distribution", 4, 3, 4, 6),
            _table("t_play", "ds_play_dist", ["active_play", "merchants"], "Active play distribution", 8, 3, 4, 6),
            _table("t_bookhealth", "ds_bookhealth", _BH_COLS, "Book Health metrics", 0, 9, 12, 8),
        ],
    },
    {
        "name": "page_daily_queue", "displayName": "Daily Queue",
        "layout": [_table("t_queue", "ds_queue", _QUEUE_COLS, "Daily queue (sliding-first)", 0, 0, 12, 18)],
    },
    {
        "name": "page_merchant_360", "displayName": "Merchant 360",
        "layout": [_table("t_360", "ds_360", _360_COLS, "Merchant 360 (lowest confidence first)", 0, 0, 12, 20)],
    },
]

DASHBOARD = {"datasets": DATASETS, "pages": PAGES}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    spec_path = os.path.join(here, "mri_dashboard.lvdash.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(DASHBOARD, f, indent=2)
    body = {"display_name": DISPLAY_NAME, "warehouse_id": WAREHOUSE_ID,
            "parent_path": PARENT_PATH, "serialized_dashboard": json.dumps(DASHBOARD)}
    with open(os.path.join(os.path.dirname(here), ".dashboard_body.json"), "w", encoding="utf-8") as f:
        json.dump(body, f)
    print(f"wrote {spec_path}")


if __name__ == "__main__":
    main()
