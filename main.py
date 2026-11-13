"""polymarket-nvidia-ceo-forecast-01: minute-by-minute quantile forecaster.

Reads every ``*.in`` case file in the input directory (one Polymarket
"Next CEO of NVIDIA?" case: each market's Yes price on a 1-minute grid up
to a checkpoint) and writes ``<id>.out`` holding, for each market and each
of the 240 minutes after the checkpoint, the 5%, 25%, 50%, 75% and 95%
quantiles of that minute's price.

Model (per market, everything estimated from that market's own history):

* Level: the price at the checkpoint. Prediction-market prices are close
  to a martingale at this horizon, and in walk-forward tests on the
  practice cases a smoothed or trend-following centre scored worse.
* Volatility: the standard deviation of the market's 1-minute price
  changes, winsorised at +/-0.02 so the isolated one-minute spikes this
  event produces (a print that jumps tens of points and reverts the next
  minute) cannot blow the estimate up. Two windows are blended --
  0.7 * (last 120 minutes) + 0.3 * (last 720 minutes) -- so the estimate
  follows the current regime but is not hostage to a single quiet or
  wild hour, then scaled by 0.8: the raw deviation overstates the
  pinball-optimal spread because most minutes do not move at all.
* Shape: offsets are ``z * sigma * sqrt(h)`` for horizon h, like a
  random walk, with z = (-1.644854, -0.5, 0, 0.5, 1.644854). The
  outer pair is the normal 5%/95% quantile; the inner pair is pulled in
  from the normal 0.674 to 0.5 because the empirical 25%/75% quantiles
  of these step-like price paths sit closer to the centre than a
  Gaussian's do. A light mean-reversion term pulls the centre 20% of
  the way toward the median of the last 360 minutes, phased in with a
  240-minute time constant: over hours, prices drift back toward their
  recent range more than a pure random walk centred on the last tick
  does (it also softens a checkpoint that happens to land on a spike).
  Every value is clamped to [0, 1], rounded to 6 decimals and made
  non-decreasing across the row.

Python 3 standard library only, no network, fully deterministic: the
same case always produces byte-identical output. A full six-case
practice run takes about 0.3 seconds in total, far inside the
8-second-per-case cap.

Local replica scoring on the five practice cases whose answers are
contained in the kit (p00-p04, scored against the following case's
prices): this model 0.302 mean pinball loss vs 0.393 for the house
persistence baseline on the same cases (the baseline's published
six-case practice loss is 0.363589).
"""

import json
import math
import os
from pathlib import Path

QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.644854, -0.5, 0.0, 0.5, 1.644854]
CLIP = 0.02
SCALE = 0.8
REVERT = 0.2
REVERT_WINDOW = 360
REVERT_TAU = 240.0
SIGMA_MIN = 0.0003
SIGMA_MAX = 0.015
FALLBACK_SIGMA = 0.0025


def clipped_sd(prices, window):
    """Std-dev of the last ``window`` minutes of 1-minute changes.

    Changes are winsorised at +/-CLIP before the deviation is taken.
    Falls back to the house baseline's sigma when there is too little
    history to estimate anything.
    """
    seg = prices[-window:] if window < len(prices) else prices
    if len(seg) < 3:
        return FALLBACK_SIGMA
    diffs = [max(-CLIP, min(CLIP, seg[i] - seg[i - 1])) for i in range(1, len(seg))]
    mean = math.fsum(diffs) / len(diffs)
    var = math.fsum((d - mean) ** 2 for d in diffs) / len(diffs)
    return math.sqrt(var)


def market_sigma(prices):
    """Blended, scaled per-market volatility per square-root minute."""
    sigma = SCALE * (0.7 * clipped_sd(prices, 120) + 0.3 * clipped_sd(prices, 720))
    return max(SIGMA_MIN, min(SIGMA_MAX, sigma))


def forecast_market(prices, horizon):
    """``horizon`` rows of the five quantiles for one market."""
    if not prices:
        raise ValueError("market has no prices")
    x = prices[-1]
    sigma = market_sigma(prices)
    recent = sorted(prices[-REVERT_WINDOW:])
    median = recent[len(recent) // 2]
    rows = []
    for h in range(1, horizon + 1):
        width = sigma * math.sqrt(h)
        centre = x + (median - x) * REVERT * (1.0 - math.exp(-h / REVERT_TAU))
        row = []
        prev = 0.0
        for z in Z:
            value = round(min(1.0, max(0.0, centre + z * width)) * 1e6) / 1e6 + 0.0
            if value < prev:
                value = prev
            row.append(value)
            prev = value
        rows.append(row)
    return rows


def main():
    input_dir = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
    output_dir = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    inputs = sorted(input_dir.glob("*.in"))
    for path in inputs:
        case = json.loads(path.read_text(encoding="utf-8"))
        if list(case["quantiles"]) != QUANTILES:
            raise ValueError(f"{path.name} asks for quantiles {case['quantiles']}")
        horizon = int(case["horizon_minutes"])
        forecasts = [
            {"id": m["id"], "q": forecast_market(m["prices"], horizon)}
            for m in case["markets"]
        ]
        text = json.dumps({"forecasts": forecasts}, separators=(",", ":")) + "\n"
        (output_dir / (path.stem + ".out")).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
