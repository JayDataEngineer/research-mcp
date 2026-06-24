"""Celery task for scraping with concurrency control and caching"""

import redis as _redis

from ..celery_app import app
from ..tasks.base import BaseTask
from ..scrapers.base import scrape_with_fallback


@app.task(
    bind=True,
    base=BaseTask,
    name="scrape_task",
    # Retries only on transient infrastructure failures — never on logic
    # errors (KeyError/ValueError/etc. would mask bugs if retried).
    # Exponential backoff 1s → 2s → 4s, max 3 attempts, with jitter so a
    # broker blip doesn't synchronously hammer every queued URL.
    autoretry_for=(_redis.exceptions.ConnectionError, ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def scrape_task(
    self,
    url: str,
    force_method: str | None = None,
    css_selector: str | None = None
) -> dict:
    """
    Scrape a URL with automatic method routing and caching

    This runs in the Celery worker with controlled concurrency.
    Returns dict that can be serialized to JSON for Redis.

    Args:
        url: URL to scrape
        force_method: Force specific scraping method
        css_selector: Optional CSS selector for targeted content extraction
    """
    # Check cache first
    if self.cache:
        cached = self.run_async(self.cache.get_scrape(url))
        if cached:
            cached["cached"] = True
            return cached

    # Run the async scrape
    result = self.run_async(
        scrape_with_fallback(
            url=url,
            cleaner=self.cleaner,
            db=self.db,
            force_method=force_method,
            css_selector=css_selector
        )
    )

    # Cache successful results
    if result.get("success") and self.cache:
        self.run_async(self.cache.set_scrape(url, result))

    result["cached"] = False
    return result
