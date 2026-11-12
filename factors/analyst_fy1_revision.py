def compute(panel):
    forecast = panel["consensus_fy1_mean"]
    coverage = panel["consensus_fy1_coverage"]
    prior = forecast.shift(20)
    valid = (forecast > 0) & (prior > 0) & (coverage >= 2)
    return (forecast / prior - 1).where(valid)
