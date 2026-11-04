"""API publique et administration du catalogue PakSuites."""

from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from buyer_space.catalogue_models import (
    SuiteComponentLine,
    SuiteEnsemble,
    SuiteFavorite,
    SuiteImage,
    SuiteSupplierOffer,
    SuiteTaxon,
    publish_blockers,
)
from buyer_space.suite_visuals import infer_visual_kind
from user.models import Category, QuoteDemand, QuoteDemandSelection, Supplier
from user.quote_market import initial_market_fields


def _page(request, default=12):
    try:
        page = max(1, int(request.query_params.get("page") or 1))
        size = min(24, max(1, int(request.query_params.get("pageSize") or default)))
    except ValueError:
        page, size = 1, default
    return page, size


def _staff(user):
    return user.is_authenticated and user.is_active and user.is_staff


def _buyer(user):
    return user.is_authenticated and user.is_active and not user.supplier_id


def _public_ensembles():
    return (
        SuiteEnsemble.objects.filter(editorial_status=SuiteEnsemble.Status.PUBLISHED)
        .select_related("function")
        .prefetch_related(
            "sectors",
            "images",
            Prefetch(
                "lines",
                queryset=SuiteComponentLine.objects.select_related(
                    "definition", "taxon", "taxon__parent"
                ).prefetch_related(
                    "offers__supplier",
                    "compatibilities",
                    "definition__images",
                    "taxon__images",
                ),
            ),
        )
    )


def _definition_image(definition) -> SuiteImage | None:
    if definition is None:
        return None
    images = list(definition.images.all())
    for image in images:
        if image.rights == SuiteImage.Rights.ALLOWED and image.file:
            return image
    return images[0] if images else None


def _taxon_image(taxon) -> SuiteImage | None:
    if taxon is None:
        return None
    images = list(taxon.images.all())
    for image in images:
        if image.rights == SuiteImage.Rights.ALLOWED and image.file:
            return image
    return images[0] if images else None


def _line_image(line: SuiteComponentLine) -> SuiteImage | None:
    image = _definition_image(line.definition)
    if image:
        return image
    return _taxon_image(line.taxon)


def _image_payload(image: SuiteImage, *, public: bool):
    show_file = image.rights == SuiteImage.Rights.ALLOWED and bool(image.file)
    payload = {
        "id": image.id,
        "viewType": image.view_type,
        "rights": image.rights,
        "illustrative": image.illustrative,
        "url": image.file.url if show_file else None,
        "caption": "" if image.illustrative else "Photographie de produit",
    }
    if public:
        if image.rights == SuiteImage.Rights.FORBIDDEN:
            return None
        if image.rights != SuiteImage.Rights.ALLOWED:
            payload["url"] = None
        return payload
    payload["remoteUrl"] = image.remote_url
    payload["pendingRights"] = image.rights != SuiteImage.Rights.ALLOWED
    return payload


def _kind_for(definition) -> str:
    kind = (definition.visual_kind or "").strip()
    return kind or infer_visual_kind(definition.code, definition.label)


def _visual_kinds(ensemble: SuiteEnsemble) -> list[str]:
    kinds: list[str] = []
    for line in ensemble.lines.all():
        kind = _kind_for(line.definition)
        if kind not in kinds:
            kinds.append(kind)
    return kinds


def _card(ensemble: SuiteEnsemble, *, favorite_ids=None):
    sectors = [sector.label_fr for sector in ensemble.sectors.all()]
    lines = list(ensemble.lines.all())
    raw_images = list(ensemble.images.all())
    overview = next(
        (
            item
            for item in (
                _image_payload(image, public=True)
                for image in raw_images
                if (image.view_type or "") == "overview"
            )
            if item and item.get("url")
        ),
        None,
    )
    images = [
        item
        for item in (_image_payload(image, public=True) for image in raw_images)
        if item and item.get("url")
    ]
    return {
        "code": ensemble.code,
        "slug": ensemble.slug,
        "name": ensemble.name,
        "description": ensemble.description,
        "priority": ensemble.priority,
        "sectors": sectors,
        "targetProduct": ensemble.target_product,
        "function": ensemble.function_label,
        "confidence": ensemble.confidence,
        "confidenceLabel": ensemble.get_confidence_display(),
        "componentCount": len(lines),
        "componentLabels": [line.definition.label for line in lines if line.definition_id],
        "visualKinds": _visual_kinds(ensemble),
        "image": overview or (images[0] if images else None),
        "placeholder": "",
        "saved": ensemble.id in favorite_ids if favorite_ids is not None else False,
    }


def _line_payload(line: SuiteComponentLine, *, public: bool):
    offers = []
    for offer in line.offers.all():
        if public and (not offer.supplier.is_active or offer.supplier.is_ghost):
            continue
        category = line.taxon.marketplace_category if line.taxon_id else None
        offers.append(
            {
                "supplierId": offer.supplier_id,
                "supplierName": offer.supplier.company_name,
                "canQuote": bool(category and category.parent_id),
                "canSample": True,
            }
        )
    compatibilities = [
        {
            "status": item.status,
            "statusLabel": item.get_status_display(),
            "justification": item.justification,
            "verifiedOn": item.verified_on.isoformat() if item.verified_on else None,
            "confirmed": item.status == "confirmed",
        }
        for item in line.compatibilities.all()
    ]
    line_image = _line_image(line)
    type_image = _taxon_image(line.taxon)
    return {
        "id": line.id,
        "label": line.definition.label,
        "visualKind": _kind_for(line.definition),
        "role": line.role,
        "level": line.level,
        "levelLabel": line.get_level_display() if line.level else "",
        "status": line.status,
        "statusLabel": line.get_status_display(),
        "material": line.material,
        "technicalNotes": line.technical_notes,
        "compatibilityNotes": line.compatibility_notes,
        "family": line.taxon.parent.label_fr if line.taxon_id and line.taxon.parent_id else "",
        "type": line.taxon.label_fr if line.taxon_id else line.definition.label,
        "image": _image_payload(line_image, public=True) if line_image else None,
        "typeImage": _image_payload(type_image, public=True) if type_image else None,
        "offers": offers,
        "availability": "offres" if offers else "a_rechercher",
        "compatibilities": compatibilities,
    }


def _detail(ensemble: SuiteEnsemble, *, public: bool, favorite_ids=None):
    payload = _card(ensemble, favorite_ids=favorite_ids)
    payload.update(
        {
            "pointsToConfirm": ensemble.points_to_confirm,
            "sourcedFacts": ensemble.sourced_facts,
            "algeriaRelevance": ensemble.algeria_relevance,
            "sources": [
                {
                    "code": source.code,
                    "name": source.name,
                    "url": source.url,
                    "infoType": source.info_type,
                    "market": source.market,
                }
                for source in ensemble.sources.all()
                if not public or source.is_public
            ],
            "lines": [
                _line_payload(line, public=public)
                for line in ensemble.lines.all()
            ],
            "variants": [
                {"tier": variant.tier, "label": variant.get_tier_display(), "summary": variant.summary}
                for variant in ensemble.variants.all()
            ],
            "images": [
                item
                for item in (_image_payload(image, public=public) for image in ensemble.images.all())
                if item
            ],
        }
    )
    if not public:
        payload["editorialStatus"] = ensemble.editorial_status
        payload["internalNote"] = ensemble.internal_note
        payload["publishBlockers"] = publish_blockers(ensemble)
    return payload


def _favorite_ids(user):
    if not _buyer(user):
        return set()
    return set(
        SuiteFavorite.objects.filter(user=user).values_list("ensemble_id", flat=True)
    )


class EnsembleListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        page, size = _page(request)
        qs = _public_ensembles()
        params = request.query_params
        query = (params.get("q") or "").strip()
        if query:
            qs = qs.filter(
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(target_product__icontains=query)
                | Q(lines__definition__label__icontains=query)
                | Q(lines__role__icontains=query)
            )
        sector = (params.get("sector") or "").strip()
        if sector:
            qs = qs.filter(Q(sectors__code=sector) | Q(sectors__label_fr__iexact=sector))
        family = (params.get("family") or "").strip()
        if family:
            code = family if family.startswith("F-") else f"F-{family}"
            qs = qs.filter(lines__taxon__parent__code=code)
        type_code = (params.get("type") or "").strip()
        if type_code:
            qs = qs.filter(Q(lines__taxon__code=type_code) | Q(lines__taxon__label_fr__icontains=type_code))
        function = (params.get("function") or "").strip()
        if function:
            qs = qs.filter(function_label__icontains=function)
        material = (params.get("material") or "").strip()
        if material:
            qs = qs.filter(lines__material__icontains=material)
        level = (params.get("level") or "").strip()
        if level:
            qs = qs.filter(lines__level=level)
        priority = (params.get("priority") or "").strip()
        if priority:
            qs = qs.filter(priority__iexact=priority)
        target = (params.get("target") or "").strip()
        if target:
            qs = qs.filter(target_product__icontains=target)
        qs = qs.distinct().order_by("code")
        count = qs.count()
        start = (page - 1) * size
        rows = list(qs[start : start + size])
        favorite_ids = _favorite_ids(request.user)
        return Response(
            {
                "count": count,
                "page": page,
                "pageSize": size,
                "totalPages": max(1, (count + size - 1) // size),
                "results": [_card(row, favorite_ids=favorite_ids) for row in rows],
            }
        )


def _detail_queryset():
    return (
        SuiteEnsemble.objects.filter(editorial_status=SuiteEnsemble.Status.PUBLISHED)
        .select_related("function")
        .prefetch_related(
            "sectors",
            "sources",
            "variants",
            "images",
            Prefetch(
                "lines",
                queryset=SuiteComponentLine.objects.select_related(
                    "definition", "taxon", "taxon__parent", "taxon__marketplace_category"
                ).prefetch_related(
                    "offers__supplier",
                    "compatibilities",
                    "definition__images",
                    "taxon__images",
                ),
            ),
        )
    )


class EnsembleDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, slug):
        ensemble = get_object_or_404(_detail_queryset(), slug=slug)
        return Response(_detail(ensemble, public=True, favorite_ids=_favorite_ids(request.user)))


class FilterView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        published = SuiteEnsemble.objects.filter(editorial_status=SuiteEnsemble.Status.PUBLISHED)
        sectors = SuiteTaxon.objects.filter(kind=SuiteTaxon.Kind.SECTOR, ensembles__in=published).distinct()
        families = SuiteTaxon.objects.filter(
            kind=SuiteTaxon.Kind.FAMILY, children__lines__ensemble__in=published
        ).distinct()
        functions = (
            published.exclude(function_label="")
            .values_list("function_label", flat=True)
            .distinct()
            .order_by("function_label")
        )
        return Response(
            {
                "sectors": [{"code": item.code, "label": item.label_fr} for item in sectors],
                "families": [{"code": item.code, "label": item.label_fr} for item in families],
                "functions": list(functions),
                "levels": [
                    {"code": "primary", "label": "Primaire"},
                    {"code": "secondary", "label": "Secondaire"},
                    {"code": "tertiary", "label": "Tertiaire"},
                ],
                "priorities": ["P1", "P2", "P3", "P4", "P5", "P6"],
            }
        )


class FavoriteListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _buyer(request.user):
            return Response({"error": "Réservé aux comptes acheteur."}, status=403)
        favorites = SuiteFavorite.objects.filter(user=request.user).select_related("ensemble")
        ensembles = _public_ensembles().filter(id__in=favorites.values("ensemble_id"))
        ids = {item.id for item in ensembles}
        return Response({"results": [_card(item, favorite_ids=ids) for item in ensembles]})


class FavoriteToggleView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, slug):
        if not _buyer(request.user):
            return Response({"error": "Réservé aux comptes acheteur."}, status=403)
        ensemble = get_object_or_404(
            SuiteEnsemble, slug=slug, editorial_status=SuiteEnsemble.Status.PUBLISHED
        )
        favorite = SuiteFavorite.objects.filter(user=request.user, ensemble=ensemble).first()
        if favorite:
            favorite.delete()
            saved = False
        else:
            SuiteFavorite.objects.create(user=request.user, ensemble=ensemble)
            saved = True
        return Response({"saved": saved, "slug": ensemble.slug})


class EnsembleQuickQuoteView(APIView):
    """Demande de devis en un clic pour les composants sélectionnés d'un ensemble."""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug):
        if not _buyer(request.user):
            return Response({"error": "Réservé aux comptes acheteur."}, status=403)
        ensemble = get_object_or_404(
            SuiteEnsemble,
            slug=slug,
            editorial_status=SuiteEnsemble.Status.PUBLISHED,
        )
        raw_ids = request.data.get("lineIds") or request.data.get("lineIds[]") or []
        if isinstance(raw_ids, str):
            raw_ids = [part for part in raw_ids.split(",") if part.strip()]
        try:
            line_ids = [int(item) for item in raw_ids]
        except (TypeError, ValueError):
            return Response({"error": "lineIds invalides."}, status=400)
        if not line_ids:
            line_ids = list(
                ensemble.lines.order_by("sort_order", "id").values_list("id", flat=True)
            )
        try:
            quantity = int(request.data.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 0

        lines = list(
            SuiteComponentLine.objects.filter(ensemble=ensemble, id__in=line_ids)
            .select_related(
                "ensemble",
                "definition",
                "taxon",
                "taxon__parent",
                "taxon__marketplace_category",
                "taxon__marketplace_category__parent",
            )
            .prefetch_related("offers__supplier")
            .order_by("sort_order", "id")
        )
        if len(lines) != len(set(line_ids)):
            return Response(
                {"error": "Un composant sélectionné n'appartient pas à cet ensemble."},
                status=400,
            )

        from buyer_space.catalogue_quotes import QuickQuoteError, quick_quotes_for_lines

        try:
            report = quick_quotes_for_lines(request.user, lines, quantity=quantity)
        except QuickQuoteError as exc:
            status_code = (
                status.HTTP_429_TOO_MANY_REQUESTS
                if exc.code == "public_cooldown"
                else status.HTTP_400_BAD_REQUEST
            )
            return Response({"error": exc.message, "code": exc.code}, status=status_code)
        return Response({"success": True, **report}, status=status.HTTP_201_CREATED)


class LineQuoteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, line_id):
        if not _buyer(request.user):
            return Response({"error": "Réservé aux comptes acheteur."}, status=403)
        line = get_object_or_404(
            SuiteComponentLine.objects.select_related(
                "ensemble", "definition", "taxon", "taxon__marketplace_category"
            ),
            pk=line_id,
            ensemble__editorial_status=SuiteEnsemble.Status.PUBLISHED,
        )
        supplier_id = request.data.get("supplierId")
        offer = (
            SuiteSupplierOffer.objects.select_related("supplier")
            .filter(line=line, supplier_id=supplier_id, supplier__is_active=True, supplier__is_ghost=False)
            .first()
        )
        if offer is None:
            return Response(
                {
                    "error": "Aucun fournisseur identifié. La disponibilité reste à rechercher.",
                    "code": "availability_pending",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        category = line.taxon.marketplace_category if line.taxon_id else None
        if category is None or category.parent_id is None:
            return Response(
                {
                    "error": "Ce composant n'est pas encore relié à une sous-catégorie du marché.",
                    "code": "category_unmapped",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        parent = Category.objects.filter(pk=category.parent_id).first()
        try:
            quantity = int(request.data.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 0
        if quantity < 1 or parent is None:
            return Response({"error": "Quantité ou catégorie invalide."}, status=400)
        from user.api.quote_demand_views import _log_quote_created, _notify_selected_suppliers, _serialize_demand

        demand = QuoteDemand.objects.create(
            buyer=request.user,
            kind=QuoteDemand.Kind.PRIVATE,
            category=parent,
            subcategory=category,
            quantity=quantity,
            description=(
                f"PakSuites {line.ensemble.code} — {line.ensemble.name}. "
                f"Composant : {line.definition.label} ({line.role})."
            ),
            wilaya=(request.user.wilaya or "").strip(),
            catalogue_line=line,
            **initial_market_fields(kind=QuoteDemand.Kind.PRIVATE, has_selections=True),
        )
        QuoteDemandSelection.objects.create(demand=demand, supplier=offer.supplier)
        _notify_selected_suppliers(
            demand, [offer.supplier], origin="paksuites", origin_label=line.ensemble.name
        )
        _log_quote_created(demand, [offer.supplier.id])
        return Response(
            {
                "success": True,
                "id": demand.id,
                "demand": _serialize_demand(demand, request, viewer="buyer"),
            },
            status=status.HTTP_201_CREATED,
        )


class AdminEnsembleListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _staff(request.user):
            return Response({"error": "Réservé aux gestionnaires."}, status=403)
        qs = SuiteEnsemble.objects.all().prefetch_related("lines__offers", "images", "sources")
        gap = (request.query_params.get("gap") or "").strip()
        if gap == "no_supplier":
            qs = qs.filter(lines__offers__isnull=True).distinct()
        elif gap == "rights":
            qs = qs.filter(images__rights__in=["to_verify", "ask", "forbidden"]).distinct()
        elif gap == "unpublished":
            qs = qs.exclude(editorial_status=SuiteEnsemble.Status.PUBLISHED)
        return Response(
            {
                "results": [
                    {
                        "code": item.code,
                        "slug": item.slug,
                        "name": item.name,
                        "editorialStatus": item.editorial_status,
                        "confidence": item.confidence,
                        "componentCount": item.lines.count(),
                        "withoutSupplier": item.lines.annotate(n=Count("offers")).filter(n=0).count(),
                        "rightsPending": item.images.exclude(rights=SuiteImage.Rights.ALLOWED).count(),
                        "publishBlockers": publish_blockers(item),
                    }
                    for item in qs.order_by("code")
                ]
            }
        )


class AdminEnsembleDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, code):
        if not _staff(request.user):
            return Response({"error": "Réservé aux gestionnaires."}, status=403)
        ensemble = get_object_or_404(SuiteEnsemble, code=code)
        return Response(_detail(ensemble, public=False))

    def patch(self, request, code):
        if not _staff(request.user):
            return Response({"error": "Réservé aux gestionnaires."}, status=403)
        ensemble = get_object_or_404(SuiteEnsemble, code=code)
        data = request.data
        for field, attr, limit in (
            ("name", "name", 240),
            ("description", "description", 8000),
            ("pointsToConfirm", "points_to_confirm", 8000),
            ("internalNote", "internal_note", 8000),
            ("confidence", "confidence", 16),
        ):
            if field in data:
                setattr(ensemble, attr, str(data.get(field) or "").strip()[:limit])
        if "editorialStatus" in data:
            next_status = str(data.get("editorialStatus") or "").strip()
            allowed = {choice for choice, _label in SuiteEnsemble.Status.choices}
            if next_status not in allowed:
                return Response({"error": "Statut éditorial inconnu."}, status=400)
            if next_status == SuiteEnsemble.Status.PUBLISHED:
                blockers = publish_blockers(ensemble)
                if blockers:
                    return Response({"error": blockers[0], "blockers": blockers}, status=400)
            ensemble.editorial_status = next_status
        orders = data.get("lines")
        if isinstance(orders, list):
            for entry in orders:
                if not isinstance(entry, dict) or "id" not in entry:
                    continue
                line = ensemble.lines.filter(pk=entry["id"]).first()
                if line is None:
                    continue
                if "sortOrder" in entry:
                    line.sort_order = int(entry["sortOrder"])
                if "status" in entry and entry["status"] in dict(SuiteComponentLine.Status.choices):
                    line.status = entry["status"]
                line.save()
        ensemble.save()
        return Response(_detail(ensemble, public=False))
