from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol

from metrika_tools.cache import DiskCache

logger = logging.getLogger(__name__)

METRIKA_API_BASE = "https://api-metrika.yandex.net"

DEFAULT_DAY_METRICS: tuple[str, ...] = (
    "ym:s:visits",
    "ym:s:pageviews",
    "ym:s:users",
    "ym:s:bounceRate",
    "ym:s:avgVisitDurationSeconds",
)

LANDING_DAY_METRICS: tuple[str, ...] = DEFAULT_DAY_METRICS + (
    "ym:s:adCost",
    "ym:s:adClicks",
)

# Narrow set used by scripts/metrika_costs_margin.py (unchanged report columns).
COSTS_MARGIN_DAY_METRICS: tuple[str, ...] = (
    "ym:s:visits",
    "ym:s:pageviews",
    "ym:s:avgVisitDurationSeconds",
)


class MetrikaTokenError(RuntimeError):
    """OAuth token missing from the environment."""


class MetrikaHTTPError(Exception):
    def __init__(self, code: int, body: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"HTTP {code}: {body}")
        self.code = code
        self.body = body
        self.headers = headers or {}


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str]


class HttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse: ...


class UrllibHttpClient:
    def __init__(self, *, proxy_url: str | None = None) -> None:
        if proxy_url:
            handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            self._opener = urllib.request.build_opener(handler)
        else:
            self._opener = urllib.request.build_opener()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=timeout) as response:
                body = response.read()
                hdrs = {k: v for k, v in response.headers.items()}
                return HttpResponse(status=response.status, body=body, headers=hdrs)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            hdrs = {k: v for k, v in exc.headers.items()} if exc.headers else {}
            raise MetrikaHTTPError(exc.code, body, hdrs) from exc


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_cache_dir() -> Path:
    return repo_root() / ".cache" / "metrika"


def resolve_token() -> str:
    token = (
        os.environ.get("YANDEX_METRIKA_TOKEN", "").strip()
        or os.environ.get("YANDEX_METRIKA_OAUTH_TOKEN", "").strip()
    )
    if not token:
        raise MetrikaTokenError(
            "YANDEX_METRIKA_TOKEN or YANDEX_METRIKA_OAUTH_TOKEN is not set"
        )
    return token


def resolve_proxy_url() -> str | None:
    return os.environ.get("METRIKA_HTTP_PROXY", "").strip() or None


def parse_totals_row(totals: Any) -> list[float]:
    """Normalize Metrika ``totals`` to a flat float row.

    API may return ``[v0, v1, v2]`` or ``[[v0, v1, v2]]`` (metrics grouped in one row),
    or rarely one scalar per inner list ``[[v0], [v1], [v2]]``.
    """
    if not totals:
        return []
    if not isinstance(totals[0], list):
        return [float(x) for x in totals]
    inner_rows = [row for row in totals if isinstance(row, list)]
    if len(inner_rows) == 1:
        return [float(x) for x in inner_rows[0]]
    if inner_rows and all(len(row) == 1 for row in inner_rows):
        return [float(row[0]) for row in inner_rows]
    return [float(x) for x in inner_rows[0]]


@dataclass(frozen=True)
class DayStats:
    visits: float
    pageviews: float
    avg_duration_seconds: float
    visitors: float = 0.0
    bounce_rate: float = 0.0
    ad_cost: float = 0.0
    ad_clicks: float = 0.0

    @classmethod
    def from_metrics(cls, metrics: tuple[str, ...], values: list[float]) -> "DayStats":
        index = {name: i for i, name in enumerate(metrics)}
        def pick(name: str, default: float = 0.0) -> float:
            i = index.get(name)
            if i is None or i >= len(values):
                return default
            return values[i]

        return cls(
            visits=pick("ym:s:visits"),
            pageviews=pick("ym:s:pageviews"),
            avg_duration_seconds=pick("ym:s:avgVisitDurationSeconds"),
            visitors=pick("ym:s:users"),
            bounce_rate=pick("ym:s:bounceRate"),
            ad_cost=pick("ym:s:adCost"),
            ad_clicks=pick("ym:s:adClicks"),
        )

    @classmethod
    def from_totals(cls, values: list[float]) -> "DayStats":
        """Legacy three-metric shape used by metrika_costs_margin."""
        return cls(
            visits=values[0] if len(values) > 0 else 0.0,
            pageviews=values[1] if len(values) > 1 else 0.0,
            avg_duration_seconds=values[2] if len(values) > 2 else 0.0,
        )


class MetrikaClient:
    def __init__(
        self,
        *,
        token: str | None = None,
        http_client: HttpClient | None = None,
        proxy_url: str | None = None,
        cache: DiskCache | None = None,
        cache_dir: Path | None = None,
        cache_ttl_seconds: float = 4 * 3600,
        timeout: float = 60.0,
        max_retries: int = 4,
        retry_base_delay: float = 2.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        require_token: bool = True,
    ) -> None:
        self._token_explicit = token
        self._require_token = require_token
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.sleep_fn = sleep_fn

        if proxy_url is None and http_client is None:
            proxy_url = resolve_proxy_url()
        self.proxy_url = proxy_url

        if http_client is not None:
            self._http = http_client
        else:
            self._http = UrllibHttpClient(proxy_url=proxy_url)

        if cache is not None:
            self._cache = cache
        else:
            root = cache_dir if cache_dir is not None else default_cache_dir()
            self._cache = DiskCache(root, ttl_seconds=cache_ttl_seconds)

    def _token(self) -> str:
        if self._token_explicit is not None:
            token = self._token_explicit.strip()
            if not token and self._require_token:
                raise MetrikaTokenError(
                    "YANDEX_METRIKA_TOKEN or YANDEX_METRIKA_OAUTH_TOKEN is not set"
                )
            return token
        if not self._require_token:
            return ""
        return resolve_token()

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"OAuth {self._token()}",
            "Accept": "application/json",
        }

    def _request_json(
        self,
        path: str,
        params: dict[str, Any],
        *,
        cache_counter_id: int | None = None,
        cache_day: str | None = None,
        cache_metrics_key: str | None = None,
    ) -> dict[str, Any]:
        if cache_counter_id is not None and cache_day and cache_metrics_key:
            cached = self._cache.get(cache_counter_id, cache_day, cache_metrics_key)
            if cached is not None:
                return cached

        query = urllib.parse.urlencode(params)
        url = f"{METRIKA_API_BASE}{path}?{query}"
        payload = self._get_json_url(url)

        if cache_counter_id is not None and cache_day and cache_metrics_key:
            try:
                self._cache.set(cache_counter_id, cache_day, cache_metrics_key, payload)
            except OSError as exc:
                logger.warning("Failed to write Metrika cache: %s", exc)
        return payload

    def _get_json_url(self, url: str) -> dict[str, Any]:
        self._token()
        headers = self._auth_headers()
        last_error: MetrikaHTTPError | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._http.request(
                    "GET",
                    url,
                    headers=headers,
                    timeout=self.timeout,
                )
                if response.status >= 400:
                    raise MetrikaHTTPError(
                        response.status,
                        response.body.decode("utf-8", errors="replace"),
                        response.headers,
                    )
                return json.loads(response.body.decode("utf-8"))
            except MetrikaHTTPError as exc:
                last_error = exc
                if exc.code not in (429, 503) or attempt == self.max_retries - 1:
                    raise
                delay = self.retry_base_delay * (2**attempt)
                retry_after = exc.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                self.sleep_fn(delay)
        if last_error is not None:
            raise last_error
        raise RuntimeError("unreachable")

    def fetch_day_stats(
        self,
        counter_id: int,
        day: date,
        *,
        landing: bool = False,
        metrics: tuple[str, ...] | None = None,
    ) -> DayStats:
        metric_list = metrics or (LANDING_DAY_METRICS if landing else DEFAULT_DAY_METRICS)
        metrics_key = ",".join(metric_list)
        params = {
            "ids": counter_id,
            "date1": day.isoformat(),
            "date2": day.isoformat(),
            "metrics": metrics_key,
            "accuracy": "full",
        }
        payload = self._request_json(
            "/stat/v1/data",
            params,
            cache_counter_id=counter_id,
            cache_day=day.isoformat(),
            cache_metrics_key=metrics_key,
        )
        values = parse_totals_row(payload.get("totals", []))
        return DayStats.from_metrics(metric_list, values)

    def fetch_range(
        self,
        counter_id: int,
        date1: date,
        date2: date,
        *,
        landing: bool = False,
        metrics: tuple[str, ...] | None = None,
    ) -> list[tuple[date, DayStats]]:
        """Daily series built from one request per day (reliable vs dimension=date)."""
        if date2 < date1:
            raise ValueError("date2 must not be before date1")
        rows: list[tuple[date, DayStats]] = []
        current = date1
        while current <= date2:
            rows.append((current, self.fetch_day_stats(counter_id, current, landing=landing, metrics=metrics)))
            current += timedelta(days=1)
        return rows

    def fetch_dimension(
        self,
        counter_id: int,
        dimension: str,
        date1: date,
        date2: date,
        *,
        metrics: tuple[str, ...] = ("ym:s:visits",),
        page_limit: int = 100,
        offset: int = 0,
        fetch_all_pages: bool = True,
    ) -> list[dict[str, Any]]:
        """Dimension breakdown with limit/offset; optionally walks every page."""
        combined: list[dict[str, Any]] = []
        current_offset = offset
        while True:
            params = {
                "ids": counter_id,
                "date1": date1.isoformat(),
                "date2": date2.isoformat(),
                "dimensions": dimension,
                "metrics": ",".join(metrics),
                "limit": page_limit,
                "offset": current_offset,
                "accuracy": "full",
            }
            cache_key = f"dim:{dimension}:{metrics}:{page_limit}:{current_offset}"
            payload = self._request_json(
                "/stat/v1/data",
                params,
                cache_counter_id=counter_id,
                cache_day=f"{date1.isoformat()}_{date2.isoformat()}",
                cache_metrics_key=cache_key,
            )
            page = payload.get("data", [])
            if not isinstance(page, list):
                page = []
            combined.extend(page)
            if not fetch_all_pages:
                break
            total_rows = payload.get("total_rows")
            if isinstance(total_rows, int):
                if current_offset + len(page) >= total_rows or not page:
                    break
            elif len(page) < page_limit or not page:
                break
            current_offset += page_limit
        return combined

    def fetch_counter_list(self) -> list[dict[str, Any]]:
        payload = self._request_json("/management/v1/counters", {"per_page": 1000})
        counters = payload.get("counters", [])
        return counters if isinstance(counters, list) else []

    def fetch_counter(self, counter_id: int) -> dict[str, Any]:
        return self._request_json(f"/management/v1/counter/{counter_id}", {})