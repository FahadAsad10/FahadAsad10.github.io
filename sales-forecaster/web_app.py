"""Browser entrypoint for the in-browser (stlite / Pyodide) demo.

The rolling backtest refits the model six times, which is slow single-threaded in
WebAssembly, so the demo ships metrics.json from a real `python -m sales_forecaster.train`
run and only fits the final models on the full history before starting app.py.
"""

import json
import runpy
from pathlib import Path

import joblib

from sales_forecaster.data import generate
from sales_forecaster.model import fit

MODEL_DIR = Path("models")

if not (MODEL_DIR / "models.joblib").exists():
    metrics = json.loads((MODEL_DIR / "metrics.json").read_text())
    models = fit(generate())
    models["widen"] = metrics["band_widen"]
    joblib.dump(models, MODEL_DIR / "models.joblib")

runpy.run_path("app.py", run_name="__main__")
