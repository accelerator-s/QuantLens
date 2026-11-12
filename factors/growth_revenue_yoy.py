def compute(panel):
    revenue = panel["revenue"]
    prior = revenue.shift(252)
    valid = (revenue > 0) & (prior > 0)
    return (revenue / prior - 1).where(valid)
