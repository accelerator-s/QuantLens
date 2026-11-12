def compute(panel):
    price = panel["adj_close_wd"]
    return -(price / price.shift(5) - 1)
