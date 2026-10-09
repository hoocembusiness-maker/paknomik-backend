# Vocabulaire de recherche Paknomik (descriptif technique)

Ce document décrit **quels mots** la plateforme associe aux **sous-catégories** du catalogue, et comment ces associations alimentent les **signaux d'intérêt acheteur** (CRM `category_intent`).

## Objectif

Quand un acheteur connecté recherche sur Paknomik, une simple apparition dans les résultats **ne suffit plus**. Un signal CRM qualifié est émis si :

1. Il filtre **catégorie + sous-catégorie**, **ou**
2. Sa requête **`q`** contient un **mot-clé** présent dans le lexique et rattaché à une sous-catégorie, **ou**
3. Il enregistre une sous-catégorie dans **`/api/buyer/interest-subcategories/`**.

Dans ces cas, les coordonnées acheteur peuvent être révélées au fournisseur correspondant (`consent=True`), sans Pak-dating.

## Sources du lexique

| Source | Modèle / fichier | Contenu indexé |
|--------|------------------|----------------|
| Taxonomie | `Category` (niveau 2, `parent` non nul) | `name_en`, `name_fr`, `name_ar`, `slug`, `plain_description` |
| Mots-clés produits | `SearchTag` | `tag`, `tag_fr`, `tag_ar`, `synonyms`, `sub_category` |
| Référence CSV | `user/paknomik_mots_cles_produits_exhaustif.csv` | Import via `python manage.py import_search_tags_exhaustif` |
| Liens catalogue | `Product` ↔ `SearchTag` | Chaque tag porté par un produit **actif** hérite de la `subcategory` du produit |
| Variantes requête | `search_engine.expand_query_tokens` | Pluriels, synonymes packaging, translittérations AR→LAT |

## Normalisation

Toute correspondance passe par :

- `normalize_phrase` puis `fold_text` (voir `user/search_normalize.py`) ;
- tokenisation `tokenize_query` ;
- expansion des tokens pour la recherche (même pipeline que `product_search.py`).

## Résolution `q` → sous-catégories

Implémentation : `user/search_vocabulary.py` → `resolve_subcategories_from_query(query)`.

1. Construire l'ensemble des clés normalisées (phrase entière + tokens + variantes).
2. Lookup dans la table `term_normalized → {subcategory_id}`.
3. Fallback : inclusion partielle si clé ≥ 4 caractères.
4. Retourner les instances `Category` (sous-catégories).

Cache mémoire process (`lru_cache`) ; invalider avec `invalidate_vocabulary_cache()` après import massif de tags.

## API machine-readable

```
GET /api/search/vocabulary/
GET /api/search/vocabulary/?summary=1   # stats + règles, sans liste des entrées
```

Réponse JSON : version, sources, stats, règle CRM, et optionnellement `entries[]` :
 `{ termNormalized, subcategoryId, subcategoryLabel, categoryLabel, subcategorySlug }`.

## Extension du référentiel

1. **Nouvelle sous-catégorie** : créer la ligne `Category` (migration catalogue) → rechargement auto du cache au prochain process.
2. **Nouveaux mots-clés** : enrichir le CSV exhaustif ou créer des `SearchTag` (y compris via mots personnalisés fournisseur, promus en tags).
3. **Lier un mot à une sous-catégorie** : associer le tag à des produits de cette sous-catégorie, ou renseigner `SearchTag.sub_category` avec un libellé rapproché du catalogue.

## Hors périmètre du lexique sous-catégorie

- Segments « fin de produit » du CSV (`cible` = *Filtre : Segment fin produit*) : utilisés pour la **recherche produit** (marchés cibles), pas forcément mappés à une sous-catégorie emballage tant qu'aucun produit/tag ne crée le lien.
- `packaging_taxonomy.CONCEPT_TERMS` : expansion de requête uniquement ; le mapping CRM repose sur le lexique sous-catégorie ci-dessus.

## Tests

`user/tests_crm_pragmadinar.py` — scénarios recherche texte vs filtre catégorie + tests unitaires sur `resolve_subcategories_from_query`.
