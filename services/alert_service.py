from . import portfolio_service


def create_alert(user_id, alert_type, symbol, target_value, condition, channel="in_app"):
    return portfolio_service.create_price_alert(user_id, alert_type, symbol, target_value, condition, channel)


def list_alerts(user_id):
    return {"ok": True, "alerts": portfolio_service.get_alerts(user_id)}
