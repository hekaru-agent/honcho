import asyncio
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import uvloop
from prometheus_client import REGISTRY, generate_latest

from src.config import settings
from src.telemetry import initialize_telemetry_async, shutdown_telemetry

from .queue_manager import main

logger = logging.getLogger(__name__)


class PrometheusHealthHandler(BaseHTTPRequestHandler):
    """HTTP handler serving both /health (JSON) and /metrics (Prometheus)."""

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        else:
            # Fall through to Prometheus metrics for all other paths
            output = generate_latest(REGISTRY)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(output)

    def log_message(self, format, *args):
        # Suppress default HTTP logging to avoid noise
        return


def start_metrics_server() -> None:
    """Start HTTP server on port 9090 serving /health and /metrics."""
    server = HTTPServer(("127.0.0.1", 9090), PrometheusHealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Prometheus+Health HTTP server started on port 9090")


def setup_logging():
    """
    Configure logging for the deriver process.
    """
    # Get log level from environment or settings
    log_level_str = os.getenv("LOG_LEVEL", settings.LOG_LEVEL).upper()

    log_levels = {
        "CRITICAL": logging.CRITICAL,  # 50
        "ERROR": logging.ERROR,  # 40
        "WARNING": logging.WARNING,  # 30
        "INFO": logging.INFO,  # 20
        "DEBUG": logging.DEBUG,  # 10
        "NOTSET": logging.NOTSET,  # 0
    }

    log_level = log_levels.get(log_level_str, logging.INFO)

    # Configure logging
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Disable SQLAlchemy engine logging unless explicitly enabled
    if not settings.DB.SQL_DEBUG:
        logging.getLogger("sqlalchemy.engine.Engine").disabled = True

    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai._base_client").setLevel(logging.WARNING)
    logging.getLogger("groq._base_client").setLevel(logging.WARNING)


async def run_deriver():
    """Run the deriver with proper telemetry lifecycle management."""
    # Initialize async telemetry (CloudEvents emitter)
    await initialize_telemetry_async()
    try:
        await main()
    finally:
        # Shutdown telemetry (flush CloudEvents buffer)
        await shutdown_telemetry()


if __name__ == "__main__":
    # Setup logging before starting the main loop
    setup_logging()
    logger.info("Starting deriver queue processor")

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    try:
        # Start Prometheus metrics server if enabled
        if settings.METRICS.ENABLED:
            start_metrics_server()

        logger.info("Running main loop")
        asyncio.run(run_deriver())
    except KeyboardInterrupt:
        logger.info("Shutdown initiated via KeyboardInterrupt")
    except Exception as e:
        logger.exception("Error in main process: %s", e)
    finally:
        logger.info("Deriver process exiting")
