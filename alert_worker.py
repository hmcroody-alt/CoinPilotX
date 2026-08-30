"""Standalone CoinPlotXAI alert worker.

Railway worker command:
    python alert_worker.py

The web app can boot without this worker, but alerts become automatic only when
this process is running.
"""

from __future__ import annotations

import logging
import os
import signal
import time

from services import alert_engine, auto_signals_service, live_market_service, market_observations
from services import pulse_briefings
from services.sentinel import runtime as sentinel_runtime


RUNNING = True

#: Matches the board size ``live_market_service.get_crypto_quote`` asks for, so
#: sampling shares that call's 45s cache entry instead of adding a second
#: provider request per cycle.
SAMPLE_BOARD_LIMIT = 80


def _sample_market():
    """Record one observation per symbol. This is the series' only writer.

    Runs before the alert sweep so a window evaluated this cycle includes the
    reading this cycle took. Failures are logged and swallowed: every rule that
    does not use a window works perfectly well without the series, and a
    provider blip must not stop the sweep.
    """
    try:
        board = live_market_service.get_crypto_market(limit=SAMPLE_BOARD_LIMIT)
        return market_observations.record_board(board)
    except Exception as exc:
        logging.exception("Market observation sampling failed: %s", exc)
        return {"ok": False, "recorded": 0, "reason": str(exc)}


def _handle_stop(_signum, _frame):
    global RUNNING
    RUNNING = False


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    interval = max(15, int(os.getenv("ALERT_WORKER_INTERVAL_SECONDS", "45")))
    limit = max(1, int(os.getenv("ALERT_WORKER_BATCH_LIMIT", "500")))
    try:
        import bot

        bot.init_db()
    except Exception as exc:
        logging.exception("Alert worker database initialization failed: %s", exc)
        alert_engine.record_worker_heartbeat("alert_worker", 0, 0, 1, f"init_db failed: {exc}")
    # One-shot embedding provider probe. Inert unless UNDX_EMBEDDING_PROBE is set,
    # and wrapped again here because a diagnostic that could stop the worker from
    # booting would be worse than the uncertainty it exists to remove.
    try:
        from services import undx_embedding_diagnostic

        undx_embedding_diagnostic.run_startup_probe_if_enabled()
    except Exception:
        logging.exception("UNDX embedding probe hook failed; worker start unaffected.")
    logging.info("CoinPlotXAI alert worker started interval=%s limit=%s", interval, limit)
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    while RUNNING:
        try:
            auto_result = auto_signals_service.process_enabled_users(limit=200)
            sample = _sample_market()
            result = alert_engine.evaluate_all_active_alerts(limit=limit, worker_name="alert_worker")
            sentinel_results = sentinel_runtime.run_scheduled_ingestion()
            # Pulse Briefings tick: an evaluation window, never a mandatory
            # send. Isolated so a briefing fault can never break the alert
            # sweep, gated server-side by BRIEFINGS_DISABLED, and delivery-gated
            # independently by BRIEFING_SHADOW_MODE.
            try:
                briefing_result = pulse_briefings.run_scheduled_cycle(
                    limit=int(os.getenv("BRIEFING_CYCLE_BATCH_LIMIT", "50"))
                )
            except Exception:
                logging.exception("Briefing cycle failed; alert sweep unaffected.")
                briefing_result = {"ok": False, "processed": 0}
            logging.info(
                "Alert worker cycle auto_users=%s auto_rules=%s sampled=%s checked=%s triggered=%s errors=%s latency_ms=%s sentinel_runs=%s",
                auto_result.get("checked_users"),
                auto_result.get("maintained_rules"),
                sample.get("recorded"),
                result.get("checked_count"),
                result.get("triggered_count"),
                result.get("error_count"),
                result.get("latency_ms"),
                len(sentinel_results),
            )
            logging.info(
                "Briefing tick status=%s shadow=%s processed=%s sent=%s suppressed=%s "
                "by_rules=%s by_dedupe=%s by_quiet_hours=%s by_shadow=%s failed=%s",
                briefing_result.get("status", "active"), briefing_result.get("shadow"),
                briefing_result.get("processed"), briefing_result.get("sent"),
                briefing_result.get("suppressed"),
                briefing_result.get("suppressed_by_rules"),
                briefing_result.get("suppressed_by_dedupe"),
                briefing_result.get("suppressed_by_quiet_hours"),
                briefing_result.get("suppressed_by_shadow"),
                briefing_result.get("failed"),
            )
        except Exception as exc:
            logging.exception("Alert worker cycle failed: %s", exc)
            try:
                alert_engine.record_worker_heartbeat("alert_worker", 0, 0, 1, str(exc))
            except Exception:
                logging.exception("Alert worker heartbeat failed after cycle error.")
        for _ in range(interval):
            if not RUNNING:
                break
            time.sleep(1)
    logging.info("CoinPlotXAI alert worker stopped.")


if __name__ == "__main__":
    main()
