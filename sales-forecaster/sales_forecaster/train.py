"""Backtest, fit on all history and save: python -m sales_forecaster.train"""

import time
from pathlib import Path

from sales_forecaster.data import generate
from sales_forecaster.model import backtest, fit, save


def main() -> None:
    t0 = time.time()
    data = generate()
    print(
        f"{len(data):,} rows, {data['date'].min().date()} to {data['date'].max().date()}, "
        f"revenue EUR {data['revenue'].sum() / 1e6:.1f}M"
    )
    metrics, _ = backtest(data)
    print(f"Backtest: {metrics['folds']} folds x {metrics['horizon']} days, WAPE (lower is better)")
    for name, v in metrics["wape"].items():
        print(f"  {name:<32} {v:.1%}")
    base = metrics["wape"]["Same weekday, last 4 weeks"]
    model = metrics["wape"]["Gradient boosting (this model)"]
    metrics["error_reduction_vs_naive"] = 1 - model / base
    print(
        f"Weekly totals WAPE: model {metrics['weekly_wape']['model']:.1%} "
        f"vs naive {metrics['weekly_wape']['naive']:.1%}"
    )
    print(f"Error cut vs naive: {metrics['error_reduction_vs_naive']:.0%}")
    print(
        f"P10-P90 coverage {metrics['coverage_p10_p90_raw']:.0%} raw, band widened x{metrics['band_widen']} "
        f"to reach 80%; bias {metrics['bias']:+.1%}"
    )
    models = fit(data)
    models["widen"] = metrics["band_widen"]
    save(models, metrics, Path("models"))
    print(f"Saved models/ in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
