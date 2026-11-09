"""Catalogue taxonomy API. The historical /api/products/ list is unchanged."""

from __future__ import annotations

from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user.catalog_import import import_workbook, template_bytes
from user.catalog_products import save_gamme
from user.catalog_search import parse_filters, quote_prefill, search_catalog
from user.catalog_taxonomy import (
    PAGE_SIZE,
    default_offer_mode,
    public_config,
    session_slice_for,
)
from user.entitlements import assert_can_add_product
from user.events import log_search_with_appearances
from user.models import Product, ProductFamily, ProductType
from user.scoring import handle_product_added


def _supplier_or_403(request):
    if not getattr(request.user, "supplier_id", None):
        return None, Response(
            {"error": "Supplier account required"},
            status=status.HTTP_403_FORBIDDEN,
        )
    return request.user.supplier, None


def _payload(request):
    if hasattr(request.data, "dict"):
        data = request.data.dict()
    else:
        data = dict(request.data)
    for key in ("attributes", "variants"):
        value = data.get(key)
        if isinstance(value, str):
            import json

            try:
                data[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return data


def _type_payload(product_type: ProductType) -> dict:
    return {
        "id": product_type.pk,
        "slug": product_type.slug,
        "label": product_type.name_fr,
        "labelEn": product_type.name_en,
        "labelAr": product_type.name_ar,
        "familyId": product_type.family_id,
        "familyLabel": product_type.family.name_fr,
    }


class CatalogMetaView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        payload = public_config()
        kind = ""
        if request.user.is_authenticated and getattr(request.user, "supplier_id", None):
            kind = request.user.supplier.kind or ""
        payload["supplierKind"] = kind
        payload["defaultOfferMode"] = default_offer_mode(kind) if kind else ""
        payload["offerModes"] = [
            {"value": "STOCK", "label": "À vendre"},
            {"value": "SUR_MESURE", "label": "Sur mesure"},
        ]
        return Response(payload)


class CatalogTypeListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        family_id = request.query_params.get("familyId")
        qs = ProductType.objects.select_related("family")
        if family_id and str(family_id).isdigit():
            qs = qs.filter(family_id=int(family_id))
        if query:
            qs = qs.filter(
                Q(name_fr__icontains=query)
                | Q(name_en__icontains=query)
                | Q(slug__icontains=query)
                | Q(family__name_fr__icontains=query)
            )
        families = [
            {
                "id": family.pk,
                "slug": family.slug,
                "label": family.name_fr,
            }
            for family in ProductFamily.objects.all()
        ]
        return Response(
            {
                "families": families,
                "types": [_type_payload(row) for row in qs[:12]],
            }
        )


class CatalogTypeSchemaView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk):
        product_type = ProductType.objects.select_related("family").filter(pk=pk).first()
        if product_type is None:
            return Response({"error": "Type introuvable."}, status=status.HTTP_404_NOT_FOUND)
        attributes = list(product_type.attribute_schema or [])
        return Response(
            {
                **_type_payload(product_type),
                "attributes": attributes,
                "filters": [item for item in attributes if item.get("filterable")],
                "offerModeFilter": [
                    {"value": "STOCK", "label": "À vendre"},
                    {"value": "SUR_MESURE", "label": "Sur mesure"},
                ],
            }
        )


class CatalogQuotePrefillView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk):
        product_type = ProductType.objects.select_related("family").filter(pk=pk).first()
        if product_type is None:
            return Response({"error": "Type introuvable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(quote_prefill(product_type))


class CatalogSearchView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            filters = parse_filters(request.query_params.get("filters"))
        except ValueError as exc:
            return Response({"filters": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        for key, value in request.query_params.items():
            if key.startswith("attr_"):
                filters[key[5:]] = value
        type_id = request.query_params.get("typeId")
        family_id = request.query_params.get("familyId")
        page = _positive(request.query_params.get("page"), 1)
        page_size = min(_positive(request.query_params.get("pageSize"), PAGE_SIZE), 48)
        slice_raw = request.query_params.get("sessionSlice")
        session_slice = (
            int(slice_raw)
            if slice_raw is not None and str(slice_raw).lstrip("-").isdigit()
            else session_slice_for(timezone.localtime())
        )
        payload = search_catalog(
            query=(request.query_params.get("q") or "").strip(),
            type_id=int(type_id) if type_id and str(type_id).isdigit() else None,
            family_id=int(family_id) if family_id and str(family_id).isdigit() else None,
            offer_mode=(request.query_params.get("offerMode") or "").strip(),
            filters=filters,
            page=page,
            page_size=page_size,
            session_slice=session_slice,
        )
        query = (request.query_params.get("q") or "").strip()
        if query:
            log_search_with_appearances(
                request,
                search_query=query,
                supplier_ids=[row["supplierId"] for row in payload["results"]],
                results_count=payload["total"],
                source="catalog_taxonomy",
                category=request.query_params.get("typeId"),
            )
        return Response(payload)


class CatalogProductView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        supplier, denied = _supplier_or_403(request)
        if denied:
            return denied
        assert_can_add_product(supplier)
        try:
            product = save_gamme(
                supplier=supplier,
                payload=_payload(request),
                files=request.FILES,
                actor=request.user,
            )
        except Exception as exc:
            return _error(exc)
        handle_product_added(supplier.pk, product.pk, actor_user=request.user)
        product = _reload(supplier.pk, product.pk)
        return Response(_gamme(product, request), status=status.HTTP_201_CREATED)


class CatalogProductDetailView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def patch(self, request, pk):
        supplier, denied = _supplier_or_403(request)
        if denied:
            return denied
        product = Product.objects.filter(pk=pk, supplier=supplier).first()
        if product is None:
            return Response({"error": "Product not found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            product = save_gamme(
                supplier=supplier,
                payload=_payload(request),
                files=request.FILES,
                actor=request.user,
                instance=product,
            )
        except Exception as exc:
            return _error(exc)
        return Response(_gamme(_reload(supplier.pk, product.pk), request))


class CatalogImportTemplateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        _supplier, denied = _supplier_or_403(request)
        if denied:
            return denied
        product_type = ProductType.objects.filter(pk=pk).first()
        if product_type is None:
            return Response({"error": "Type introuvable."}, status=status.HTTP_404_NOT_FOUND)
        response = HttpResponse(
            template_bytes(product_type),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="gabarit-{product_type.slug}.xlsx"'
        )
        return response


class CatalogImportView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        supplier, denied = _supplier_or_403(request)
        if denied:
            return denied
        product_type = ProductType.objects.filter(pk=pk).first()
        if product_type is None:
            return Response({"error": "Type introuvable."}, status=status.HTTP_404_NOT_FOUND)
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"file": "Fichier manquant."}, status=status.HTTP_400_BAD_REQUEST)
        assert_can_add_product(supplier)
        result = import_workbook(
            supplier=supplier,
            product_type=product_type,
            upload=upload,
            actor=request.user,
        )
        for product_id in result.pop("ids", []):
            handle_product_added(supplier.pk, product_id, actor_user=request.user)
        code = status.HTTP_201_CREATED if result["created"] and not result["errors"] else (
            status.HTTP_400_BAD_REQUEST if result["errors"] else status.HTTP_200_OK
        )
        return Response(result, status=code)


def _positive(raw, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _error(exc):
    from rest_framework.exceptions import ValidationError

    if isinstance(exc, ValidationError):
        return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
    raise exc


def _reload(supplier_id, product_id):
    return (
        Product.objects.filter(pk=product_id, supplier_id=supplier_id)
        .select_related("product_type", "product_type__family", "category", "subcategory")
        .prefetch_related("variants", "images")
        .get()
    )


def _gamme(product: Product, request) -> dict:
    images = []
    for image in product.images.all():
        url = image.image.url
        if request is not None:
            url = request.build_absolute_uri(url)
        images.append({"id": image.pk, "url": url, "isPrimary": image.is_primary})
    return {
        "id": product.pk,
        "name": product.name,
        "description": product.description,
        "productTypeId": product.product_type_id,
        "offerMode": product.offer_mode,
        "mixed": product.is_mixed,
        "flagship": product.is_flagship,
        "publish": product.is_active,
        "attributes": product.attributes or {},
        "categoryId": product.category_id,
        "subcategoryId": product.subcategory_id,
        "listingType": product.listing_type,
        "images": images,
        "variants": [
            {
                "id": row.pk,
                "sku": row.sku,
                "axes": row.axes or {},
                "priceFrom": None if row.price_from is None else f"{row.price_from:.2f}",
                "moq": row.moq,
                "available": row.is_available,
            }
            for row in product.variants.all()
        ],
    }
