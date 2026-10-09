"""
Read models for the digital presence dashboards (supplier view and global admin view).

Rules enforced here:
- a KPI is only shown when the source provided it for the exact window, or —
  for additive metrics — when every day of the window is present;
- missing days stay `None` in chart series (gaps), never 0;
- platforms are never merged (no Facebook + Instagram reach, no clicks + sessions).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.db.models import Max, Q
from django.utils import timezone

from user.digital_config import (
    MANUAL_SYNC_COOLDOWN_MINUTES,
    METRICS,
    PROVIDERS,
    TOP_DIMENSIONS,
    TOP_ROWS,
    configuration_problem,
    metric_spec,
)
from user.digital_http import ERROR_MESSAGES
from user.digital_models import (
    DigitalAuthorization,
    DigitalConnection,
    DigitalMetric,
    DigitalPlatform,
)

ALLOWED_DAYS = (7, 28, 90)
PLATFORM_ORDER = [
    DigitalPlatform.META_FACEBOOK,
    DigitalPlatform.META_INSTAGRAM,
    DigitalPlatform.GOOGLE_ANALYTICS,
    DigitalPlatform.GOOGLE_SEARCH_CONSOLE,
]
UNAVAILABLE_REASONS = {
    "unsupported_metric": "Métrique non prise en charge par la source pour cette ressource.",
    "insufficient_permission": "Meta ne fournit pas cette métrique pour ce compte (droits ou seuil d'audience).",
    "insights_unavailable": "Statistiques indisponibles sous les seuils d'éligibilité de la source.",
    "invalid_request": "La source a refusé cette métrique pour cette ressource.",
    "no_key_events": "Aucun événement clé n'est configuré dans la propriété.",
}
CONTENT_METRIC_LABELS = {
    "post_media_view": "Vues",
    "post_total_media_view_unique": "Spectateurs uniques",
    "post_reactions_by_type_total": "Réactions",
    "media_views": "Vues",
    "media_reach": "Portée",
    "media_total_interactions": "Interactions",
}
SEARCH_ROWS_NOTE = (
    "Lignes principales renvoyées par Google pour cette période (limitées à {limit}) : "
    "ce n'est pas un export exhaustif de toutes les requêtes."
)


def parse_days(raw, default=28) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value in ALLOWED_DAYS else default


def _num(value):
    return None if value is None else float(value)


def _iso(value):
    return value.isoformat() if value else None


def window_bounds(connection: DigitalConnection, days: int):
    if connection.data_end is None:
        return None, None
    return connection.data_end - timedelta(days=days - 1), connection.data_end


def metric_value(connection: DigitalConnection, spec, start, end) -> dict:
    """Value of one KPI for [start, end], with an honest status."""
    unsupported = (connection.metadata.get("unsupportedMetrics") or {}).get(spec.key)
    if unsupported:
        code = unsupported.get("code") if isinstance(unsupported, dict) else str(unsupported)
        return {"value": None, "status": "unavailable", "reason": UNAVAILABLE_REASONS.get(code, UNAVAILABLE_REASONS["unsupported_metric"]), "code": code}
    if start is None:
        return {"value": None, "status": "no_data", "reason": "Aucune synchronisation terminée pour cette ressource."}

    base = DigitalMetric.objects.filter(connection=connection, metric_key=spec.key, dimension_type="")
    window = base.filter(
        granularity=DigitalMetric.Granularity.WINDOW, period_start=start, period_end=end
    ).first()
    if window is not None:
        if window.value is None:
            return {"value": None, "status": "unavailable", "reason": "La source n'a pas fourni de valeur pour cette période."}
        return {"value": _num(window.value), "status": "ok", "provenance": window.provenance}

    if spec.aggregation == "sum":
        days = (end - start).days + 1
        daily = list(
            base.filter(
                granularity=DigitalMetric.Granularity.DAY,
                period_start__gte=start,
                period_end__lte=end,
                value__isnull=False,
            ).values_list("value", flat=True)
        )
        if len(daily) == days:
            return {"value": _num(sum(daily)), "status": "ok", "provenance": {"aggregation": "sum_of_days"}}
        return {
            "value": None,
            "status": "partial",
            "reason": f"Données incomplètes : {len(daily)} jour(s) sur {days} fournis par la source.",
            "daysWithData": len(daily),
            "daysExpected": days,
        }
    return {
        "value": None,
        "status": "unavailable",
        "reason": f"La source ne fournit pas cette valeur pour une période de {(end - start).days + 1} jours.",
    }


def _series(connection, spec, start, end):
    values = {
        row["period_start"]: row["value"]
        for row in DigitalMetric.objects.filter(
            connection=connection,
            metric_key=spec.key,
            granularity=DigitalMetric.Granularity.DAY,
            dimension_type="",
            period_start__gte=start,
            period_end__lte=end,
        ).values("period_start", "value")
    }
    points = []
    cursor = start
    while cursor <= end:
        points.append({"date": cursor.isoformat(), "value": _num(values.get(cursor))})
        cursor += timedelta(days=1)
    return {
        "key": spec.key,
        "label": spec.label,
        "unit": spec.unit,
        "definition": spec.definition,
        "points": points,
        "missingDays": sum(1 for p in points if p["value"] is None),
    }


def _tops(connection, start, end):
    dims = TOP_DIMENSIONS.get(connection.platform, ())
    if not dims:
        return []
    rows = DigitalMetric.objects.filter(
        connection=connection,
        granularity=DigitalMetric.Granularity.WINDOW,
        period_start=start,
        period_end=end,
        dimension_type__in=[d for d, _ in dims],
    ).values("dimension_type", "dimension_key", "dimension_label", "metric_key", "value", "provenance")
    grouped: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        entry = grouped[row["dimension_type"]].setdefault(
            row["dimension_key"],
            {"label": row["dimension_label"], "rank": (row["provenance"] or {}).get("rank", 999), "values": {}},
        )
        entry["values"][row["metric_key"]] = _num(row["value"])
    tops = []
    for dimension, label in dims:
        entries = sorted(grouped.get(dimension, {}).values(), key=lambda e: e["rank"])
        tops.append(
            {
                "dimension": dimension,
                "label": label,
                "rows": [{"label": e["label"], "values": e["values"]} for e in entries],
                "note": SEARCH_ROWS_NOTE.format(limit=TOP_ROWS),
            }
        )
    return tops


def _contents(connection):
    rows = DigitalMetric.objects.filter(
        connection=connection,
        granularity=DigitalMetric.Granularity.LIFETIME,
        dimension_type="content",
    ).values("dimension_key", "dimension_label", "metric_key", "value", "period_start", "provenance", "fetched_at")
    items: dict[str, dict] = {}
    for row in rows:
        item = items.setdefault(
            row["dimension_key"],
            {
                "label": row["dimension_label"],
                "publishedAt": row["period_start"].isoformat(),
                "url": (row["provenance"] or {}).get("permalink", ""),
                "mediaType": (row["provenance"] or {}).get("mediaType", ""),
                "fetchedAt": _iso(row["fetched_at"]),
                "metrics": [],
            },
        )
        item["metrics"].append(
            {
                "key": row["metric_key"],
                "label": CONTENT_METRIC_LABELS.get(row["metric_key"], row["metric_key"]),
                "value": _num(row["value"]),
            }
        )
    ordered = sorted(items.values(), key=lambda i: i["publishedAt"], reverse=True)
    return ordered[:12]


def connection_summary(connection: DigitalConnection, *, now=None) -> dict:
    now = now or timezone.now()
    next_manual = None
    if connection.last_manual_sync_at:
        candidate = connection.last_manual_sync_at + timedelta(minutes=MANUAL_SYNC_COOLDOWN_MINUTES)
        next_manual = candidate if candidate > now else None
    return {
        "id": connection.pk,
        "platform": connection.platform,
        "resourceType": connection.resource_type,
        "name": connection.display_name or connection.external_id,
        "externalId": connection.external_id,
        "status": connection.status,
        "isDemo": connection.is_demo,
        "lastSyncedAt": _iso(connection.last_synced_at),
        "lastSyncAttemptAt": _iso(connection.last_sync_attempt_at),
        "dataStart": _iso(connection.data_start),
        "dataEnd": _iso(connection.data_end),
        "sourceTimezone": connection.source_timezone or PROVIDERS[connection.platform].source_timezone,
        "lastError": (
            {"code": connection.last_error_code, "message": connection.last_error_message}
            if connection.last_error_code
            else None
        ),
        "manualSyncAvailableAt": _iso(next_manual),
    }


def connection_dashboard(connection: DigitalConnection, days: int) -> dict:
    start, end = window_bounds(connection, days)
    platform = connection.platform
    kpis = []
    for spec in METRICS[platform]:
        result = metric_value(connection, spec, start, end)
        if result.get("code") == "no_key_events":
            continue  # key events are only shown when the property defines some
        kpis.append(
            {
                "key": spec.key,
                "label": spec.label,
                "unit": spec.unit,
                "definition": spec.definition,
                **{k: v for k, v in result.items() if k != "code"},
            }
        )
    series = (
        [_series(connection, spec, start, end) for spec in METRICS[platform] if spec.chart]
        if start
        else []
    )
    notes = [
        ERROR_MESSAGES.get(note, "")
        for note in (connection.metadata.get("notes") or [])
        if note in ERROR_MESSAGES
    ]
    if "content_unavailable" in (connection.metadata.get("notes") or []):
        notes.append("Les contenus récents ne sont pas accessibles avec les autorisations actuelles.")
    return {
        **connection_summary(connection),
        "periodStart": _iso(start),
        "periodEnd": _iso(end),
        "days": days,
        "sourceLabel": PROVIDERS[platform].label,
        "freshnessNote": PROVIDERS[platform].freshness_note,
        "kpis": kpis,
        "series": series,
        "tops": _tops(connection, start, end) if start else [],
        "contents": _contents(connection),
        "notes": [n for n in notes if n],
    }


def platform_cards(supplier, *, include_config_details=False) -> list[dict]:
    authorizations = {a.platform: a for a in DigitalAuthorization.objects.filter(supplier=supplier)}
    connections = defaultdict(list)
    for connection in DigitalConnection.objects.filter(supplier=supplier):
        connections[connection.platform].append(connection)

    cards = []
    for platform in PLATFORM_ORDER:
        config = PROVIDERS[platform]
        problem = configuration_problem(platform)
        authorization = authorizations.get(platform)
        platform_connections = connections.get(platform, [])
        resources = authorization.available_resources if authorization else []
        eligible = [r for r in resources if r.get("eligible")]
        selected_ids = {c.external_id for c in platform_connections}

        if authorization is None and not platform_connections:
            state = "not_connected"
        elif authorization is not None and authorization.status != DigitalAuthorization.Status.ACTIVE:
            state = "needs_reauth"
        elif any(c.status == DigitalConnection.Status.NEEDS_REAUTH for c in platform_connections):
            state = "needs_reauth"
        elif not platform_connections:
            state = "needs_selection" if eligible else "no_eligible_resource"
        elif any(c.status == DigitalConnection.Status.ERROR for c in platform_connections):
            state = "error"
        elif all(c.status == DigitalConnection.Status.INELIGIBLE for c in platform_connections):
            state = "ineligible"
        else:
            state = "connected"

        card = {
            "platform": platform,
            "label": config.label,
            "resourceLabel": config.resource_label,
            "configured": not problem,
            "configurationMessage": (problem if include_config_details else ERROR_MESSAGES["not_configured"])
            if problem
            else "",
            "state": state,
            "scopes": list(config.scopes),
            "accountName": authorization.external_account_name if authorization else "",
            "authorizedAt": _iso(authorization.last_authorized_at) if authorization else None,
            "authorizationError": (
                {"code": authorization.last_error_code, "message": authorization.last_error_message}
                if authorization and authorization.last_error_code
                else None
            ),
            "resources": [
                {
                    "id": r["id"],
                    "name": r.get("name", r["id"]),
                    "eligible": bool(r.get("eligible")),
                    "reason": r.get("reason", ""),
                    "selected": r["id"] in selected_ids,
                    "detail": r.get("account") or r.get("permissionLevel") or r.get("accountType") or "",
                }
                for r in resources
            ],
            "connections": [connection_summary(c) for c in platform_connections],
            "freshnessNote": config.freshness_note,
        }
        if state == "no_eligible_resource":
            card["stateMessage"] = (
                resources[0].get("reason")
                if resources and resources[0].get("reason")
                else "Aucune ressource admissible n'a été trouvée avec ce compte."
            )
        cards.append(card)
    return cards


def build_supplier_dashboard(supplier, *, days=28, platform=None, include_config_details=False) -> dict:
    qs = DigitalConnection.objects.filter(supplier=supplier).exclude(
        status=DigitalConnection.Status.INELIGIBLE
    )
    if platform in PROVIDERS:
        qs = qs.filter(platform=platform)
    sources = [connection_dashboard(c, days) for c in qs.order_by("platform", "display_name")]
    synced = [c.last_synced_at for c in qs if c.last_synced_at]
    return {
        "supplier": {"id": supplier.pk, "companyName": supplier.company_name},
        "days": days,
        "allowedDays": list(ALLOWED_DAYS),
        "platform": platform if platform in PROVIDERS else None,
        "lastUpdatedAt": _iso(max(synced)) if synced else None,
        "cards": platform_cards(supplier, include_config_details=include_config_details),
        "sources": sources,
        "hasDemoData": any(s["isDemo"] for s in sources),
        "pricing": {
            "debited": False,
            "note": (
                "Aucun Pragmadinar n'est débité pour connecter, synchroniser ou consulter ce tableau de bord."
            ),
        },
    }


# ── Global admin view ───────────────────────────────────────────────────────

CONNECTION_STATUSES = {s.value for s in DigitalConnection.Status}


def _freshness_bucket(last_synced_at, now):
    if last_synced_at is None:
        return "never"
    age = now - last_synced_at
    if age <= timedelta(hours=26):
        return "fresh"
    if age <= timedelta(hours=72):
        return "stale"
    return "outdated"


def build_admin_overview(*, category=None, wilaya=None, platform=None, status=None, include_demo=False, limit=200):
    from user.models import Category, Supplier

    now = timezone.now()
    connections = DigitalConnection.objects.all()
    if not include_demo:
        connections = connections.filter(is_demo=False)

    coverage = []
    for p in PLATFORM_ORDER:
        scoped = connections.filter(platform=p)
        coverage.append(
            {
                "platform": p,
                "label": PROVIDERS[p].label,
                "connectedSuppliers": scoped.filter(status=DigitalConnection.Status.ACTIVE)
                .values("supplier_id")
                .distinct()
                .count(),
                "connections": scoped.count(),
                "errors": scoped.filter(status=DigitalConnection.Status.ERROR).count(),
                "needsReauth": scoped.filter(status=DigitalConnection.Status.NEEDS_REAUTH).count(),
                "ineligible": scoped.filter(status=DigitalConnection.Status.INELIGIBLE).count(),
            }
        )

    freshness = {"fresh": 0, "stale": 0, "outdated": 0, "never": 0}
    for last in connections.values_list("last_synced_at", flat=True):
        freshness[_freshness_bucket(last, now)] += 1

    suppliers = Supplier.objects.filter(is_active=True)
    if category:
        suppliers = suppliers.filter(
            Q(interested_categories__id=category) | Q(interested_subcategories__parent_id=category)
        )
    if wilaya:
        suppliers = suppliers.filter(wilaya=wilaya)
    if status == "not_connected":
        excluded = connections.filter(platform=platform) if platform in PROVIDERS else connections
        suppliers = suppliers.exclude(pk__in=excluded.values("supplier_id"))
    else:
        scoped = connections
        if platform in PROVIDERS:
            scoped = scoped.filter(platform=platform)
        if status in CONNECTION_STATUSES:
            scoped = scoped.filter(status=status)
        suppliers = suppliers.filter(pk__in=scoped.values("supplier_id"))
    suppliers = suppliers.distinct().order_by("company_name")
    total = suppliers.count()

    page = list(suppliers.prefetch_related("interested_categories")[:limit])
    by_supplier = defaultdict(list)
    for c in connections.filter(supplier_id__in=[s.pk for s in page]):
        by_supplier[c.supplier_id].append(c)

    rows = []
    for supplier in page:
        platforms = {}
        for c in by_supplier.get(supplier.pk, []):
            entry = platforms.setdefault(
                c.platform, {"connections": 0, "statuses": [], "lastSyncedAt": None, "freshness": "never"}
            )
            entry["connections"] += 1
            entry["statuses"].append(c.status)
            if c.last_synced_at and (entry["lastSyncedAt"] is None or c.last_synced_at.isoformat() > entry["lastSyncedAt"]):
                entry["lastSyncedAt"] = c.last_synced_at.isoformat()
                entry["freshness"] = _freshness_bucket(c.last_synced_at, now)
        rows.append(
            {
                "id": supplier.pk,
                "companyName": supplier.company_name,
                "wilaya": supplier.get_wilaya_display() if supplier.wilaya else "",
                "categories": [c.name_fr or c.name_en for c in supplier.interested_categories.all()][:3],
                "platforms": platforms,
            }
        )

    return {
        "generatedAt": now.isoformat(),
        "coverage": coverage,
        "freshness": freshness,
        "filters": {"category": category, "wilaya": wilaya, "platform": platform, "status": status},
        "total": total,
        "rows": rows,
        "platforms": [{"key": p, "label": PROVIDERS[p].label} for p in PLATFORM_ORDER],
        "metrics": {
            p: [{"key": s.key, "label": s.label, "unit": s.unit} for s in METRICS[p]] for p in PLATFORM_ORDER
        },
        "wilayas": [{"key": k, "label": v} for k, v in Supplier.WILAYA_CHOICES],
        "categories": [
            {"id": c.pk, "label": c.name_fr or c.name_en}
            for c in Category.objects.filter(parent__isnull=True).order_by("sort_order", "name_en")
        ],
    }


class ComparisonError(ValueError):
    pass


def build_comparison(*, platform, metric, days, supplier_ids=None, include_demo=False):
    """Compare suppliers on ONE metric of ONE source over ONE identical window."""
    if platform not in PROVIDERS:
        raise ComparisonError("Choisissez une source unique pour comparer.")
    spec = metric_spec(platform, metric)
    if spec is None:
        raise ComparisonError("Cette métrique n'appartient pas à la source choisie.")

    connections = DigitalConnection.objects.filter(platform=platform).exclude(
        status=DigitalConnection.Status.INELIGIBLE
    ).select_related("supplier")
    if not include_demo:
        connections = connections.filter(is_demo=False)
    if supplier_ids:
        connections = connections.filter(supplier_id__in=supplier_ids)

    common_end = connections.aggregate(end=Max("data_end"))["end"]
    rows, excluded = [], []
    if common_end is not None:
        start = common_end - timedelta(days=days - 1)
        for connection in connections:
            result = metric_value(connection, spec, start, common_end)
            entry = {
                "supplierId": connection.supplier_id,
                "companyName": connection.supplier.company_name,
                "resource": connection.display_name or connection.external_id,
                "connectionId": connection.pk,
                "isDemo": connection.is_demo,
            }
            if result["status"] == "ok":
                rows.append({**entry, "value": result["value"]})
            else:
                excluded.append({**entry, "reason": result.get("reason", "")})
        rows.sort(key=lambda r: r["value"], reverse=True)

    return {
        "platform": platform,
        "platformLabel": PROVIDERS[platform].label,
        "metric": {"key": spec.key, "label": spec.label, "unit": spec.unit, "definition": spec.definition},
        "days": days,
        "periodStart": _iso(common_end - timedelta(days=days - 1)) if common_end else None,
        "periodEnd": _iso(common_end),
        "rows": rows,
        "excluded": excluded,
        "note": (
            "Comparaison limitée à une seule source, une seule métrique et une période identique. "
            "Les fournisseurs sans valeur exacte pour cette période sont exclus."
        ),
    }

