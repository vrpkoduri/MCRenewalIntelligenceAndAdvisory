# Databricks notebook source
# FU-602 diagnostic probe: confirm PyMC-Marketing fits on this cluster (the PyTensor driver
# crash was C-compilation — disabled via PYTENSOR_FLAGS=cxx=) AND print the exact API shapes
# we need (expected_probability_alive return type/dims, posterior-width access, CLV method) so
# the real driver is wired in one informed pass. Cheap: tiny synthetic data, MCMC cores=1.
# Run on a single-node 16.4 LTS ML cluster; pymc-marketing installed as a cluster-level lib.

# COMMAND ----------
import os
os.environ["PYTENSOR_FLAGS"] = "cxx="  # skip C compilation — the driver-crash fix

import json
import numpy as np
import pandas as pd

out = {}
try:
    import pymc_marketing
    out["pymc_marketing_version"] = pymc_marketing.__version__
    from pymc_marketing.clv import BetaGeoModel, GammaGammaModel

    rng = np.random.default_rng(0)
    n = 60
    df = pd.DataFrame({
        "customer_id": np.arange(n),
        "frequency": rng.integers(0, 5, n).astype(float),
        "recency": rng.uniform(0, 40, n),
        "T": rng.uniform(40, 80, n),
    })
    df["recency"] = np.minimum(df["recency"], df["T"])

    bg = BetaGeoModel(data=df)
    bg.fit(draws=200, tune=200, chains=2, cores=1, progressbar=False)
    out["fit_ok"] = True

    pa = bg.expected_probability_alive(data=df)
    out["p_alive_type"] = type(pa).__name__
    out["p_alive_dims"] = list(getattr(pa, "dims", []))
    out["p_alive_shape"] = list(getattr(pa, "shape", []))
    # posterior mean + width (std across chain/draw)
    try:
        reduce_dims = [d for d in pa.dims if d in ("chain", "draw")]
        out["p_alive_reduce_dims"] = reduce_dims
        mean = pa.mean(dim=reduce_dims) if reduce_dims else pa
        std = pa.std(dim=reduce_dims) if reduce_dims else None
        out["p_alive_mean_sample"] = [float(x) for x in np.asarray(mean).reshape(-1)[:3]]
        out["p_alive_std_sample"] = [float(x) for x in np.asarray(std).reshape(-1)[:3]] if std is not None else None
    except Exception as e:
        out["posterior_reduce_error"] = repr(e)

    # method availability for purchases / CLV
    out["has_expected_purchases"] = hasattr(bg, "expected_purchases")
    out["has_expected_num_purchases"] = hasattr(bg, "expected_num_purchases")
    out["bg_methods"] = [m for m in dir(bg) if m.startswith("expected")]

    gg_df = df[df["frequency"] > 0].copy()
    gg_df["monetary_value"] = rng.uniform(5000, 40000, len(gg_df))
    gg = GammaGammaModel(data=gg_df)
    gg.fit(draws=200, tune=200, chains=2, cores=1, progressbar=False)
    out["gg_fit_ok"] = True
    out["gg_methods"] = [m for m in dir(gg) if m.startswith("expected")]
except Exception as e:
    import traceback
    out["ERROR"] = repr(e)
    out["TRACE"] = traceback.format_exc()[-2000:]

print(json.dumps(out, indent=2, default=str))
dbutils.notebook.exit(json.dumps(out, default=str))  # noqa: F821
