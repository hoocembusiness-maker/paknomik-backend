"""Admin API for hierarchical search term map and zero-result queries."""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import rbac
from user.api.rbac_views import PermissionScopedView, _permission_denied
from user.models import Category, SearchQueryMiss, SearchTerm, SearchTermMapping
from user.search_map_seed import seed_bobines_search_map
from user.search_term_normalize import normalize_search_term


class AdminSearchMapListView(PermissionScopedView):
    """List / create search terms (ultra-admin)."""

    def check(self, request, permission=None):
        try:
            rbac.require_pricing_manage(request.user)
        except Exception as exc:
            return _permission_denied(exc)
        return None

    def get(self, request):
        denied = self.check(request)
        if denied:
            return denied
        q = (request.query_params.get("q") or "").strip().lower()
        qs = SearchTerm.objects.filter(is_active=True).prefetch_related("mappings")
        if q:
            qs = qs.filter(term__icontains=q) | qs.filter(normalized_term__icontains=q)
        terms = []
        for term in qs.order_by("-priority", "term")[:500]:
            terms.append(
                {
                    "id": term.id,
                    "term": term.term,
                    "normalizedTerm": term.normalized_term,
                    "language": term.language,
                    "termType": term.term_type,
                    "isAmbiguous": term.is_ambiguous,
                    "priority": term.priority,
                    "weight": term.weight,
                    "mappings": [
                        {
                            "id": m.id,
                            "categoryId": m.category_id,
                            "subcategoryId": m.subcategory_id,
                            "attributeId": m.attribute_id,
                            "attributeValue": m.attribute_value,
                            "confidence": m.confidence,
                            "priority": m.priority,
                            "context": m.context,
                            "isActive": m.is_active,
                        }
                        for m in term.mappings.all()
                    ],
                }
            )
        return Response({"terms": terms})

    def post(self, request):
        denied = self.check(request)
        if denied:
            return denied
        term_text = (request.data.get("term") or "").strip()
        if not term_text:
            return Response(
                {"error": "term is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        term = SearchTerm.objects.create(
            term=term_text,
            normalized_term=normalize_search_term(term_text),
            language=request.data.get("language") or SearchTerm.Language.FR,
            term_type=request.data.get("termType") or SearchTerm.TermType.CANONICAL,
            is_ambiguous=bool(request.data.get("isAmbiguous")),
            priority=int(request.data.get("priority") or 50),
            weight=int(request.data.get("weight") or 50),
            is_active=True,
        )
        sub_id = request.data.get("subcategoryId")
        cat_id = request.data.get("categoryId")
        if sub_id:
            sub = Category.objects.filter(pk=sub_id, parent__isnull=False).first()
            if sub:
                SearchTermMapping.objects.create(
                    search_term=term,
                    category=sub.parent,
                    subcategory=sub,
                    confidence=int(request.data.get("confidence") or 80),
                    priority=int(request.data.get("mappingPriority") or 50),
                    context=(request.data.get("context") or "")[:120],
                )
        elif cat_id:
            cat = Category.objects.filter(pk=cat_id, parent__isnull=True).first()
            if cat:
                SearchTermMapping.objects.create(
                    search_term=term,
                    category=cat,
                    confidence=int(request.data.get("confidence") or 70),
                    priority=int(request.data.get("mappingPriority") or 50),
                    context=(request.data.get("context") or "")[:120],
                )
        rbac.log_audit(
            request.user,
            "search_map.term.create",
            entity_type="SearchTerm",
            entity_id=term.pk,
            new_value={"term": term.term},
            request=request,
        )
        return Response({"id": term.pk}, status=status.HTTP_201_CREATED)


class AdminSearchMapTermView(PermissionScopedView):
    def check(self, request, permission=None):
        try:
            rbac.require_pricing_manage(request.user)
        except Exception as exc:
            return _permission_denied(exc)
        return None

    def patch(self, request, pk):
        denied = self.check(request)
        if denied:
            return denied
        term = SearchTerm.objects.filter(pk=pk).first()
        if term is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if "isActive" in request.data:
            term.is_active = bool(request.data.get("isActive"))
        if "isAmbiguous" in request.data:
            term.is_ambiguous = bool(request.data.get("isAmbiguous"))
        if "priority" in request.data:
            term.priority = int(request.data.get("priority") or term.priority)
        if "weight" in request.data:
            term.weight = int(request.data.get("weight") or term.weight)
        term.save()
        rbac.log_audit(
            request.user,
            "search_map.term.update",
            entity_type="SearchTerm",
            entity_id=term.pk,
            new_value={"isActive": term.is_active},
            request=request,
        )
        return Response({"updated": True})


class AdminSearchMissListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_staff:
            return Response(status=status.HTTP_403_FORBIDDEN)
        qs = SearchQueryMiss.objects.all().order_by("-created_at")[:200]
        return Response(
            {
                "results": [
                    {
                        "id": row.id,
                        "originalQuery": row.original_query,
                        "normalizedQuery": row.normalized_query,
                        "languageHint": row.language_hint,
                        "resultsCount": row.results_count,
                        "suggestedSubcategoryId": row.suggested_subcategory_id,
                        "buyerId": row.buyer_id,
                        "source": row.source,
                        "createdAt": row.created_at,
                    }
                    for row in qs
                ]
            }
        )


class AdminSearchMapReseedView(PermissionScopedView):
    def check(self, request, permission=None):
        try:
            rbac.require_pricing_manage(request.user)
        except Exception as exc:
            return _permission_denied(exc)
        return None

    def post(self, request):
        denied = self.check(request)
        if denied:
            return denied
        stats = seed_bobines_search_map()
        return Response({"stats": stats})
