from . import market_data


def live_market(limit=12):
    return market_data.live_market_board(limit=limit)


def live_btc():
    board = market_data.live_market_board(limit=20)
    for asset in board.get("assets", []):
        if (asset.get("symbol") or "").upper() == "BTC":
            return {"ok": True, "asset": asset, "source": board.get("source"), "updated_at": board.get("updated_at")}
    return {"ok": False, "message": "Live data source temporarily unavailable."}
