"""
Metric definitions for the Market Dashboards (Market Intelligence).

Every metric shown in a market dashboard is declared here, with its source, numerator,
denominator, aggregation, time dimension, supported granularities and privacy rule.
The API exposes this catalog so the UI never computes or redefines a metric.
"""

from __future__ import annotations

GRANULARITY_LABELS = {
    "day": "Jour",
    "week": "Semaine",
    "month": "Mois",
    "weekday": "Jour de la semaine",
    "geo": "Géographie (wilaya)",
    "category": "Catégorie",
}

TEMPORAL_GRANULARITIES = ("day", "week", "month")

SESSION_RULE = (
    "Session dérivée : suite d'événements acheteur/visiteur d'un même navigateur "
    "(cookie anonyme), interrompue après {gap} minutes d'inactivité. Les comptes "
    "fournisseurs et les robots sont exclus."
)

METRIC_DEFINITIONS: dict[str, dict] = {
    "sessions.count": {
        "name": "Sessions",
        "description": "Nombre de sessions de navigation sur la marketplace.",
        "source": "Event (événements buyer.*), regroupés en sessions",
        "numerator": "Sessions démarrées dans la période",
        "denominator": None,
        "aggregation": "Somme par unité de granularité ; moyenne, écart type, min, max sur les unités",
        "timeDimension": "Date locale (Africa/Algiers) du premier événement de la session",
        "granularities": ["day", "week", "month", "weekday", "geo"],
        "privacy": "Aucune donnée fournisseur. Wilayas sous le seuil regroupées en « Autres ».",
    },
    "sessions.duration": {
        "name": "Durée des sessions",
        "description": (
            "Temps observé entre le premier et le dernier événement de la session. "
            "Une session à un seul événement a une durée observée de 0."
        ),
        "source": "Event, sessions dérivées",
        "numerator": "Somme des durées observées",
        "denominator": "Nombre de sessions (pour la moyenne)",
        "aggregation": "Total, moyenne, écart type, min, max par session",
        "timeDimension": "Date locale du début de session",
        "granularities": ["day", "week", "month", "weekday", "geo"],
        "privacy": "Aucune donnée fournisseur.",
    },
    "sessions.conversion": {
        "name": "Comportement des sessions",
        "description": "Part des sessions contenant au moins une action donnée.",
        "source": "Event, sessions dérivées",
        "numerator": "Sessions contenant au moins un événement du type indiqué",
        "denominator": "Toutes les sessions de la période",
        "aggregation": "Ratio (en %)",
        "timeDimension": "Date locale du début de session",
        "granularities": [],
        "privacy": "Aucune donnée fournisseur.",
    },
    "search.count": {
        "name": "Recherches",
        "description": (
            "Recherches lancées sur la marketplace (une même requête répétée par le même "
            "visiteur en moins de 30 s compte une fois)."
        ),
        "source": "Event buyer.search",
        "numerator": "Recherches dans la période",
        "denominator": None,
        "aggregation": "Somme par unité de granularité ; moyenne, écart type, min, max sur les unités",
        "timeDimension": "Date locale de la recherche",
        "granularities": ["day", "week", "month", "weekday", "geo"],
        "privacy": "Aucune donnée fournisseur.",
    },
    "search.per_session": {
        "name": "Recherches par session",
        "description": "Nombre de recherches effectuées dans chaque session.",
        "source": "Event buyer.search, sessions dérivées",
        "numerator": "Recherches",
        "denominator": "Sessions de la période (y compris celles sans recherche)",
        "aggregation": "Moyenne, écart type, min, max par session ; histogramme",
        "timeDimension": "Date locale du début de session",
        "granularities": [],
        "privacy": "Aucune donnée fournisseur.",
    },
    "search.keywords": {
        "name": "Mots-clés recherchés",
        "description": (
            "Requêtes regroupées après normalisation lexicale (minuscules, accents, "
            "pluriels simples). Ce regroupement ne prétend pas mesurer une intention d'achat."
        ),
        "source": "Event buyer.search (+ system.appear_in_search, buyer.product_view)",
        "numerator": (
            "Volume = recherches ; Résultats moyens = moyenne de results_count ; "
            "Apparitions = fournisseurs affichés pour ces recherches ; "
            "Visites générées = fiches produit ouvertes depuis la recherche dans la même "
            "session, avant la recherche suivante"
        ),
        "denominator": "Résultats moyens : nombre de recherches du mot-clé",
        "aggregation": "Par mot-clé normalisé",
        "timeDimension": "Période sélectionnée",
        "granularities": [],
        "privacy": (
            "Mot-clé affiché seulement s'il est recherché dans au moins {min_population} "
            "sessions distinctes. Les recherches contenant un nom de fournisseur sont masquées."
        ),
    },
    "search.low_results": {
        "name": "Recherches à faible résultat",
        "description": "Mots-clés dont la moyenne de résultats est faible : piste de besoin non couvert.",
        "source": "Event buyer.search",
        "numerator": "Recherches du mot-clé",
        "denominator": "Moyenne de results_count ≤ {low_results}",
        "aggregation": "Par mot-clé normalisé",
        "timeDimension": "Période sélectionnée",
        "granularities": [],
        "privacy": "Même seuil d'anonymat que les mots-clés.",
    },
    "search.similarity": {
        "name": "Similarité lexicale",
        "description": (
            "Mots-clés proches à l'écrit (mots identiques après normalisation ou racine "
            "commune d'au moins 5 lettres). Ce n'est pas une similarité sémantique."
        ),
        "source": "Event buyer.search",
        "numerator": "Mots en commun",
        "denominator": "Mots distincts des deux requêtes",
        "aggregation": "Paires de mots-clés au-dessus du seuil d'anonymat",
        "timeDimension": "Période sélectionnée",
        "granularities": [],
        "privacy": "Même seuil d'anonymat que les mots-clés.",
    },
    "visibility.appearances": {
        "name": "Apparitions en recherche",
        "description": (
            "Nombre de fois où un fournisseur ou ses produits apparaissent dans les résultats "
            "d'une recherche acheteur (une apparition par fournisseur et par recherche)."
        ),
        "source": "Event system.appear_in_search",
        "numerator": "Apparitions dans la période",
        "denominator": None,
        "aggregation": "Somme par unité de granularité ; moyenne, écart type, min, max sur les unités",
        "timeDimension": "Date locale de la recherche",
        "granularities": ["day", "week", "month", "weekday", "geo"],
        "privacy": (
            "Total marché uniquement. Les recherches faites par des comptes fournisseurs et les "
            "robots sont exclues."
        ),
    },
    "visibility.visits": {
        "name": "Visites (fiches produit et boutiques)",
        "description": (
            "Ouvertures de fiches produit et visites de boutiques. Une boutique ouverte depuis "
            "une fiche produit n'est pas comptée comme visite de boutique."
        ),
        "source": "Event buyer.product_view, buyer.supplier_view",
        "numerator": "Visites dans la période, par source (recherche, accès direct)",
        "denominator": None,
        "aggregation": "Somme par unité de granularité ; moyenne, écart type, min, max sur les unités",
        "timeDimension": "Date locale de la visite",
        "granularities": ["day", "week", "month", "weekday", "geo"],
        "privacy": "Total marché uniquement. Comptes fournisseurs et robots exclus.",
    },
    "visibility.per_search": {
        "name": "Fournisseurs affichés par recherche",
        "description": "Nombre de fournisseurs distincts apparus dans chaque recherche.",
        "source": "Event system.appear_in_search (regroupé par recherche)",
        "numerator": "Apparitions",
        "denominator": "Recherches ayant affiché au moins un fournisseur",
        "aggregation": "Moyenne, écart type, min, max par recherche",
        "timeDimension": "Période sélectionnée",
        "granularities": [],
        "privacy": "Aucune donnée nominative.",
    },
    "visibility.search_visit_rate": {
        "name": "Taux de visite depuis la recherche",
        "description": (
            "Fiches produit ouvertes depuis la recherche rapportées aux apparitions dans les "
            "résultats produits. Indicateur approximatif : les deux compteurs ont des règles de "
            "dédoublonnage différentes."
        ),
        "source": "Event buyer.product_view (source = search), system.appear_in_search",
        "numerator": "Fiches produit ouvertes depuis la recherche",
        "denominator": "Apparitions dans les résultats produits",
        "aggregation": "Ratio (en %)",
        "timeDimension": "Période sélectionnée",
        "granularities": [],
        "privacy": "Total marché uniquement.",
    },
    "visibility.formats": {
        "name": "Apparitions par format d'affichage",
        "description": (
            "Répartition des apparitions entre affichage standard, cartes produit personnalisées "
            "et vitrines (Extra, Central, Mega). Enregistré depuis la mise en service du suivi ; "
            "les apparitions antérieures sont « non enregistrées »."
        ),
        "source": "Event system.appear_in_search (champ display_format)",
        "numerator": "Apparitions du format",
        "denominator": "Apparitions dont le format est enregistré",
        "aggregation": "Somme et part (en %) par format",
        "timeDimension": "Période sélectionnée",
        "granularities": [],
        "privacy": (
            "Un format n'est affiché que s'il concerne au moins {min_suppliers} fournisseurs "
            "distincts ; sinon il est regroupé ou masqué."
        ),
    },
    "visibility.concentration": {
        "name": "Concentration de la visibilité",
        "description": (
            "Part des apparitions captée par les 20 % de fournisseurs les plus visibles, et "
            "distribution des apparitions par fournisseur (sans aucun nom)."
        ),
        "source": "Event system.appear_in_search",
        "numerator": "Apparitions des 20 % de fournisseurs les plus affichés",
        "denominator": "Toutes les apparitions",
        "aggregation": "Ratio (en %) ; moyenne, médiane, écart type par fournisseur",
        "timeDimension": "Période sélectionnée",
        "granularities": [],
        "privacy": "Publié seulement si au moins {min_suppliers} fournisseurs sont apparus.",
    },
    "commercial.activity": {
        "name": "Activité commerciale du marché",
        "description": (
            "Demandes de devis boutique, demandes de devis marché, commandes nées sur Paknomik, "
            "demandes d'échantillon, nouvelles conversations et favoris."
        ),
        "source": (
            "RFQRequest, QuoteDemand, Order (hors ventes externes), SampleRequest, Message "
            "(premier message d'un couple acheteur/fournisseur), BuyerFavoriteSupplier/Product"
        ),
        "numerator": "Éléments créés dans la période",
        "denominator": None,
        "aggregation": "Somme par unité de granularité ; moyenne, écart type, min, max sur les unités",
        "timeDimension": "Date locale de création",
        "granularities": ["day", "week", "month", "weekday", "category"],
        "privacy": (
            "Une activité liée aux fournisseurs n'est publiée que si elle concerne au moins "
            "{min_suppliers} fournisseurs distincts (au moins {min_population} éléments pour les "
            "demandes marché). Même règle par catégorie ; sinon « Données insuffisantes »."
        ),
    },
    "commercial.status": {
        "name": "Statut des demandes et commandes",
        "description": "Statut actuel des commandes et des demandes de devis boutique créées dans la période.",
        "source": "Order.status, RFQRequest.status",
        "numerator": "Éléments dans le groupe de statuts",
        "denominator": "Éléments créés dans la période",
        "aggregation": "Ratio (en %)",
        "timeDimension": "Date locale de création",
        "granularities": [],
        "privacy": "Publié seulement si au moins {min_suppliers} fournisseurs distincts sont concernés.",
    },
}


def metric_definitions(
    *, gap_minutes: int, min_population: int, low_results: int, min_suppliers: int
) -> dict:
    """Definitions with the live thresholds substituted."""
    values = {
        "gap": gap_minutes,
        "min_population": min_population,
        "low_results": low_results,
        "min_suppliers": min_suppliers,
    }
    rendered = {}
    for key, definition in METRIC_DEFINITIONS.items():
        rendered[key] = {
            field: (value.format(**values) if isinstance(value, str) else value)
            for field, value in definition.items()
        }
        rendered[key]["key"] = key
    return {
        "sessionRule": SESSION_RULE.format(**values),
        "granularities": GRANULARITY_LABELS,
        "metrics": rendered,
    }
