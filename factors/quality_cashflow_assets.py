def compute(panel):
    cashflow = panel["cfo_net"]
    assets = panel["total_assets"]
    return (cashflow / assets).where(assets > 0)
