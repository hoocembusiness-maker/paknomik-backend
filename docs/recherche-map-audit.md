# Audit technique — recherche Paknomik (avant map hiérarchique)

## Modèles existants (réutilisés)

| Besoin prompt | Implémentation Paknomik |
|---------------|-------------------------|
| Category | `Category` level 1 (`parent` null) |
| Subcategory | `Category` level 2 (`parent` FK) — **pas** de table séparée |
| Product | `Product` + `subcategory`, `category`, `attribute_values` |
| Synonymes / tags | `SearchTag`, `Lexicon`, CSV exhaustif |
| Attributs techniques | `TechnicalAttribute`, `CategoryAttribute`, `AttributeOption` |
| Bobines | `catalog_bobines.py` + migration `0067` |

## Recherche actuelle

- Filtrage texte : `product_search.py` (tokens, tags, catégories, marchés).
- Expansion : `search_engine.py`, `packaging_taxonomy.py`, `search_normalize.py`.
- Suggestions : `search_suggestions.py`, `search_similarity.py`.
- Filtres UI : `category`, `subcategory_id`, attributs niveau 1.

## Manques identifiés (traités en 0069)

- Ancienne map plate `search_vocabulary` (mot → sous-catégorie unique).
- Pas de journal des recherches sans résultat.
- Pas d’admin CRUD pour la map hiérarchique.
- Termes ambigus (`film`) mal modélisés.

## Nouveaux modèles (0069)

- `SearchTerm` — mot normalisé, langue, type, `is_ambiguous`, poids.
- `SearchTermMapping` — lien vers catégorie / sous-catégorie / attribut + `confidence`, `context`.
- `SearchQueryMiss` — requêtes sans résultat produit.

## Fichiers clés

- `user/search_keyword_resolver.py` — résolution pondérée + règles CRM.
- `user/search_map_seed.py` — seed Bobines (idempotent).
- `user/api/search_map_views.py` — admin API (ultra-admin).
- `docs/vocabulaire-recherche-paknomik.md` — lexique legacy + API.

## Limites restantes

- Seeds hors Bobines (étiquettes, cartons, flacons…) à enrichir par vagues.
- Attributs dans la map : structure prête, peu de valeurs seedées.
- Pas de traduction automatique — entrées manuelles / CSV validés.
- Interface admin front : consultation + export ; CRUD map via API ultra-admin.

## Commandes

```bash
python manage.py migrate user 0069
python manage.py test user.tests_search_keyword_map user.tests_crm_pragmadinar.CategoryIntentSearchApiTests
```
