from . import db


def database_snapshot():
    health = db.health_check()
    return {
        "ok": health.get("connected", False),
        "db_engine": health.get("db_engine"),
        "database_name": health.get("database_name"),
        "tables_detected": health.get("tables_detected", []),
        "latency_ms": health.get("latency_ms"),
        "error": health.get("error", ""),
    }
