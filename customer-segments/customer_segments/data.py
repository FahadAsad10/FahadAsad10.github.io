"""Synthetic but realistic e-commerce order history.

Customers join over two years and behave like the groups found in real online
shops: a few loyal high spenders, steady regulars, occasional buyers, bargain
hunters who buy once or twice in a sale, and customers who were active and then
quietly stopped. Each customer has an acquisition channel and a country. Order
timing follows a Poisson process per customer, and order values are lognormal.
Generating the data keeps the project self-contained, with no private dataset.
"""

import numpy as np
import pandas as pd

START, END = pd.Timestamp("2024-01-01"), pd.Timestamp("2025-12-31")
CHANNELS = ["Organic search", "Paid social", "Email", "Referral", "Marketplace"]
COUNTRIES = ["Italy", "Germany", "France", "Spain", "Netherlands"]

# share, mean days between orders, mean order value (EUR), chance of stopping early, channel weights
ARCHETYPES = {
    "loyal_vip": (0.06, 24, 135, 0.05, [0.30, 0.10, 0.30, 0.25, 0.05]),
    "regular": (0.20, 50, 72, 0.15, [0.30, 0.20, 0.25, 0.15, 0.10]),
    "occasional": (0.30, 130, 58, 0.25, [0.30, 0.25, 0.15, 0.10, 0.20]),
    "bargain": (0.24, 400, 34, 0.60, [0.15, 0.45, 0.05, 0.05, 0.30]),
    "fading": (0.20, 45, 65, 1.00, [0.25, 0.30, 0.15, 0.10, 0.20]),
}


def generate(n_customers: int = 5000, seed: int = 11, end: pd.Timestamp = END) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (customers, orders). Orders have order_id, customer_id, date, amount, items, discounted."""
    rng = np.random.default_rng(seed)
    names = list(ARCHETYPES)
    shares = np.array([ARCHETYPES[a][0] for a in names])
    archetype = rng.choice(names, n_customers, p=shares / shares.sum())
    span = (end - START).days
    # More customers join later, as the shop grows
    joined = START + pd.to_timedelta((rng.beta(1.3, 1.0, n_customers) * span * 0.97).astype(int), unit="D")
    channel = np.array([rng.choice(CHANNELS, p=ARCHETYPES[a][4]) for a in archetype])
    country = rng.choice(COUNTRIES, n_customers, p=[0.40, 0.20, 0.17, 0.13, 0.10])

    rows = []
    for cid in range(n_customers):
        a = archetype[cid]
        _, gap, aov, p_stop, _ = ARCHETYPES[a]
        gap_i = gap * rng.lognormal(0, 0.35)
        aov_i = aov * rng.lognormal(0, 0.3)
        start = joined[cid]
        stop = end
        if rng.random() < p_stop:
            life = rng.gamma(2.0, 90 if a == "fading" else 120)
            stop = min(end, start + pd.Timedelta(days=int(life)))
        day = start
        first = True
        while day <= stop:
            amount = round(float(aov_i * rng.lognormal(0, 0.45)), 2)
            items = int(1 + rng.poisson(max(amount / 30 - 1, 0.2)))
            discounted = int(rng.random() < (0.75 if a == "bargain" else 0.2))
            rows.append((cid, day, amount, items, discounted))
            if a == "bargain" and not first and rng.random() < 0.5:
                break
            first = False
            if a == "fading":
                gap_i *= 1.3  # engagement decays: each gap is longer than the last, until they stop
            day = day + pd.Timedelta(days=max(1, int(rng.exponential(gap_i))))

    orders = pd.DataFrame(rows, columns=["customer_id", "date", "amount", "items", "discounted"])
    orders = orders.sort_values(["date", "customer_id"]).reset_index(drop=True)
    orders.insert(0, "order_id", np.arange(1, len(orders) + 1))
    customers = pd.DataFrame(
        {"customer_id": np.arange(n_customers), "joined": joined, "channel": channel, "country": country}
    )
    return customers, orders
