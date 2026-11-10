"""Application Insights, when a connection string is set; nothing otherwise.

The Azure Monitor OpenTelemetry distro instruments Flask requests, SQLAlchemy queries and outgoing
HTTP calls (Blob Storage, the model API), and sends Python log records, so the worker's "run
finished" and "run failed" records arrive with their run and report ids as custom dimensions.
"""

import logging
import os

log = logging.getLogger("pmr_web")


def configure() -> bool:
    if not os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        return False
    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(logger_name="pmr_web")
    log.setLevel(logging.INFO)
    return True
