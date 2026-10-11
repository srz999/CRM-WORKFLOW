"""Optional, fire-and-forget notification to an n8n webhook after tickets are created.

The core workflow never depends on this: if N8N_WEBHOOK_URL is unset or n8n is down,
the call is skipped / logged and processing continues.
"""
import logging
import threading

import httpx

from .config import get_settings

log = logging.getLogger(__name__)


def notify_tickets_created(tickets: list[dict]) -> None:
    settings = get_settings()
    if not settings.n8n_webhook_url or not tickets:
        return

    def _send():
        try:
            httpx.post(
                settings.n8n_webhook_url,
                json={"event": "tickets.created", "tickets": tickets},
                timeout=settings.n8n_timeout_seconds,
            )
        except Exception as e:  # noqa: BLE001 - never break the app because of n8n
            log.warning("n8n notification failed: %s", type(e).__name__)

    threading.Thread(target=_send, daemon=True).start()
