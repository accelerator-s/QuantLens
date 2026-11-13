def compute(panel):
    return panel['ret_wd'].rolling(60, min_periods=60).std(ddof=0)
