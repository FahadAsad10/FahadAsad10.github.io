"""Browser entrypoint for the in-browser (stlite / Pyodide) demo.

Full training (5-fold CV over three models) takes a while single-threaded in
WebAssembly, so the demo ships metrics.json from a real `python -m churn_radar.train`
run and only refits the winning pipeline on the same split before starting app.py.
"""

import json
import runpy
from pathlib import Path

import joblib
from sklearn.model_selection import train_test_split

from churn_radar.data import TARGET, generate
from churn_radar.model import FEATURES, build_pipeline, candidates

MODEL_DIR = Path("models")

if not (MODEL_DIR / "model.joblib").exists():
    metrics = json.loads((MODEL_DIR / "metrics.json").read_text())
    df = generate()
    X_train, _, y_train, _ = train_test_split(
        df[FEATURES], df[TARGET], test_size=0.2, stratify=df[TARGET], random_state=42
    )
    pipe = build_pipeline(candidates()[metrics["best_model"]]).fit(X_train, y_train)
    joblib.dump(pipe, MODEL_DIR / "model.joblib")

runpy.run_path("app.py", run_name="__main__")
