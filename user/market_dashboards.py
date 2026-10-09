"""
Market Dashboards: aggregated, anonymized view of marketplace activity.

Raw Event rows -> derived sessions -> aggregates per granularity -> API.
Nothing here is supplier-specific: no supplier name or supplier-level figure is returned.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone

from user.market_metrics import GRANULARITY_LABELS, TEMPORAL_GRANULARITIES, metric_definitions
from user.models import Category, Event, Supplier
from user.platform_config import get_int
from user.reporting_period import MONTH_SHORT_FR, ReportPeriod, aware_range_for_local_dates
from user.search_normalize import fold_text, levenshtein_distance, normalize_phrase, tokenize_query

CACHE_SECONDS = 300
CACHE_VERSION = "v3"
UNKNOWN_GEO = "Non renseigné"
WEEKDAY_LABELS = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")

SESSION_GRANULARITIES = ("day", "week", "month", "weekday", "geo")
SEARCH_GRANULARITIES = SESSION_GRANULARITIES

#: Supplier views embedded in a product page are not standalone storefront visits.
EMBEDDED_SUPPLIER_VIEW_SOURCES = {"product_page"}

#: Words too generic to identify a supplier when they appear in a company name.
GENERIC_NAME_WORDS = {
    "emballage", "emballages", "packaging", "pack", "sarl", "eurl", "spa", "snc", "sas",
    "algerie", "algeria", "dz", "groupe", "group", "industrie", "industries", "industriel",
    "societe", "ste", "company", "co", "the", "des", "les", "and", "et", "de", "du", "la",
}

SESSION_ACTIONS = (
    ("search", "Au moins une recherche", (Event.EventType.BUYER_SEARCH,)),
    ("product_view", "Au moins une visite de fiche produit", (Event.EventType.BUYER_PRODUCT_VIEW,)),
    ("supplier_view", "Au moins une visite de boutique", (Event.EventType.BUYER_SUPPLIER_VIEW,)),
    (
        "favorite",
        "Au moins un favori (produit ou fournisseur)",
        (Event.EventType.BUYER_PRODUCT_FAVORITE, Event.EventType.BUYER_FOLLOW_SUPPLIER),
    ),
    ("product_favorite", "Au moins un produit ajouté aux favoris", (Event.EventType.BUYER_PRODUCT_FAVORITE,)),
    ("supplier_favorite", "Au moins un fournisseur ajouté aux favoris", (Event.EventType.BUYER_FOLLOW_SUPPLIER,)),
    ("rfq", "Au moins une demande de devis", (Event.EventType.BUYER_QUOTATION_REQUEST,)),
    ("order", "Au moins une commande", (Event.EventType.BUYER_ORDER_CREATED,)),
)

#: Actions that exist in the business data but are not yet logged as session events.
UNTRACKED_SESSION_ACTIONS = (
    ("conversation", "Nouvelle conversation"),
    ("sample_request", "Nouvelle demande d'échantillon"),
)


def gap_minutes() -> int:
    return max(5, get_int("market_session_gap_minutes", 30))


def min_population() -> int:
    return max(2, get_int("market_min_population", 3))


def low_results_threshold() -> int:
    return max(0, get_int("market_low_results_threshold", 2))


def min_suppliers() -> int:
    """Distinct suppliers required before a supplier-related figure is published."""
    return max(1, get_int("market_min_suppliers", 1))


def definitions_payload() -> dict:
    return metric_definitions(
        gap_minutes=gap_minutes(),
        min_population=min_population(),
        low_results=low_results_threshold(),
        min_suppliers=min_suppliers(),
    )


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def describe(values: list[float], labels: list[str] | None = None) -> dict:
    """Total, mean, population standard deviation, min, max and the label of the max."""
    if not values:
        return {
            "total": 0, "count": 0, "average": 0.0, "stdDev": 0.0,
            "min": 0, "max": 0, "maxLabel": None,
        }
    total = sum(values)
    mean = total / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    max_value = max(values)
    max_index = values.index(max_value)
    return {
        "total": round(total, 2),
        "count": len(values),
        "average": round(mean, 2),
        "stdDev": round(math.sqrt(variance), 2),
        "min": min(values),
        "max": max_value,
        "maxLabel": labels[max_index] if labels else None,
    }


def change_pct(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@dataclass
class MarketSession:
    start: datetime
    end: datetime
    geo: str
    events: list[tuple[datetime, str, dict]] = field(default_factory=list)
    types: Counter = field(default_factory=Counter)

    @property
    def duration_seconds(self) -> int:
        return int((self.end - self.start).total_seconds())

    @property
    def local_date(self) -> date:
        return timezone.localtime(self.start).date()


def _market_event_queryset(start: date, end: date):
    lo, hi = aware_range_for_local_dates(start, end)
    return (
        Event.objects.filter(
            created_at__gte=lo,
            created_at__lte=hi,
            event_type__startswith="buyer.",
            actor_role__in=(Event.ActorRole.BUYER, Event.ActorRole.VISITOR),
        )
        .filter(Q(actor_user__isnull=True) | Q(actor_user__supplier__isnull=True))
        .exclude(Q(context__has_key="device_class") & Q(context__device_class="bot"))
    )


def _actor_key(user_id, sid, ip_hash) -> str | None:
    if sid:
        return f"sid:{sid}"
    if user_id:
        return f"user:{user_id}"
    if ip_hash:
        return f"ip:{ip_hash}"
    return None


def build_sessions(start: date, end: date) -> tuple[list[MarketSession], dict]:
    """Split buyer/visitor events into sessions (inactivity gap) for a local-date range."""
    rows = _market_event_queryset(start, end).values_list(
        "created_at",
        "event_type",
        "actor_user_id",
        "context__visitor_session_id",
        "context__geo_ip_hash",
        "context__geo_wilaya_label",
        "context__source",
        "context__query",
        "context__results_count",
        "context__search_id",
    )
    by_actor: dict[str, list[tuple]] = defaultdict(list)
    unattributed = 0
    considered = 0
    for created_at, event_type, user_id, sid, ip_hash, geo, source, query, results, search_id in rows:
        considered += 1
        key = _actor_key(user_id, sid, ip_hash)
        if key is None:
            unattributed += 1
            continue
        by_actor[key].append(
            (
                created_at,
                event_type,
                {
                    "geo": geo,
                    "source": source,
                    "query": query,
                    "results": results,
                    "search_id": search_id,
                },
            )
        )

    gap = timedelta(minutes=gap_minutes())
    sessions: list[MarketSession] = []
    for events in by_actor.values():
        events.sort(key=lambda item: item[0])
        current: MarketSession | None = None
        for created_at, event_type, ctx in events:
            if current is None or created_at - current.end > gap:
                current = MarketSession(start=created_at, end=created_at, geo=UNKNOWN_GEO)
                sessions.append(current)
            current.end = created_at
            current.events.append((created_at, event_type, ctx))
            current.types[event_type] += 1
            geo_label = (ctx.get("geo") or "").strip()
            if current.geo == UNKNOWN_GEO and geo_label and geo_label != UNKNOWN_GEO:
                current.geo = geo_label

    sessions.sort(key=lambda session: session.start)
    quality = {
        "eventsConsidered": considered,
        "eventsWithoutIdentity": unattributed,
        "singleEventSessions": sum(1 for session in sessions if len(session.events) == 1),
    }
    return sessions, quality


# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------


def _day_label(day: date) -> str:
    return f"{day.day} {MONTH_SHORT_FR[day.month - 1]}"


def _bucket_key(day: date, granularity: str) -> str:
    if granularity == "week":
        monday = day - timedelta(days=day.weekday())
        return monday.isoformat()
    if granularity == "month":
        return f"{day.year:04d}-{day.month:02d}"
    if granularity == "weekday":
        return str(day.weekday())
    return day.isoformat()


def temporal_buckets(start: date, end: date, granularity: str) -> list[dict]:
    """Ordered buckets covering the period, including empty ones."""
    buckets: list[dict] = []
    seen: set[str] = set()
    if granularity == "weekday":
        counts = Counter((start + timedelta(days=offset)).weekday() for offset in range((end - start).days + 1))
        return [
            {"key": str(index), "label": WEEKDAY_LABELS[index], "units": counts.get(index, 0)}
            for index in range(7)
        ]
    day = start
    while day <= end:
        key = _bucket_key(day, granularity)
        if key not in seen:
            seen.add(key)
            if granularity == "week":
                label = f"sem. {_day_label(max(date.fromisoformat(key), start))}"
            elif granularity == "month":
                label = f"{MONTH_SHORT_FR[day.month - 1]} {day.year}"
            else:
                label = _day_label(day)
            buckets.append({"key": key, "label": label, "units": 0})
        buckets[-1]["units"] += 1
        day += timedelta(days=1)
    return buckets


def geo_buckets(counts: Counter, threshold: int, unit: str = "sessions") -> list[dict]:
    """Wilaya buckets; wilayas below the threshold are merged into one group."""
    buckets = []
    merged = 0
    for label, count in counts.most_common():
        if label != UNKNOWN_GEO and count < threshold:
            merged += count
            continue
        buckets.append({"key": label, "label": label, "units": 1})
    if merged:
        buckets.append(
            {"key": "__other__", "label": f"Autres wilayas (< {threshold} {unit})", "units": 1}
        )
    return buckets


def _geo_bucket_key(geo: str, kept: set[str]) -> str:
    return geo if geo in kept else "__other__"


# ---------------------------------------------------------------------------
# Sessions dashboard
# ---------------------------------------------------------------------------


def _cache_key(section: str, period: ReportPeriod, granularity: str) -> str:
    return (
        f"market-dash:{CACHE_VERSION}:{section}:{period.start}:{period.end}:"
        f"{granularity}:{gap_minutes()}:{min_population()}:{min_suppliers()}"
    )


def _cached(section: str, period: ReportPeriod, granularity: str, builder):
    key = _cache_key(section, period, granularity)
    payload = cache.get(key)
    if payload is None:
        payload = builder()
        cache.set(key, payload, CACHE_SECONDS)
    return payload


def _session_bucket_key(session: MarketSession, granularity: str, kept_geo: set[str]) -> str:
    if granularity == "geo":
        return _geo_bucket_key(session.geo, kept_geo)
    return _bucket_key(session.local_date, granularity)


def _buckets_for(
    period: ReportPeriod, granularity: str, geo_counts: Counter, unit: str = "sessions"
) -> list[dict]:
    if granularity == "geo":
        return geo_buckets(geo_counts, min_population(), unit)
    return temporal_buckets(period.start, period.end, granularity)


def build_sessions_dashboard(period: ReportPeriod, granularity: str) -> dict:
    return _cached("sessions", period, granularity, lambda: _sessions_dashboard(period, granularity))


def _sessions_dashboard(period: ReportPeriod, granularity: str) -> dict:
    sessions, quality = build_sessions(period.start, period.end)
    previous_sessions, _ = build_sessions(period.prev_start, period.prev_end)

    geo_counts = Counter(session.geo for session in sessions)
    buckets = _buckets_for(period, granularity, geo_counts)
    kept_geo = {bucket["key"] for bucket in buckets} if granularity == "geo" else set()

    per_bucket: dict[str, list[MarketSession]] = defaultdict(list)
    for session in sessions:
        per_bucket[_session_bucket_key(session, granularity, kept_geo)].append(session)

    series = []
    for bucket in buckets:
        rows = per_bucket.get(bucket["key"], [])
        durations = [session.duration_seconds for session in rows]
        series.append(
            {
                "key": bucket["key"],
                "label": bucket["label"],
                "sessions": len(rows),
                "units": bucket["units"],
                "sessionsPerUnit": round(len(rows) / bucket["units"], 2) if bucket["units"] else 0,
                "avgDurationSeconds": round(sum(durations) / len(durations)) if durations else 0,
            }
        )

    labels = [row["label"] for row in series]
    durations = [session.duration_seconds for session in sessions]
    longest = max(sessions, key=lambda session: session.duration_seconds, default=None)
    duration_stats = describe(durations)
    previous_average = describe([session.duration_seconds for session in previous_sessions])["average"]
    total = len(sessions)

    behaviour = []
    for key, label, event_types in SESSION_ACTIONS:
        numerator = sum(
            1 for session in sessions if any(session.types.get(event_type) for event_type in event_types)
        )
        behaviour.append(
            {
                "key": key,
                "label": label,
                "numerator": numerator,
                "denominator": total,
                "ratePct": round(numerator / total * 100, 1) if total else 0.0,
                "tracked": True,
            }
        )
    for key, label in UNTRACKED_SESSION_ACTIONS:
        behaviour.append(
            {"key": key, "label": label, "numerator": None, "denominator": total, "ratePct": None, "tracked": False}
        )

    return {
        "period": period.as_dict(),
        "granularity": granularity,
        "granularityLabel": GRANULARITY_LABELS[granularity],
        "temporal": granularity in TEMPORAL_GRANULARITIES,
        "kpis": {
            "sessions": {
                "total": total,
                "previousTotal": len(previous_sessions),
                "changePct": change_pct(total, len(previous_sessions)),
                "perUnit": describe([row["sessions"] for row in series], labels),
            },
            "duration": {
                "totalSeconds": duration_stats["total"],
                "averageSeconds": duration_stats["average"],
                "stdDevSeconds": duration_stats["stdDev"],
                "minSeconds": duration_stats["min"],
                "maxSeconds": duration_stats["max"],
                "maxDate": longest.local_date.isoformat() if longest else None,
                "previousAverageSeconds": previous_average,
                "changePct": change_pct(duration_stats["average"], previous_average),
            },
        },
        "series": series,
        "behaviour": behaviour,
        "dataQuality": quality,
    }


# ---------------------------------------------------------------------------
# Search dashboard
# ---------------------------------------------------------------------------


def lexical_key(query: str) -> str:
    """Normalized keyword: folded, stop words removed, simple French plurals singularized."""
    tokens = []
    for token in tokenize_query(query):
        if len(token) > 3 and re.fullmatch(r"[a-z]+", token):
            if token.endswith("aux"):
                token = f"{token[:-3]}al"
            elif token.endswith(("s", "x")):
                token = token[:-1]
        tokens.append(token)
    return " ".join(tokens) or normalize_phrase(query)


def _tokens_match(left: str, right: str) -> bool:
    if left == right:
        return True
    prefix = 0
    for a, b in zip(left, right):
        if a != b:
            break
        prefix += 1
    return prefix >= 5


def lexical_similarity(left: str, right: str) -> float:
    """Share of matching tokens (identical, or common prefix of 5+ letters)."""
    left_tokens = left.split()
    right_tokens = right.split()
    if not left_tokens or not right_tokens:
        return 0.0
    matched = sum(1 for token in left_tokens if any(_tokens_match(token, other) for other in right_tokens))
    distinct = len(left_tokens) + len(right_tokens) - matched
    return round(matched / distinct, 2) if distinct else 0.0


def _protected_name_tokens() -> set[str]:
    """Distinctive words of supplier names (generic and catalog vocabulary excluded)."""
    vocabulary: set[str] = set(GENERIC_NAME_WORDS)
    for names in Category.objects.values_list("name_fr", "name_en"):
        for name in names:
            vocabulary.update(fold_text(name or "").split())
    protected: set[str] = set()
    for name in Supplier.objects.values_list("company_name", flat=True):
        for token in fold_text(name or "").split():
            if len(token) >= 4 and token not in vocabulary:
                protected.add(token)
    return protected


def _mentions_supplier(key: str, protected: set[str]) -> bool:
    """Exact supplier-name word, or a close misspelling (1 edit) for words of 5+ letters."""
    for token in key.split():
        if token in protected:
            return True
        if len(token) >= 5 and any(
            abs(len(token) - len(name)) <= 1 and levenshtein_distance(token, name) <= 1
            for name in protected
            if len(name) >= 5
        ):
            return True
    return False


def build_search_dashboard(period: ReportPeriod, granularity: str) -> dict:
    return _cached("search", period, granularity, lambda: _search_dashboard(period, granularity))


def _search_dashboard(period: ReportPeriod, granularity: str) -> dict:
    sessions, quality = build_sessions(period.start, period.end)
    previous_sessions, _ = build_sessions(period.prev_start, period.prev_end)
    threshold = min_population()

    searches = []
    for index, session in enumerate(sessions):
        current_key = None
        for created_at, event_type, ctx in session.events:
            if event_type == Event.EventType.BUYER_SEARCH:
                query = str(ctx.get("query") or "").strip()
                if not query:
                    continue
                current_key = lexical_key(query)
                searches.append(
                    {
                        "session": index,
                        "date": timezone.localtime(created_at).date(),
                        "geo": session.geo,
                        "raw": query.lower(),
                        "key": current_key,
                        "results": ctx.get("results"),
                        "search_id": ctx.get("search_id"),
                        "visits": 0,
                    }
                )
            elif (
                event_type == Event.EventType.BUYER_PRODUCT_VIEW
                and ctx.get("source") == "search"
                and current_key is not None
                and searches
                and searches[-1]["session"] == index
            ):
                searches[-1]["visits"] += 1

    previous_total = sum(session.types.get(Event.EventType.BUYER_SEARCH, 0) for session in previous_sessions)

    geo_counts = Counter(session.geo for session in sessions)
    buckets = _buckets_for(period, granularity, geo_counts)
    kept_geo = {bucket["key"] for bucket in buckets} if granularity == "geo" else set()
    per_bucket = Counter()
    for row in searches:
        key = (
            _geo_bucket_key(row["geo"], kept_geo)
            if granularity == "geo"
            else _bucket_key(row["date"], granularity)
        )
        per_bucket[key] += 1
    series = [
        {
            "key": bucket["key"],
            "label": bucket["label"],
            "searches": per_bucket.get(bucket["key"], 0),
            "units": bucket["units"],
        }
        for bucket in buckets
    ]
    labels = [row["label"] for row in series]

    per_session = Counter(row["session"] for row in searches)
    counts_per_session = [per_session.get(index, 0) for index in range(len(sessions))]
    histogram_labels = ("0", "1", "2", "3", "4", "5+")
    histogram = Counter(min(count, 5) for count in counts_per_session)

    impressions = _impressions_by_search_id(period)
    keywords, hidden = _keyword_rows(searches, impressions, threshold)

    low = low_results_threshold()
    low_result_rows = [
        row for row in keywords if row["avgResults"] is not None and row["avgResults"] <= low
    ]
    similar = _similar_pairs(keywords)

    return {
        "period": period.as_dict(),
        "granularity": granularity,
        "granularityLabel": GRANULARITY_LABELS[granularity],
        "temporal": granularity in TEMPORAL_GRANULARITIES,
        "kpis": {
            "searches": {
                "total": len(searches),
                "previousTotal": previous_total,
                "changePct": change_pct(len(searches), previous_total),
                "perUnit": describe([row["searches"] for row in series], labels),
            },
            "perSession": {
                **describe(counts_per_session),
                "sessions": len(sessions),
                "sessionsWithSearch": sum(1 for count in counts_per_session if count),
            },
            "zeroResultSearches": sum(1 for row in searches if row["results"] == 0),
        },
        "series": series,
        "histogram": [
            {"label": label, "count": histogram.get(index, 0)}
            for index, label in enumerate(histogram_labels)
        ],
        "keywords": keywords[:50],
        "lowResultKeywords": low_result_rows[:20],
        "similarKeywords": similar[:20],
        "hidden": hidden,
        "dataQuality": quality,
    }


def _impressions_by_search_id(period: ReportPeriod) -> dict[str, int]:
    lo, hi = aware_range_for_local_dates(period.start, period.end)
    rows = (
        Event.objects.filter(
            event_type=Event.EventType.SYSTEM_APPEAR_IN_SEARCH,
            created_at__gte=lo,
            created_at__lte=hi + timedelta(minutes=5),
        )
        .exclude(context__search_id=None)
        .values("context__search_id")
        .annotate(total=Count("id"))
    )
    return {str(row["context__search_id"]): row["total"] for row in rows}


def _keyword_rows(searches: list[dict], impressions: dict[str, int], threshold: int):
    protected = _protected_name_tokens()
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in searches:
        grouped[row["key"]].append(row)

    rows = []
    hidden = {"belowThreshold": 0, "belowThresholdKeywords": 0, "supplierNames": 0, "threshold": threshold}
    for key, items in grouped.items():
        if _mentions_supplier(key, protected):
            hidden["supplierNames"] += len(items)
            continue
        distinct_sessions = len({item["session"] for item in items})
        if distinct_sessions < threshold:
            hidden["belowThreshold"] += len(items)
            hidden["belowThresholdKeywords"] += 1
            continue
        results = [item["results"] for item in items if isinstance(item["results"], (int, float))]
        variants = Counter(item["raw"] for item in items)
        rows.append(
            {
                "key": key,
                "keyword": variants.most_common(1)[0][0],
                "variants": [variant for variant, _ in variants.most_common(5)],
                "searches": len(items),
                "sessions": distinct_sessions,
                "avgResults": round(sum(results) / len(results), 1) if results else None,
                "impressions": sum(
                    impressions.get(str(item["search_id"]), 0) for item in items if item["search_id"]
                ),
                "visitsGenerated": sum(item["visits"] for item in items),
            }
        )
    rows.sort(key=lambda row: (-row["searches"], row["keyword"]))
    return rows, hidden


def _similar_pairs(keywords: list[dict], *, minimum: float = 0.5) -> list[dict]:
    pairs = []
    top = keywords[:60]
    for index, left in enumerate(top):
        for right in top[index + 1:]:
            score = lexical_similarity(left["key"], right["key"])
            if score >= minimum:
                pairs.append(
                    {
                        "left": left["keyword"],
                        "right": right["keyword"],
                        "similarity": score,
                        "searches": left["searches"] + right["searches"],
                    }
                )
    pairs.sort(key=lambda pair: (-pair["similarity"], -pair["searches"]))
    return pairs
