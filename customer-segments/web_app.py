"""Browser entrypoint for the in-browser (stlite / Pyodide) demo.

Ships metrics.json from a real `python -m customer_segments.train` run (silhouette
search and out-of-time CLV test) and only fits the final value models in the
browser before starting app.py.
"""

import json
import runpy
from pathlib import Path

import joblib
import pandas as pd

from customer_segments.data import END, generate
from customer_segments.model import WINDOW, fit_clv

MODEL_DIR = Path("models")

if not (MODEL_DIR / "models.joblib").exists():
    json.loads((MODEL_DIR / "metrics.json").read_text())  # fail early if metrics are missing
    customers, orders = generate()
    joblib.dump(fit_clv(orders, customers, END - pd.Timedelta(days=WINDOW)), MODEL_DIR / "models.joblib")

runpy.run_path("app.py", run_name="__main__")
