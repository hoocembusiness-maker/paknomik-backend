"""
Main REST API views: login, products, orders, messages, supplier
dashboard, RFQ, and most frontend features. Returns JSON.
"""

import logging
from datetime import date, datetime, timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views import View
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
import json
from urllib.parse import quote

from user.category_tree import (
    count_selectable_subcategories,
    selectable_subcategories,
    subcategory_belongs_to_category,
)
from user.product_similarity import find_similar_products
from user.product_search import filter_products_by_text
from user.search_suggestions import build_search_suggestions
from user.supplier_search import annotate_boutique_search_priority, filter_suppliers_by_text
from user.product_markets import TARGET_MARKET_OPTIONS
from user.analytics import build_supplier_geo_breakdown
from user.category_benchmark import build_category_benchmark
from user.supplier_dashboard_metrics import build_supplier_dashboard_sections
from user.reporting_period import resolve_report_period
from user.entitlements import assert_can_add_product
from user.events import (
    handle_auth_login,
    log_auth_logout,
    log_buyer_event,
    log_seller_event,
    log_search_with_appearances,
    log_product_compare,
    log_supplier_product_view,
    log_supplier_profile_view,
)
from user import crm as crm_service
from user import rbac
from user.commission import register_order_commission
from user.order_reviews import (
    OrderReviewError,
    annotate_delivery_reviews,
    delivery_review_stats,
    public_review_rows,
    save_order_review,
    supplier_rating_is_public,
)
from user.order_workflow import (
    BUYER_CANCELLABLE,
    SUPPLIER_ARCHIVABLE,
    SUPPLIER_REOPEN_TRANSITIONS,
    OrderAmountError,
    OrderTransitionError,
    order_summary_note,
    record_status_event,
    status_label as order_status_label,
    supplier_allowed_transitions,
    transition_order,
    update_order_unit_price,
)
from user.proposal_messaging import (
    create_proposal_notice_message,
    resolve_buyer_for_order,
    resolve_buyer_for_rfq,
)
from user.photo_uploads import clean_photos, sanitized_photos, save_photos
from user.product_pdf import product_datasheet_pdf_bytes
from user.proposal_commerce import quote_response_snapshot
from user.proposal_pdf import (
    PDFRenderer,
    build_proposal_pdf_context,
    html_to_pdf_bytes,
    render_proposal_pdf_html,
)
from user.scoring import (
    ensure_supplier_score,
    get_supplier_rank,
    handle_order_created,
    handle_product_added,
    handle_product_deleted,
    handle_product_edited,
    handle_profile_updated,
    handle_rfq_created,
    handle_supplier_created,
    handle_supplier_favorited,
    handle_supplier_message_reply,
    award_profile_bonuses_for_supplier,
)

logger = logging.getLogger(__name__)
from user.models import (
    AppNotification,
    Category,
    CategoryAttribute,
    Event,
    BuyerFavoriteSupplier,
    BuyerFavoriteProduct,
    BuyerSearchHistory,
    CrmContact,
    NewsletterSubscriber,
    Product,
    QuoteDemand,
    QuoteDemandPhoto,
    RFQRequest,
    RFQRequestPhoto,
    QuoteResponse,
    OrderResponse,
    SearchTag,
    Supplier,
    Order,
    TechnicalAttribute,
    TenderCall,
    TradeScope,
    TenderCallAttributeValue,
    TenderCallNotification,
    Message,
)
from user.api.serializers import (
    CategorySerializer,
    ProductListSerializer,
    ProductOrderDetailSerializer,
    OrderCreateSerializer,
    SearchTagSerializer,
    SubCategorySerializer,
    SupplierContactSerializer,
    SupplierDetailSerializer,
    SupplierListSerializer,
    SupplierMessageCreateSerializer,
    SupplierMessageSerializer,
    SupplierOrderSerializer,
    SupplierProductFormSerializer,
    SupplierProductSerializer,
    SupplierRfqSerializer,
    SupplierRfqRespondSerializer,
    SupplierOrderRespondSerializer,
    QuoteResponseSerializer,
    OrderResponseSerializer,
    TechnicalAttributeSerializer,
    BuyerProfileDetailSerializer,
    BuyerUpgradeSupplierSerializer,
    BuyerOrderSerializer,
    BuyerContactSerializer,
    BuyerSearchHistorySerializer,
    BuyerFavoriteSupplierSerializer,
    SupplierProfileEditSerializer,
    SupplierSettingsSerializer,
    SupplierFavoriteActivitySerializer,
    contact_display_name,
    ensure_supplier_settings,
    get_supplier_search_tag_limit,
    get_supplier_product_image_limit,
    get_supplier_subscription_tier,
    render_proposal_blocks_to_text,
)

User = get_user_model()


def _auth_payload(user):
    refresh = RefreshToken.for_user(user)
    name = _buyer_display_name(user) or user.username
    return {
        "token": str(refresh.access_token),
        "user": {
            "id": user.id,
            "email": user.email,
            "name": name,
            "company": user.company_name,
            "isSupplier": bool(user.supplier_id),
            "supplierId": user.supplier_id,
            "isStaff": bool(user.is_staff),
        },
    }


def _buyer_display_name(user):
    """Buyer full name for profile UI (never substitute login username/email)."""
    if user.first_name:
        return user.first_name.strip()
    full = user.get_full_name().strip()
    return full


class PlatformStatsView(APIView):
    def get(self, request):
        supplier_count = Supplier.objects.filter(is_active=True, is_ghost=False).count()
        product_count = Product.objects.filter(
            is_active=True, supplier__is_active=True, supplier__is_ghost=False
        ).count()
        category_count = Category.objects.count()
        country_count = (
            Supplier.objects.filter(is_active=True, is_ghost=False, wilaya__isnull=False)
            .values("wilaya")
            .distinct()
            .count()
        ) or 1
        return Response(
            {
                "supplierCount": supplier_count,
                "productCount": product_count,
                "categoryCount": category_count,
                "countryCount": country_count,
            }
        )


class CategoryListView(APIView):
    def get(self, request):
        from user.taxonomy_v3 import ACTIVE_FAMILY_SLUGS, ACTIVE_TYPE_SLUGS

        qs = Category.objects.filter(
            parent__isnull=True, slug__in=ACTIVE_FAMILY_SLUGS
        ).annotate(
            active_product_count=Count(
                "products",
                filter=Q(
                    products__is_active=True,
                    products__supplier__is_active=True,
                    products__supplier__is_ghost=False,
                ),
                distinct=True,
            ),
            subcategory_count=Count(
                "children",
                filter=Q(children__slug__in=ACTIVE_TYPE_SLUGS),
                distinct=True,
            ),
            supplier_count=Count(
                "products__supplier",
                filter=Q(
                    products__is_active=True,
                    products__supplier__is_active=True,
                    products__supplier__is_ghost=False,
                ),
                distinct=True,
            ),
            attribute_count=Count("category_attributes", distinct=True),
        ).order_by("sort_order", "name_en")
        return Response(
            CategorySerializer(qs, many=True, context={"request": request}).data
        )


class SubCategoryListView(APIView):
    def get(self, request):
        parent = request.query_params.get("parent")
        parent_category = None

        if parent:
            if parent.isdigit():
                parent_category = Category.objects.filter(
                    pk=int(parent), parent__isnull=True
                ).first()
            else:
                parent_category = Category.objects.filter(
                    slug=parent, parent__isnull=True
                ).first()

        rows = selectable_subcategories(parent_category)
        ids = [row.id for row in rows]
        counts = {
            row.id: row.active_product_count
            for row in Category.objects.filter(pk__in=ids).annotate(
                active_product_count=Count(
                    "products_subcategory",
                    filter=Q(
                        products_subcategory__is_active=True,
                        products_subcategory__supplier__is_ghost=False,
                    ),
                    distinct=True,
                )
            )
        }
        for row in rows:
            row.active_product_count = counts.get(row.id, 0)

        return Response(
            SubCategorySerializer(rows, many=True, context={"request": request}).data
        )


class SupplierListView(APIView):
    def get(self, request):
        qs = annotate_delivery_reviews(
            Supplier.objects.filter(is_active=True, is_ghost=False)
        ).annotate(
            active_product_count=Count(
                "products",
                filter=Q(products__is_active=True),
                distinct=True,
            ),
            order_count=Count("orders", distinct=True),
        ).select_related("settings").prefetch_related(
            "products__category",
            "interested_categories",
            "interested_subcategories",
        )

        q = request.query_params.get("q")
        if q:
            qs = filter_suppliers_by_text(qs, q)

        category = request.query_params.get("category")
        if category:
            qs = qs.filter(
                products__category__name_en__icontains=category,
                products__is_active=True,
            ).distinct()

        subcategory_id = request.query_params.get("subcategory_id")
        if subcategory_id and str(subcategory_id).isdigit():
            qs = qs.filter(
                products__subcategory_id=int(subcategory_id),
                products__is_active=True,
            ).distinct()

        country = request.query_params.get("country")
        if country and country.lower() not in ("algeria", "dz", "all"):
            qs = qs.none()

        if request.query_params.get("verified") == "true":
            qs = qs.filter(is_active=True)

        if request.query_params.get("featured") == "true":
            qs = qs.filter(is_featured=True)
        else:
            qs = qs.filter(is_featured=False)

        qs = annotate_boutique_search_priority(qs)
        if not q:
            qs, has_fav_rank = _annotate_buyer_favorite_suppliers(qs, request.user)
            if has_fav_rank:
                qs = qs.order_by(
                    "_favorite_rank",
                    "_boutique_search_priority",
                    "-is_featured",
                    "company_name",
                )
            else:
                qs = qs.order_by("_boutique_search_priority", "-is_featured", "company_name")
        else:
            qs, has_fav_rank = _annotate_buyer_favorite_suppliers(qs, request.user)
            if has_fav_rank:
                qs = qs.order_by("_favorite_rank", "_boutique_search_priority", "company_name")
            supplier_ids = list(qs.values_list("id", flat=True))
            log_search_with_appearances(
                request,
                search_query=q,
                supplier_ids=supplier_ids,
                results_count=len(supplier_ids),
                source="suppliers",
                category=category or None,
            )
        supplier_ids_for_intent = list(qs.values_list("id", flat=True))
        _maybe_sync_category_intent_from_listing(request, supplier_ids_for_intent)
        return Response(SupplierListSerializer(qs, many=True).data)


class SupplierDetailView(APIView):
    def get(self, request, pk):
        try:
            supplier = (
                annotate_delivery_reviews(
                    Supplier.objects.filter(is_active=True, is_ghost=False)
                )
                .prefetch_related(
                    "products",
                    "products__category",
                    "products__subcategory",
                    "products__material",
                    "products__images",
                    "products__search_tags",
                    "products__attribute_values__attribute",
                    "interested_categories",
                    "interested_subcategories",
                )
                .select_related("settings")
                .get(pk=pk)
            )
        except Supplier.DoesNotExist:
            return Response({"error": "Supplier not found"}, status=status.HTTP_404_NOT_FOUND)

        supplier.active_products = supplier.products.filter(
            is_active=True
        ).prefetch_related("images")[:12]
        source = request.query_params.get("source") or "direct"
        track = request.query_params.get("track", "1").strip().lower()
        if track not in {"0", "false", "no"}:
            log_supplier_profile_view(request, supplier.pk, source=source)
        return Response(
            SupplierDetailSerializer(
                supplier, context={"request": request}
            ).data
        )


class SupplierReviewsView(APIView):
    """Buyer ratings for a boutique: date, buyer name, comment, and stars.

    Hidden from the public when the supplier turns ratings off. The supplier
    account can still read its own history.
    """

    def get(self, request, pk):
        supplier = (
            Supplier.objects.filter(pk=pk, is_active=True, is_ghost=False)
            .select_related("settings")
            .first()
        )
        if supplier is None:
            return Response({"error": "Supplier not found"}, status=status.HTTP_404_NOT_FOUND)

        visible = supplier_rating_is_public(supplier)
        is_owner = (
            request.user.is_authenticated
            and getattr(request.user, "supplier_id", None) == supplier.id
        )
        if not visible and not is_owner:
            return Response(
                {"showRating": False, "rating": 0, "reviewCount": 0, "reviews": []}
            )

        average, count = delivery_review_stats(supplier)
        return Response(
            {
                "showRating": visible,
                "rating": average,
                "reviewCount": count,
                "reviews": public_review_rows(supplier),
            }
        )


class ProductListView(APIView):
    def get(self, request):
        qs = annotate_delivery_reviews(
            Product.objects.filter(
                is_active=True, supplier__is_active=True, supplier__is_ghost=False
            ),
            supplier_field="supplier_id",
        ).select_related(
            "supplier", "supplier__settings", "category", "subcategory", "material"
        ).prefetch_related("images", "search_tags", "attribute_values__attribute")

        q = request.query_params.get("q")
        if q:
            qs = filter_products_by_text(qs, q, include_supplier_name=True)

        category = request.query_params.get("category")
        if category:
            qs = qs.filter(category__name_en__icontains=category)

        supplier_id = request.query_params.get("supplier_id")
        if supplier_id:
            qs = qs.filter(supplier_id=supplier_id)

        subcategory_id = request.query_params.get("subcategory_id")
        if subcategory_id and str(subcategory_id).isdigit():
            qs = qs.filter(subcategory_id=int(subcategory_id))

        country = request.query_params.get("country")
        if country and country.lower() not in ("algeria", "dz", "all"):
            qs = qs.none()

        qs = annotate_boutique_search_priority(qs, through_supplier=True)
        if not q:
            qs, has_fav_rank = _annotate_buyer_favorite_products(qs, request.user)
            if has_fav_rank:
                qs = qs.order_by(
                    "_favorite_rank",
                    "_boutique_search_priority",
                    "-is_featured",
                    "-created_at",
                )
            else:
                qs = qs.order_by(
                    "_boutique_search_priority",
                    "-is_featured",
                    "-created_at",
                )
        else:
            qs, has_fav_rank = _annotate_buyer_favorite_products(qs, request.user)
            if has_fav_rank:
                qs = qs.order_by(
                    "_favorite_rank",
                    "_boutique_search_priority",
                    "-created_at",
                )
        if q:
            product_count = qs.count()
            supplier_ids = list(qs.values_list("supplier_id", flat=True).distinct())
            log_search_with_appearances(
                request,
                search_query=q,
                supplier_ids=supplier_ids,
                results_count=product_count,
                source="products",
                category=category or None,
            )
        products = ProductListSerializer(
            qs, many=True, context={"request": request}
        ).data

        if q and not products:
            from user.search_query_log import log_search_miss

            buyer_id = (
                request.user.id
                if request.user.is_authenticated and not request.user.supplier_id
                else None
            )
            log_search_miss(query=q, results_count=0, buyer_id=buyer_id)

        suggestions = None
        if q and not products:
            suggestion_data = build_search_suggestions(q)
            if suggestion_data["similarTerms"] or suggestion_data["didYouMean"]:
                suggestions = {
                    "similarTerms": suggestion_data["similarTerms"],
                    "didYouMean": suggestion_data["didYouMean"],
                }

        supplier_ids_for_intent = list(
            qs.values_list("supplier_id", flat=True).distinct()
        )
        _maybe_sync_category_intent_from_listing(request, supplier_ids_for_intent)

        if suggestions:
            return Response({"results": products, "suggestions": suggestions})

        return Response(products)


class ProductDetailView(APIView):
    def get(self, request, pk):
        try:
            product = (
                Product.objects.filter(
                    is_active=True, supplier__is_active=True, supplier__is_ghost=False
                )
                .select_related("supplier", "category", "subcategory", "material")
                .prefetch_related(
                    "images",
                    "search_tags",
                    "attribute_values__attribute",
                )
                .get(pk=pk)
            )
        except Product.DoesNotExist:
            return Response(
                {"error": "Product not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        source = request.query_params.get("source") or "direct"
        log_supplier_product_view(
            request,
            supplier_id=product.supplier_id,
            product_id=product.pk,
            source=source,
        )
        return Response(
            ProductOrderDetailSerializer(
                product, context={"request": request}
            ).data
        )


class ProductTechnicalSheetPdfView(APIView):
    permission_classes = [AllowAny]
    renderer_classes = [PDFRenderer, JSONRenderer]

    def get(self, request, pk):
        product = (
            Product.objects.filter(
                is_active=True, supplier__is_active=True, supplier__is_ghost=False, pk=pk
            )
            .select_related("supplier", "category", "subcategory", "material")
            .prefetch_related("images", "attribute_values__attribute", "attribute_values__attribute__unit")
            .first()
        )
        if not product:
            return Response(
                {"error": "Product not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            pdf_bytes, filename = product_datasheet_pdf_bytes(product)
        except RuntimeError:
            return Response(
                {"error": "Impossible de générer la fiche technique."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response


MAX_COMPARE_PRODUCTS = 5


def _product_compare_image_url(product, request):
    image = product.get_display_image()
    if not image:
        return None
    url = image.url
    if request is not None:
        return request.build_absolute_uri(url)
    return url


def _build_product_compare_payload(products, request):
    from user.api.serializers import (
        _join_attribute_values,
        _product_delivery_label,
        _product_dimensions,
        _product_moq_label,
        _product_price_badge,
    )

    subcategory = products[0].subcategory
    category = products[0].category
    if not subcategory:
        raise ValueError("missing_subcategory")

    product_payloads = []
    for product in products:
        product_payloads.append(
            {
                "id": product.id,
                "name": product.name,
                "imageUrl": _product_compare_image_url(product, request),
                "supplierName": product.supplier.company_name,
                "priceFrom": _product_price_badge(product),
            }
        )

    rows = []
    fixed_rows = [
        ("Prix", lambda p: _product_price_badge(p)),
        ("MOQ", lambda p: _product_moq_label(p)),
        ("Livraison", lambda p: _product_delivery_label(p)),
        ("Dimensions", lambda p: _product_dimensions(p) or "—"),
    ]
    if any(p.material_id for p in products):
        fixed_rows.append(
            (
                "Matière première",
                lambda p: (
                    (p.material.name_fr or p.material.name_en)
                    if p.material_id
                    else "—"
                ),
            )
        )

    for label, getter in fixed_rows:
        rows.append({"label": label, "values": [getter(p) for p in products]})

    dim_keys = {"moq", "livraison", "dimensions", "matière première"}
    for link in _category_attribute_links(subcategory):
        attribute = link.attribute
        label = (attribute.name_fr or attribute.name_en or "").strip()
        if not label or label.lower() in dim_keys:
            continue
        values = []
        for product in products:
            matches = [
                av for av in product.attribute_values.all() if av.attribute_id == attribute.id
            ]
            if not matches:
                values.append("—")
            elif attribute.attribute_type == "boolean":
                values.append("Oui" if matches[0].bool_value else "Non")
            else:
                values.append(_join_attribute_values(matches) or "—")
        rows.append(
            {
                "label": label,
                "attributeId": attribute.id,
                "values": values,
            }
        )

    return {
        "subcategoryId": subcategory.id,
        "subcategoryName": subcategory.name_fr or subcategory.name_en,
        "categoryName": (
            (category.name_fr or category.name_en) if category else ""
        ),
        "maxProducts": MAX_COMPARE_PRODUCTS,
        "products": product_payloads,
        "rows": rows,
    }


class ProductCompareView(APIView):
    def get(self, request):
        ids_param = (request.query_params.get("ids") or "").strip()
        if not ids_param:
            return Response(
                {"error": "Le paramètre ids est requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_ids = []
        for part in ids_param.split(","):
            part = part.strip()
            if part.isdigit():
                raw_ids.append(int(part))
        product_ids = list(dict.fromkeys(raw_ids))

        if not product_ids:
            return Response(
                {"error": "Aucun identifiant produit valide."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(product_ids) > MAX_COMPARE_PRODUCTS:
            return Response(
                {
                    "error": f"Maximum {MAX_COMPARE_PRODUCTS} produits comparables.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        products = list(
            Product.objects.filter(
                is_active=True, supplier__is_active=True, supplier__is_ghost=False, pk__in=product_ids
            )
            .select_related("supplier", "category", "subcategory", "material")
            .prefetch_related(
                "images",
                "attribute_values__attribute",
                "attribute_values__attribute__unit",
            )
        )
        if len(products) != len(product_ids):
            return Response(
                {"error": "Un ou plusieurs produits sont introuvables."},
                status=status.HTTP_404_NOT_FOUND,
            )

        by_id = {product.id: product for product in products}
        ordered_products = [by_id[pid] for pid in product_ids if pid in by_id]

        subcategory_ids = {
            product.subcategory_id for product in ordered_products
        }
        if None in subcategory_ids or len(subcategory_ids) != 1:
            return Response(
                {
                    "error": (
                        "La comparaison est limitée aux produits "
                        "de la même sous-catégorie."
                    ),
                    "code": "mixed_subcategory",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            payload = _build_product_compare_payload(ordered_products, request)
        except ValueError:
            return Response(
                {"error": "Sous-catégorie manquante pour la comparaison."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        log_product_compare(request, ordered_products)

        return Response(payload)


class ProductCompareCandidatesView(APIView):
    """Similar products in the same subcategory for the compare picker."""

    def get(self, request):
        anchor_param = request.query_params.get("anchor_id")
        if not anchor_param or not str(anchor_param).isdigit():
            return Response(
                {"error": "anchor_id est requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        exclude_ids = []
        for part in (request.query_params.get("exclude") or "").split(","):
            part = part.strip()
            if part.isdigit():
                exclude_ids.append(int(part))

        try:
            anchor = (
                Product.objects.filter(
                    is_active=True, supplier__is_active=True, supplier__is_ghost=False
                )
                .select_related("supplier", "category", "subcategory", "material")
                .prefetch_related("images", "search_tags", "attribute_values__attribute")
                .get(pk=int(anchor_param))
            )
        except Product.DoesNotExist:
            return Response(
                {"error": "Produit introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not anchor.subcategory_id:
            return Response(
                {"error": "Ce produit n'a pas de sous-catégorie comparable."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        exclude_set = set(exclude_ids) | {anchor.id}
        similar = find_similar_products(anchor, limit=30, active_only=True)
        candidates = [product for product in similar if product.id not in exclude_set]

        if len(candidates) < 12:
            filler_qs = (
                Product.objects.filter(
                    is_active=True,
                    subcategory_id=anchor.subcategory_id,
                )
                .exclude(pk__in=exclude_set | {p.id for p in candidates})
                .select_related("supplier", "category", "subcategory", "material")
                .prefetch_related("images", "search_tags", "attribute_values__attribute")
                .order_by("-is_featured", "-created_at")[:24]
            )
            candidates.extend(list(filler_qs))

        q = (request.query_params.get("q") or "").strip()
        if q and candidates:
            id_list = [product.id for product in candidates]
            candidates = list(
                filter_products_by_text(
                    Product.objects.filter(pk__in=id_list)
                    .select_related("supplier", "category", "subcategory", "material")
                    .prefetch_related("images", "search_tags", "attribute_values__attribute"),
                    q,
                    include_supplier_name=True,
                )
            )

        return Response(
            ProductListSerializer(
                candidates,
                many=True,
                context={"request": request},
            ).data
        )


class OrderCreateView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = OrderCreateSerializer(
            data=request.data,
            context={"request": request},
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        product = data["product"]
        quantity = data["quantity"]
        variant = data.get("variant")
        unit_price = product.fixed_unit_price if product.fixed_unit_price is not None else 0
        if variant is not None and variant.price_from is not None:
            unit_price = variant.price_from
        buyer = request.user if request.user.is_authenticated else None
        is_export = data["trade_scope"] == TradeScope.EXPORT

        order_kwargs = {
            "buyer": buyer,
            "supplier": product.supplier,
            "product": product,
            "variant": variant,
            "quantity": quantity,
            "unit_price": unit_price,
            "specifications": data.get("specifications", ""),
            "status": Order.Status.PENDING,
            "origin": Order.Origin.PRODUCT_PAGE,
            "trade_scope": data["trade_scope"],
        }
        if is_export:
            order_kwargs.update(
                {
                    "destination_country": data["destination_country"],
                    "destination_city": data["destination_city"],
                    "incoterm": data["incoterm"],
                    "buyer_note": data["buyer_note"],
                }
            )
        elif data.get("wilaya"):
            order_kwargs["delivery_wilaya"] = data["wilaya"]
        if not buyer:
            order_kwargs.update(
                {
                    "guest_name": serializer.validated_data["guest_name"],
                    "guest_phone": serializer.validated_data["guest_phone"],
                    "guest_phone_country_code": serializer.validated_data[
                        "guest_phone_country_code"
                    ],
                }
            )

        if buyer and not is_export:
            wilaya = data.get("wilaya")
            if wilaya and buyer.wilaya != wilaya:
                User.objects.filter(pk=buyer.pk).update(wilaya=wilaya)
                buyer.wilaya = wilaya

        with transaction.atomic():
            order = Order.objects.create(**order_kwargs)
            record_status_event(
                order,
                from_status="",
                to_status=Order.Status.PENDING,
                actor=buyer,
                actor_role="buyer",
                note=(
                    "Demande de commande export depuis la fiche produit."
                    if is_export
                    else "Commande depuis la fiche produit."
                ),
            )
            register_order_commission(order)
            rbac.log_audit(
                buyer,
                "order.created",
                entity_type="order",
                entity_id=order.pk,
                new_value={
                    "origin": order.origin,
                    "tradeScope": order.trade_scope,
                    "destinationCountry": order.destination_country,
                    "supplierId": order.supplier_id,
                    "guest": buyer is None,
                },
                request=request,
            )
        try:
            crm_service.sync_from_order(order)
        except Exception:
            logger.exception("CRM sync failed for order %s", order.pk)

        if buyer:
            _create_buyer_message(product.supplier, buyer, order_summary_note(order))
        handle_order_created(product.supplier_id, order.id, buyer=buyer)

        message = (
            "Votre demande de commande export a été transmise au fournisseur. Il vous "
            "contactera pour convenir du prix, de l'Incoterm, du transport et du paiement. "
            "Paknomik n'encaisse aucun paiement et ne réserve pas la marchandise."
            if is_export
            else "Votre commande a été enregistrée avec succès."
        )
        return Response(
            {"success": True, "id": order.id, "message": message},
            status=status.HTTP_201_CREATED,
        )


class SearchSuggestionsView(APIView):
    def get(self, request):
        query = request.query_params.get("q", "")
        return Response(build_search_suggestions(query))


class SearchVocabularyView(APIView):
    """Descriptif technique des mots de recherche rattachés aux sous-catégories."""

    permission_classes = [AllowAny]

    def get(self, request):
        from user.search_vocabulary import build_vocabulary_descriptor

        summary = request.query_params.get("summary", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        return Response(build_vocabulary_descriptor(include_entries=not summary))


class LoginView_email(APIView):
    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        password = request.data.get("password", "")

        if not email or not password:
            return Response(
                {"error": "Email and password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response(
                {"error": "Invalid email or password"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.check_password(password):
            return Response(
                {"error": "Invalid email or password"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        handle_auth_login(user, method="email")
        return Response(_auth_payload(user))

class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        login = (
            request.data.get("login")
            or request.data.get("username")
            or request.data.get("email")
            or ""
        ).strip()
        password = request.data.get("password", "")

        if not login or not password:
            return Response(
                {"error": "Email or username and password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        login_key = login.lower()
        user = User.objects.filter(username__iexact=login).first()
        if user is None:
            user = User.objects.filter(email__iexact=login_key).first()

        if user is None:
            return Response(
                {"error": "Invalid email, username or password"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.check_password(password):
            return Response(
                {"error": "Invalid email, username or password"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        handle_auth_login(user, method="email")
        return Response(_auth_payload(user))


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        log_auth_logout(request.user)
        return Response({"success": True})


class RegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        email = (
            request.data.get("email") or request.data.get("user") or ""
        ).strip().lower()
        password = request.data.get("password", "")
        name = request.data.get("name", "").strip()
        company = request.data.get("company") or ""
        is_supplier = bool(request.data.get("isSupplier"))

        if not email or not password or not name:
            return Response(
                {"error": "Email, password, and name are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if User.objects.filter(email=email).exists():
            return Response(
                {"error": "An account with this email already exists"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        username = email
        base_username = email.split("@")[0]
        if User.objects.filter(username=username).exists():
            username = f"{base_username}_{User.objects.count()}"

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=name,
            company_name=company or None,
        )

        if is_supplier:
            supplier, supplier_created = Supplier.objects.get_or_create(
                email=email,
                defaults={"company_name": company or name},
            )
            user.supplier = supplier
            user.save(update_fields=["supplier"])
            if supplier_created:
                handle_supplier_created(supplier, actor_user=user)
            else:
                ensure_supplier_score(supplier.id)
                award_profile_bonuses_for_supplier(supplier, actor_user=user)
        else:
            log_buyer_event(
                user,
                Event.EventType.BUYER_REGISTER,
                {
                    "method": "email",
                    "company_name": company or "",
                },
            )
            from buyer_space.wallet import grant_company_registration

            grant_company_registration(user)

        handle_auth_login(user, method="email")
        return Response(_auth_payload(user), status=status.HTTP_201_CREATED)


def _read_oauth_redirect(request, default="/"):
    redirect_to = request.session.get("oauth_frontend_redirect")
    if redirect_to:
        return redirect_to
    cookie_redirect = request.COOKIES.get("paknomik_oauth_redirect")
    if cookie_redirect and cookie_redirect.startswith("/"):
        return cookie_redirect
    return default


def _clear_oauth_cookies(response: HttpResponse) -> HttpResponse:
    response.delete_cookie("paknomik_oauth")
    response.delete_cookie("paknomik_oauth_redirect")
    return response


def _finalize_google_sociallogin(request, sociallogin):
    from allauth.socialaccount.internal.flows.login import complete_login

    complete_login(request, sociallogin)
    if request.user.is_authenticated:
        return request.user
    if sociallogin.user and sociallogin.user.pk:
        return sociallogin.user
    raise ValueError("google_auth_failed")


class GoogleTokenAuthView(APIView):
    """Verify a Google ID token (GIS) and return a JWT — no redirect/session dance."""

    permission_classes = [AllowAny]

    def post(self, request):
        credential = (request.data.get("credential") or "").strip()
        if not credential:
            return Response(
                {"error": "Google credential is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from allauth.socialaccount.adapter import get_adapter as get_social_adapter

        provider = get_social_adapter().get_provider(request, "google")
        try:
            sociallogin = provider.verify_token(request, {"id_token": credential})
        except Exception:
            return Response(
                {"error": "Invalid Google token"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = _finalize_google_sociallogin(request, sociallogin)
        except ValueError:
            return Response(
                {"error": "Google sign-in could not be completed"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        handle_auth_login(user, method="google")
        return Response(_auth_payload(user))


class GoogleAuthStartView(View):
    def get(self, request):
        frontend_redirect = request.GET.get("redirect", "/")
        if not frontend_redirect.startswith("/"):
            frontend_redirect = "/"

        if not request.session.session_key:
            request.session.create()
        request.session["oauth_frontend_redirect"] = frontend_redirect
        request.session.modified = True

        callback_path = "/api/auth/google/callback/"
        backend_base = settings.BACKEND_URL.rstrip("/")
        login_url = (
            f"{backend_base}/accounts/google/login/"
            f"?next={quote(callback_path, safe='')}"
        )
        response = redirect(login_url)
        response.set_cookie("paknomik_oauth", "1", max_age=600, samesite="Lax")
        response.set_cookie(
            "paknomik_oauth_redirect",
            frontend_redirect,
            max_age=600,
            samesite="Lax",
        )
        return response


def _google_auth_frontend_callback_url(**query_parts: str) -> str:
    base = f"{settings.FRONTEND_URL.rstrip('/')}/auth/callback"
    if not query_parts:
        return base
    query = "&".join(
        f"{key}={quote(value, safe='')}" for key, value in query_parts.items()
    )
    return f"{base}?{query}"


class GoogleAuthCallbackView(View):
    """Plain Django view so the session from django-allauth is always visible."""

    def get(self, request):
        frontend_redirect = _read_oauth_redirect(request, "/")
        request.session.pop("oauth_frontend_redirect", None)

        if not request.user.is_authenticated:
            response = redirect(
                _google_auth_frontend_callback_url(
                    error="google_auth_failed",
                    redirect=frontend_redirect,
                )
            )
            return _clear_oauth_cookies(response)

        handle_auth_login(request.user, method="google")
        payload = _auth_payload(request.user)
        response = redirect(
            _google_auth_frontend_callback_url(
                token=payload["token"],
                user=json.dumps(payload["user"]),
                redirect=frontend_redirect,
            )
        )
        return _clear_oauth_cookies(response)


class RfqView(APIView):
    def post(self, request):
        supplier_id = request.data.get("supplierId")
        product_id = request.data.get("productId")
        title = (request.data.get("title") or "").strip()
        name = request.data.get("name", "").strip()
        company = request.data.get("company", "").strip()
        email = request.data.get("email", "").strip().lower()
        phone = (request.data.get("phone") or "").strip()
        message = request.data.get("message", "").strip()
        destination = (request.data.get("destination") or "").strip()
        category_id = request.data.get("categoryId")
        try:
            quantity_estimate = int(request.data.get("quantity"))
        except (TypeError, ValueError):
            quantity_estimate = 0

        buyer = request.user if request.user.is_authenticated else None
        has_contact = email if buyer else phone
        if not all([supplier_id, name, has_contact]) or quantity_estimate < 1:
            return Response(
                {"error": "supplierId, name, a buyer contact, and a positive quantity are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            supplier = Supplier.objects.get(pk=supplier_id, is_active=True, is_ghost=False)
        except Supplier.DoesNotExist:
            return Response(
                {"error": "Supplier not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        from user.models import Category, RFQRequestFile
        from user.quote_forms import (
            QuoteFormError,
            apply_snapshot,
            clean_technical_files,
            parse_specs,
            prepare_specs,
        )

        category = None
        snapshot = None
        if category_id not in (None, ""):
            if not str(category_id).isdigit():
                return Response(
                    {"error": "categoryId invalide."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            category = Category.objects.filter(pk=int(category_id)).first()
            if category is None:
                return Response(
                    {"error": "Type d'emballage introuvable."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            try:
                snapshot = prepare_specs(supplier, category, parse_specs(request.data.get("specs")))
            except QuoteFormError as exc:
                return Response({"error": exc.message}, status=exc.status_code)

        photos = request.FILES.getlist("photos")
        photo_error = clean_photos(photos)
        if photo_error:
            return Response({"error": photo_error}, status=status.HTTP_400_BAD_REQUEST)
        photos = sanitized_photos(photos)
        technical_files = request.FILES.getlist("files")
        file_error = clean_technical_files(technical_files)
        if file_error:
            return Response({"error": file_error}, status=status.HTTP_400_BAD_REQUEST)

        product = None
        if product_id:
            product = Product.objects.filter(
                pk=product_id, supplier=supplier, is_active=True
            ).first()

        rfq = RFQRequest.objects.create(
            buyer=buyer,
            supplier=supplier,
            product=product,
            title=title,
            name=name,
            company=company,
            email=email,
            phone=phone,
            quantity_estimate=quantity_estimate,
            destination=destination,
            message=message,
            status=RFQRequest.Status.SUBMITTED,
        )
        save_photos(RFQRequestPhoto, "rfq", rfq, photos)
        for upload in technical_files:
            RFQRequestFile.objects.create(
                rfq=rfq,
                file=upload,
                original_name=upload.name,
            )
        if snapshot is not None:
            apply_snapshot(rfq, category, snapshot, actor=buyer)

        if buyer:
            _create_buyer_message(supplier, buyer, message or f"Demande de devis pour {quantity_estimate} unités.")
            try:
                from user.api.quote_demand_views import create_private_demand_from_rfq

                demand = create_private_demand_from_rfq(
                    buyer, supplier, product, message, quantity_estimate, destination
                )
                if demand is not None:
                    save_photos(QuoteDemandPhoto, "demand", demand, photos)
            except Exception:
                logger.exception("Private demand dual-write failed for RFQ %s", rfq.pk)

        try:
            crm_service.sync_from_rfq(rfq)
        except Exception:
            logger.exception("CRM sync failed for RFQ %s", rfq.pk)

        handle_rfq_created(supplier.id, rfq.id, buyer=buyer)

        return Response(
            {
            "success": True,
            "rfqId": rfq.pk,
            "message": (
                    f"Votre demande de devis a été envoyée à {supplier.company_name}."
                ),
            }
        )


class NewsletterView(APIView):
    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        if not email:
            return Response(
                {"error": "Email is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        NewsletterSubscriber.objects.get_or_create(email=email)
        return Response({"success": True})


def _suppliers_for_subcategory(subcategory):
    suppliers = Supplier.objects.filter(
        is_active=True,
        is_ghost=False,
        interested_subcategories=subcategory,
    ).distinct()

    if suppliers.exists():
        return suppliers

    return Supplier.objects.filter(
        is_active=True,
        is_ghost=False,
        products__category=subcategory,
        products__is_active=True,
    ).distinct()


def _notify_suppliers_for_tender(tender_call):
    suppliers = _suppliers_for_subcategory(tender_call.subcategory)
    notified = []

    for supplier in suppliers:
        notification, created = TenderCallNotification.objects.get_or_create(
            tender_call=tender_call,
            supplier=supplier,
        )
        if not created:
            continue

        notified.append(supplier)
        buyer = tender_call.buyer
        buyer_name = buyer.get_full_name() or buyer.username
        subject = (
            "Nouvelle demande de devis — "
            f"{tender_call.subcategory.name_fr or tender_call.subcategory.name_en}"
        )
        message = (
            f"Bonjour {supplier.company_name},\n\n"
            f"Un nouvel appel d'offre correspond à votre sous-catégorie "
            f"« {tender_call.subcategory.name_fr or tender_call.subcategory.name_en} ».\n\n"
            f"Catégorie : {tender_call.category.name_fr or tender_call.category.name_en}\n"
            f"Quantité : {tender_call.quantity}\n"
            f"Type : {tender_call.get_tender_type_display()}\n"
            f"Acheteur : {buyer_name} ({buyer.email})\n"
        )
        if tender_call.description:
            message += f"\nDescription :\n{tender_call.description}\n"

        send_mail(
            subject,
            message,
            None,
            [supplier.email],
            fail_silently=True,
        )

    return notified


def _category_attribute_links(category):
    """Return CategoryAttribute rows for a parent category or subcategory (with parent fallback)."""
    links = (
        CategoryAttribute.objects.filter(category=category)
        .select_related("attribute", "attribute__unit")
        .prefetch_related("options")
        .order_by("sort_order", "attribute__attribute_level", "attribute__name_en")
    )
    if links.exists():
        return links

    if category.parent_id:
        return (
            CategoryAttribute.objects.filter(category=category.parent)
            .select_related("attribute", "attribute__unit")
            .prefetch_related("options")
            .order_by("sort_order", "attribute__attribute_level", "attribute__name_en")
        )

    return links


def _attribute_category_for_validation(category, subcategory):
    """Category used to validate tender technical attributes (parent holds catalog attrs)."""
    if CategoryAttribute.objects.filter(category=subcategory).exists():
        return subcategory
    if CategoryAttribute.objects.filter(category=category).exists():
        return category
    return category.parent if category.parent_id else category


class CategoryAttributesView(APIView):
    def get(self, request):
        category_id = request.query_params.get("category")
        subcategory_id = request.query_params.get("subcategory")

        if not category_id and not subcategory_id:
            return Response(
                {"error": "category or subcategory query parameter is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target = None
        if subcategory_id and str(subcategory_id).isdigit():
            try:
                target = Category.objects.select_related("parent").get(
                    pk=int(subcategory_id), parent__isnull=False
                )
            except Category.DoesNotExist:
                return Response(
                    {"error": "Subcategory not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )
        elif category_id and str(category_id).isdigit():
            try:
                target = Category.objects.get(pk=int(category_id), parent__isnull=True)
            except Category.DoesNotExist:
                return Response(
                    {"error": "Category not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            return Response(
                {"error": "Invalid category or subcategory id"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        links = _category_attribute_links(target)

        data = []
        for link in links:
            data.append(
                TechnicalAttributeSerializer(
                    link.attribute,
                    context={"category_attribute": link, "request": request},
                ).data
            )

        return Response(data)


class TenderCallCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        category_id = request.data.get("categoryId")
        subcategory_id = request.data.get("subcategoryId")
        quantity = request.data.get("quantity")
        description = (request.data.get("description") or "").strip()
        tender_type = request.data.get("tenderType", "simple")
        technical_attributes = request.data.get("technicalAttributes") or []

        if not all([category_id, subcategory_id, quantity]):
            return Response(
                {"error": "categoryId, subcategoryId, and quantity are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            quantity = int(quantity)
            if quantity <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return Response(
                {"error": "quantity must be a positive integer"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if tender_type not in ("simple", "detailed"):
            return Response(
                {"error": "tenderType must be 'simple' or 'detailed'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            category = Category.objects.get(pk=category_id, parent__isnull=True)
            subcategory = Category.objects.select_related("parent", "parent__parent").get(
                pk=subcategory_id
            )
            if not subcategory_belongs_to_category(category, subcategory):
                raise Category.DoesNotExist
        except Category.DoesNotExist:
            return Response(
                {"error": "Invalid category or subcategory"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tender_call = TenderCall.objects.create(
            buyer=request.user,
            category=category,
            subcategory=subcategory,
            quantity=quantity,
            description=description,
            tender_type=tender_type,
        )

        if tender_type == "detailed":
            attribute_category = _attribute_category_for_validation(category, subcategory)
            for item in technical_attributes:
                attribute_id = item.get("attributeId")
                value = (item.get("value") or "").strip()
                if not attribute_id or not value:
                    continue
                try:
                    attribute = TechnicalAttribute.objects.get(pk=attribute_id)
                except TechnicalAttribute.DoesNotExist:
                    continue
                if not CategoryAttribute.objects.filter(
                    category=attribute_category, attribute=attribute
                ).exists():
                    continue
                TenderCallAttributeValue.objects.create(
                    tender_call=tender_call,
                    attribute=attribute,
                    value=value,
                )

        notified_suppliers = _notify_suppliers_for_tender(tender_call)

        return Response(
            {
                "success": True,
                "id": tender_call.id,
                "message": "Votre appel d'offre a été publié avec succès.",
                "notifiedSupplierCount": len(notified_suppliers),
            },
            status=status.HTTP_201_CREATED,
        )


class SupplierDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if not user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        supplier = user.supplier
        period = resolve_report_period(request.query_params)
        year, month = period.year, period.month
        raw_subcategory = request.query_params.get("subcategory")
        subcategory_id = int(raw_subcategory) if str(raw_subcategory or "").isdigit() else None

        sections = build_supplier_dashboard_sections(
            supplier,
            period,
            subcategory_id=subcategory_id,
        )
        geo = build_supplier_geo_breakdown(supplier.id, period=period)

        log_seller_event(
            user,
            Event.EventType.SELLER_DASHBOARD_VIEW,
            {
                "supplier_id": supplier.id,
                "year": year,
                "month": month,
                "period": period.preset,
            },
            request=request,
        )

        return Response(
            {
                "companyName": supplier.company_name,
                "logo": _supplier_logo_url(request, supplier),
                "year": year,
                "month": month,
                "period": period.as_dict(),
                "subcategories": sections["subcategories"],
                "subcategoryId": sections["subcategoryId"],
                "market": sections["market"],
                "visibility": sections["visibility"],
                "activity": sections["activity"],
                "chart": sections["chart"],
                "geoBreakdown": geo["locations"],
                "geoCountryBreakdown": geo["countries"],
            }
        )


class SupplierMarketAnalysisView(APIView):
    """Supplier market analysis page (placeholder metrics + event logging)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if not user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        supplier = user.supplier
        today = date.today()
        year = int(request.query_params.get("year", today.year))
        month = int(request.query_params.get("month", today.month))
        month = max(1, min(12, month))

        active_products = supplier.products.filter(is_active=True).count()
        category_count = supplier.interested_categories.count()
        from user.supplier_competitors import count_direct_competitors

        competitors = count_direct_competitors(supplier)

        log_seller_event(
            user,
            Event.EventType.SELLER_MARKET_ANALYSIS_VIEW,
            {
                "supplier_id": supplier.id,
                "year": year,
                "month": month,
            },
        )

        return Response(
            {
                "companyName": supplier.company_name,
                "year": year,
                "month": month,
                "summary": {
                    "activeProducts": active_products,
                    "categories": category_count,
                    "directCompetitors": competitors,
                },
                "status": "coming_soon",
            }
        )


class SupplierCategoryBenchmarkView(APIView):
    """Anonymized packaging-category benchmark. Same read as the dashboard: no debit."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if not user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        supplier = user.supplier
        period = resolve_report_period(request.query_params)
        raw_subcategory = request.query_params.get("subcategory")
        subcategory_id = int(raw_subcategory) if str(raw_subcategory or "").isdigit() else None
        payload = build_category_benchmark(supplier, period, subcategory_id)

        log_seller_event(
            user,
            Event.EventType.SELLER_MARKET_ANALYSIS_VIEW,
            {
                "supplier_id": supplier.id,
                "view": "category_benchmark",
                "period": period.preset,
                "subcategory_id": payload["subcategoryId"],
                "masked": payload["group"]["masked"],
            },
            request=request,
        )
        return Response(payload)


def _supplier_logo_url(request, supplier):
    if not supplier.logo:
        return None
    url = supplier.logo.url
    if request is not None:
        return request.build_absolute_uri(url)
    return url


def _supplier_product_form_context(request):
    supplier = Supplier.objects.get(pk=request.user.supplier_id)
    return {
        "request": request,
        "supplier_id": request.user.supplier_id,
        "supplier": supplier,
        "search_tag_limit": get_supplier_search_tag_limit(supplier),
        "image_limit": get_supplier_product_image_limit(supplier),
    }


class SearchTagListView(APIView):
    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        try:
            limit = min(int(request.query_params.get("limit", 300)), 300)
        except (TypeError, ValueError):
            limit = 20
        qs = SearchTag.objects.all()
        if q:
            qs = qs.filter(
                Q(tag__icontains=q)
                | Q(tag_fr__icontains=q)
                | Q(tag_ar__icontains=q)
            ).order_by("-search_weight", "tag")
        else:
            qs = qs.order_by("tag_fr", "tag")
        return Response(SearchTagSerializer(qs[:limit], many=True).data)


class SupplierProductFormMetaView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        supplier = Supplier.objects.get(pk=request.user.supplier_id)
        tier = get_supplier_subscription_tier(supplier)
        from user.product_photos import product_photo_pricing_payload

        return Response(
            {
                "subscriptionTier": tier,
                "searchTagLimit": get_supplier_search_tag_limit(supplier),
                "imageLimit": get_supplier_product_image_limit(supplier),
                "targetMarkets": list(TARGET_MARKET_OPTIONS),
                **product_photo_pricing_payload(),
            }
        )


class SupplierProductsView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _supplier_required(self, request):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    def _product_queryset(self, supplier_id):
        return (
            Product.objects.filter(supplier_id=supplier_id)
            .select_related("supplier", "category", "subcategory", "material")
            .prefetch_related(
                "images",
                "search_tags",
                "attribute_values__attribute",
            )
        )

    def get(self, request):
        denied = self._supplier_required(request)
        if denied:
            return denied

        qs = self._product_queryset(request.user.supplier_id).filter(
            is_active=True
        ).order_by("-is_featured", "-created_at")
        return Response(
            SupplierProductSerializer(
                qs, many=True, context={"request": request}
            ).data
        )

    def post(self, request):
        denied = self._supplier_required(request)
        if denied:
            return denied

        # Généreux caps the catalogue; Business is unlimited. The limit comes
        # from PlanEntitlement, never from a constant in this file.
        assert_can_add_product(request.user.supplier)

        serializer = SupplierProductFormSerializer(
            data=request.data,
            context=_supplier_product_form_context(request),
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        product = serializer.save()
        product = self._product_queryset(request.user.supplier_id).get(pk=product.pk)
        handle_product_added(
            request.user.supplier_id,
            product.pk,
            actor_user=request.user,
        )
        return Response(
            SupplierProductFormSerializer(
                product, context=_supplier_product_form_context(request)
            ).data,
            status=status.HTTP_201_CREATED,
        )


class SupplierProductDetailView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _get_product(self, request, pk):
        if not request.user.supplier_id:
            return None, Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            product = (
                Product.objects.filter(supplier_id=request.user.supplier_id)
                .select_related("category", "subcategory")
                .prefetch_related("images", "attribute_values__attribute", "search_tags")
                .get(pk=pk)
            )
        except Product.DoesNotExist:
            return None, Response(
                {"error": "Product not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return product, None

    def get(self, request, pk):
        product, err = self._get_product(request, pk)
        if err:
            return err
        return Response(
            SupplierProductFormSerializer(
                product, context=_supplier_product_form_context(request)
            ).data
        )

    def patch(self, request, pk):
        product, err = self._get_product(request, pk)
        if err:
            return err

        serializer = SupplierProductFormSerializer(
            product,
            data=request.data,
            partial=True,
            context=_supplier_product_form_context(request),
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        product = serializer.save()
        handle_product_edited(
            request.user.supplier_id,
            product.pk,
            actor_user=request.user,
        )
        return Response(
            SupplierProductFormSerializer(
                product, context=_supplier_product_form_context(request)
            ).data
        )

    def put(self, request, pk):
        return self.patch(request, pk)

    def delete(self, request, pk):
        product, err = self._get_product(request, pk)
        if err:
            return err
        product.is_active = False
        product.save(update_fields=["is_active", "updated_at"])
        handle_product_deleted(
            request.user.supplier_id,
            product.pk,
            actor_user=request.user,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupplierOrdersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        qs = (
            Order.objects.filter(supplier_id=request.user.supplier_id)
            .select_related(
                "product", "variant", "buyer", "quote_proposal", "commission", "commission__mandate"
            )
            .prefetch_related("responses", "status_events")
            .order_by("-created_at")
        )

        status_filter = request.query_params.get("status")
        if status_filter and status_filter != "all":
            qs = qs.filter(status=status_filter)

        orders = list(qs)
        for order in orders:
            order._prefetched_responses = list(order.responses.all()[:1])

        return Response(
            SupplierOrderSerializer(orders, many=True, context={"request": request}).data
        )


class SupplierRfqsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        qs = (
            RFQRequest.objects.filter(supplier_id=request.user.supplier_id)
            .select_related("product", "category")
            .prefetch_related("responses", "photos", "files")
            .order_by("-created_at")
        )
        for rfq in qs:
            rfq._prefetched_responses = list(rfq.responses.all()[:1])

        return Response(
            SupplierRfqSerializer(qs, many=True, context={"request": request}).data
        )


class SupplierRfqProcessingView(APIView):
    """Mark a boutique RFQ as in review and notify its registered buyer."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        rfq = (
            RFQRequest.objects.filter(
                pk=pk,
                supplier_id=request.user.supplier_id,
            )
            .select_related("supplier")
            .first()
        )
        if not rfq:
            return Response({"error": "Demande introuvable."}, status=status.HTTP_404_NOT_FOUND)

        buyer = resolve_buyer_for_rfq(rfq)
        if not buyer:
            return Response(
                {"error": "Aucun compte acheteur Paknomik n’est associé à cette demande."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if rfq.status not in {
            RFQRequest.Status.SUBMITTED,
            RFQRequest.Status.IN_REVIEW,
        }:
            return Response(
                {"error": "Cette demande ne peut plus être marquée en cours de traitement."},
                status=status.HTTP_409_CONFLICT,
            )

        if rfq.status != RFQRequest.Status.IN_REVIEW:
            rfq.status = RFQRequest.Status.IN_REVIEW
            update_fields = ["status", "updated_at"]
            if rfq.responded_at is None:
                rfq.responded_at = timezone.now()
                update_fields.append("responded_at")
            rfq.save(update_fields=update_fields)
            from user.models import RFQRequestEvent
            from user.quote_forms import record_rfq_event

            record_rfq_event(
                rfq,
                RFQRequestEvent.Action.STATUS_CHANGED,
                actor=request.user,
                snapshot={"to": RFQRequest.Status.IN_REVIEW},
            )

        notification_link = f"/buyer?tab=orders&view=devis&rfq={rfq.pk}"
        AppNotification.objects.get_or_create(
            user=buyer,
            kind=AppNotification.Kind.RFQ_IN_REVIEW,
            link=notification_link,
            defaults={
                "title": "Votre demande est en cours de traitement",
                "body": (
                    f"{rfq.supplier.company_name} a commencé à traiter votre demande de devis. "
                    "Vous serez informé(e) de la suite sur Paknomik."
                ),
            },
        )

        return Response(
            SupplierRfqSerializer(rfq, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class SupplierRfqRespondView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request, pk):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        rfq = (
            RFQRequest.objects.filter(
                pk=pk,
                supplier_id=request.user.supplier_id,
            )
            .select_related("product", "supplier")
            .first()
        )
        if not rfq:
            return Response({"error": "Demande introuvable."}, status=status.HTTP_404_NOT_FOUND)

        serializer = SupplierRfqRespondSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        blocks = data.get("blocks") or []
        message = (data.get("message") or "").strip()
        if not message and blocks:
            message = render_proposal_blocks_to_text(blocks)
        if not message:
            return Response(
                {"error": "Le contenu de la proposition est requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_obj = QuoteResponse.objects.create(
            quote_request=rfq,
            supplier_id=request.user.supplier_id,
            message=message,
            proposal_template_id=(data.get("templateId") or "").strip(),
            proposal_template_name=(data.get("templateName") or "").strip(),
            proposal_blocks=blocks,
            proposed_price=data.get("proposedPrice"),
            lead_time_days=data.get("leadTimeDays"),
            moq=data.get("moq"),
            customization_fee=data.get("customizationFee"),
            commercial_terms=(data.get("commercialTerms") or "").strip(),
            status=QuoteResponse.Status.SUBMITTED,
        )

        update_fields = []
        if rfq.status in {
            RFQRequest.Status.SUBMITTED,
            RFQRequest.Status.IN_REVIEW,
        }:
            rfq.status = RFQRequest.Status.QUOTED
            update_fields.append("status")
        if rfq.responded_at is None:
            rfq.responded_at = response_obj.created_at
            update_fields.append("responded_at")
        if update_fields:
            rfq.save(update_fields=[*update_fields, "updated_at"])

        from user.models import RFQRequestEvent
        from user.quote_forms import record_rfq_event

        record_rfq_event(
            rfq,
            RFQRequestEvent.Action.QUOTED,
            actor=request.user,
            comment=message,
            snapshot={
                "response_id": response_obj.id,
                "proposed_price": str(response_obj.proposed_price or ""),
                "moq": response_obj.moq,
                "lead_time_days": response_obj.lead_time_days,
                "customization_fee": str(response_obj.customization_fee or ""),
                "commercial_terms": response_obj.commercial_terms,
            },
        )

        buyer = resolve_buyer_for_rfq(rfq)
        create_proposal_notice_message(
            supplier=rfq.supplier,
            buyer=buyer,
            sender=request.user,
            kind="quote",
            response_id=response_obj.id,
        )
        try:
            crm_service.mark_proposal_sent(
                rfq.supplier,
                rfq=rfq,
                actor=request.user,
                note=f"Proposition devis #{response_obj.id} envoyée.",
                estimated_value=response_obj.proposed_price,
            )
        except Exception:
            logger.exception("CRM stage update failed for RFQ %s", rfq.pk)

        return Response(
            QuoteResponseSerializer(response_obj).data,
            status=status.HTTP_201_CREATED,
        )


class SupplierOrderRespondView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request, pk):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        order = (
            Order.objects.filter(
                pk=pk,
                supplier_id=request.user.supplier_id,
            )
            .select_related("product", "supplier", "buyer")
            .first()
        )
        if not order:
            return Response({"error": "Commande introuvable."}, status=status.HTTP_404_NOT_FOUND)

        serializer = SupplierOrderRespondSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        blocks = data.get("blocks") or []
        message = (data.get("message") or "").strip()
        if not message and blocks:
            message = render_proposal_blocks_to_text(blocks)
        if not message:
            return Response(
                {"error": "Le contenu de la proposition est requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_obj = OrderResponse.objects.create(
            order=order,
            supplier_id=request.user.supplier_id,
            message=message,
            proposal_template_id=(data.get("templateId") or "").strip(),
            proposal_template_name=(data.get("templateName") or "").strip(),
            proposal_blocks=blocks,
            proposed_price=data.get("proposedPrice"),
            lead_time_days=data.get("leadTimeDays"),
            status=OrderResponse.Status.SUBMITTED,
        )

        if order.status == Order.Status.PENDING:
            order.status = Order.Status.IN_NEGOTIATION
            order.save(update_fields=["status", "updated_at"])

        buyer = resolve_buyer_for_order(order)
        create_proposal_notice_message(
            supplier=order.supplier,
            buyer=buyer,
            sender=request.user,
            kind="order",
            response_id=response_obj.id,
        )
        try:
            crm_service.mark_proposal_sent(
                order.supplier,
                order=order,
                actor=request.user,
                note=f"Proposition commande #{response_obj.id} envoyée.",
                estimated_value=response_obj.proposed_price,
            )
        except Exception:
            logger.exception("CRM stage update failed for order %s", order.pk)

        return Response(
            OrderResponseSerializer(response_obj).data,
            status=status.HTTP_201_CREATED,
        )


class SupplierOrderOutcomeView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def patch(self, request, pk):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        order = (
            Order.objects.filter(
                pk=pk,
                supplier_id=request.user.supplier_id,
            )
            .prefetch_related("responses")
            .first()
        )
        if not order:
            return Response({"error": "Commande introuvable."}, status=status.HTTP_404_NOT_FOUND)

        if order.status in {Order.Status.WON, Order.Status.LOST}:
            settled = "won" if order.status == Order.Status.WON else "lost"
            try:
                crm_service.sync_order_outcome(
                    order,
                    outcome=settled,
                    actor=request.user,
                )
            except Exception:
                logger.exception(
                    "CRM heal failed for settled order %s", order.pk
                )
            return Response(
                SupplierOrderSerializer(order, context={"request": request}).data
            )

        if not order.responses.exists():
            return Response(
                {"error": "Une proposition doit être envoyée avant de marquer le résultat."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        outcome = str(request.data.get("outcome") or "").strip().lower()
        if outcome not in {"won", "lost"}:
            return Response(
                {"error": "Le résultat doit être « won » ou « lost »."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        latest = order.responses.order_by("-created_at").first()
        if outcome == "won":
            order.status = Order.Status.WON
            if latest:
                latest.status = OrderResponse.Status.ACCEPTED
                latest.save(update_fields=["status", "updated_at"])
        else:
            order.status = Order.Status.LOST
            if latest:
                latest.status = OrderResponse.Status.REJECTED
                latest.save(update_fields=["status", "updated_at"])

        order.save(update_fields=["status", "updated_at"])
        try:
            crm_service.sync_order_outcome(
                order,
                outcome=outcome,
                actor=request.user,
            )
        except Exception:
            logger.exception("CRM sync failed for order outcome %s", order.pk)

        return Response(
            SupplierOrderSerializer(order, context={"request": request}).data
        )


class SupplierOrderStatusView(APIView):
    """Supplier-side execution tracking (confirmed → production → shipped → delivered)."""

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def patch(self, request, pk):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        order = (
            Order.objects.filter(pk=pk, supplier_id=request.user.supplier_id)
            .select_related("product", "buyer", "supplier", "quote_proposal")
            .first()
        )
        if not order:
            return Response({"error": "Commande introuvable."}, status=status.HTTP_404_NOT_FOUND)

        to_status = str(request.data.get("status") or "").strip()
        note = str(request.data.get("note") or "").strip()[:2000]
        if to_status not in Order.Status.values:
            return Response(
                {"error": "Statut inconnu.", "code": "invalid_status"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if to_status == Order.Status.CANCELLED and not note:
            return Response(
                {
                    "error": "Indiquez le motif de l'annulation.",
                    "code": "cancel_reason_required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        expected_raw = request.data.get("expectedDeliveryDate")
        expected_date = None
        if expected_raw not in (None, ""):
            try:
                expected_date = date.fromisoformat(str(expected_raw)[:10])
            except ValueError:
                return Response(
                    {"error": "Date de livraison prévue invalide.", "code": "invalid_date"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            transition_order(
                order,
                to_status,
                actor=request.user,
                actor_role="supplier",
                note=note,
            )
        except OrderTransitionError as exc:
            return Response(
                {
                    "error": str(exc),
                    "code": "invalid_transition",
                    "allowed": supplier_allowed_transitions(order),
                },
                status=status.HTTP_409_CONFLICT,
            )

        if expected_date:
            order.expected_delivery_date = expected_date
            order.save(update_fields=["expected_delivery_date", "updated_at"])

        try:
            crm_service.sync_order_execution(order, actor=request.user, note=note)
        except Exception:
            logger.exception("CRM sync failed for order status %s", order.pk)

        if order.buyer_id:
            content = f"Commande #{order.pk} — {order_status_label(to_status)}."
            if expected_date:
                content += f"\nLivraison prévue le {expected_date.strftime('%d/%m/%Y')}."
            if note:
                content += f"\n{note}"
            Message.objects.create(
                supplier=order.supplier,
                buyer=order.buyer,
                sender=request.user,
                content=content,
            )

        order = (
            Order.objects.filter(pk=order.pk)
            .select_related("product", "buyer", "quote_proposal", "commission", "commission__mandate")
            .prefetch_related("responses", "status_events")
            .first()
        )
        return Response(SupplierOrderSerializer(order, context={"request": request}).data)


class SupplierOrderAmountView(APIView):
    """Supplier sets the unit price when the product sheet has no fixed price."""

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def patch(self, request, pk):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        order = _supplier_order_or_none(request, pk)
        if not order:
            return Response({"error": "Commande introuvable."}, status=status.HTTP_404_NOT_FOUND)
        try:
            update_order_unit_price(
                order,
                request.data.get("unitPrice"),
                actor=request.user,
                note=str(request.data.get("note") or "").strip()[:2000],
                request=request,
            )
        except OrderAmountError as exc:
            return Response(
                {"error": str(exc), "code": "invalid_amount"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return _supplier_order_response(request, order.pk)


def _supplier_order_or_none(request, pk):
    return (
        Order.objects.filter(pk=pk, supplier_id=request.user.supplier_id)
        .select_related("product", "buyer", "supplier")
        .first()
    )


def _supplier_order_response(request, pk):
    order = (
        Order.objects.filter(pk=pk)
        .select_related("product", "buyer", "quote_proposal", "commission", "commission__mandate")
        .prefetch_related("responses", "status_events")
        .first()
    )
    return Response(SupplierOrderSerializer(order, context={"request": request}).data)


class SupplierOrderArchiveView(APIView):
    """Hide a closed order from the supplier's main list (or bring it back)."""

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request, pk):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        order = _supplier_order_or_none(request, pk)
        if not order:
            return Response({"error": "Commande introuvable."}, status=status.HTTP_404_NOT_FOUND)

        archive = request.data.get("archived", True) not in (False, "false", "0", 0)
        if archive and order.status not in SUPPLIER_ARCHIVABLE:
            return Response(
                {
                    "error": "Seule une commande clôturée (annulée, perdue, expirée ou terminée) "
                    "peut être archivée.",
                    "code": "not_archivable",
                },
                status=status.HTTP_409_CONFLICT,
            )

        previous = order.supplier_archived_at
        order.supplier_archived_at = timezone.now() if archive else None
        order.save(update_fields=["supplier_archived_at", "updated_at"])
        rbac.log_audit(
            request.user,
            "order.archived" if archive else "order.unarchived",
            entity_type="order",
            entity_id=order.pk,
            old_value={"archivedAt": previous.isoformat() if previous else None},
            new_value={"archived": archive},
            request=request,
        )
        return _supplier_order_response(request, order.pk)


class SupplierOrderReopenView(APIView):
    """Relancer une commande annulée : elle repasse « En attente » et sort des archives."""

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request, pk):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        order = _supplier_order_or_none(request, pk)
        if not order:
            return Response({"error": "Commande introuvable."}, status=status.HTTP_404_NOT_FOUND)

        note = str(request.data.get("note") or "").strip()[:2000]
        try:
            with transaction.atomic():
                transition_order(
                    order,
                    Order.Status.PENDING,
                    actor=request.user,
                    actor_role="supplier",
                    note=note or "Commande relancée par le fournisseur.",
                    allowed=SUPPLIER_REOPEN_TRANSITIONS,
                )
                if order.supplier_archived_at:
                    order.supplier_archived_at = None
                    order.save(update_fields=["supplier_archived_at", "updated_at"])
                rbac.log_audit(
                    request.user,
                    "order.reopened",
                    entity_type="order",
                    entity_id=order.pk,
                    old_value={"status": Order.Status.CANCELLED},
                    new_value={"status": Order.Status.PENDING},
                    reason=note,
                    request=request,
                )
        except OrderTransitionError as exc:
            return Response(
                {"error": str(exc), "code": "invalid_transition"},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            crm_service.sync_order_execution(order, actor=request.user)
        except Exception:
            logger.exception("CRM sync failed for reopened order %s", order.pk)

        if order.buyer_id:
            content = f"Commande #{order.pk} relancée par le fournisseur — de nouveau en attente."
            if note:
                content += f"\n{note}"
            Message.objects.create(
                supplier=order.supplier,
                buyer=order.buyer,
                sender=request.user,
                content=content,
            )
        return _supplier_order_response(request, order.pk)


def _user_can_access_quote_response(user, response_obj: QuoteResponse) -> bool:
    if not user.is_authenticated:
        return False
    if user.supplier_id and response_obj.supplier_id == user.supplier_id:
        return True
    rfq = response_obj.quote_request
    buyer = resolve_buyer_for_rfq(rfq)
    return buyer is not None and buyer.id == user.id


def _user_can_access_order_response(user, response_obj: OrderResponse) -> bool:
    if not user.is_authenticated:
        return False
    if user.supplier_id and response_obj.supplier_id == user.supplier_id:
        return True
    buyer = resolve_buyer_for_order(response_obj.order)
    return buyer is not None and buyer.id == user.id


class QuoteProposalPdfView(APIView):
    permission_classes = [IsAuthenticated]
    renderer_classes = [PDFRenderer, JSONRenderer]

    def get(self, request, pk):
        response_obj = (
            QuoteResponse.objects.filter(pk=pk)
            .select_related("supplier", "quote_request", "quote_request__product")
            .first()
        )
        if not response_obj or not _user_can_access_quote_response(request.user, response_obj):
            return Response({"error": "Document introuvable."}, status=status.HTTP_404_NOT_FOUND)

        rfq = response_obj.quote_request
        product_name = rfq.product.name if rfq.product_id and rfq.product else ""
        title = (rfq.title or product_name or "Proposition commerciale").strip()
        subtitle = f"Demande de devis — {rfq.name}"
        if rfq.company:
            subtitle = f"{subtitle} ({rfq.company})"
        client_name = rfq.name.strip()
        if rfq.company:
            client_name = f"{client_name} — {rfq.company.strip()}"

        context = build_proposal_pdf_context(
            supplier=response_obj.supplier,
            document_title=title.strip() or "Proposition commerciale",
            subtitle=subtitle,
            client_name=client_name,
            blocks=response_obj.proposal_blocks,
            message=response_obj.message,
            proposed_price=response_obj.proposed_price,
            lead_time_days=response_obj.lead_time_days,
            currency=response_obj.currency,
        )
        html_content = render_proposal_pdf_html(**context)
        try:
            pdf_bytes = html_to_pdf_bytes(html_content)
        except RuntimeError:
            return Response(
                {"error": "Impossible de générer le PDF."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        filename = f"proposition-devis-{response_obj.pk}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response


def _catalog_product_for_rfq_quote(supplier, rfq):
    if rfq.product_id:
        product = (
            Product.objects.filter(pk=rfq.product_id, supplier=supplier, is_active=True).first()
        )
        if product:
            return product
    base = Product.objects.filter(supplier=supplier, is_active=True)
    if rfq.product_id and rfq.product.subcategory_id:
        match = base.filter(subcategory_id=rfq.product.subcategory_id).order_by(
            "-updated_at"
        ).first()
        if match:
            return match
    if rfq.product_id and rfq.product.category_id:
        match = base.filter(category_id=rfq.product.category_id).order_by(
            "-updated_at"
        ).first()
        if match:
            return match
    return base.order_by("-updated_at").first()


def _quote_response_order_specs(rfq, response_obj):
    parts = [f"Commande issue de la proposition de devis #{response_obj.pk}."]
    if (rfq.message or "").strip():
        parts.append(rfq.message.strip())
    if (response_obj.message or "").strip():
        parts.append(response_obj.message.strip())
    return "\n\n".join(parts)


def _serialize_quote_order_prefill(request, response_obj, *, product, existing_order=None):
    rfq = response_obj.quote_request
    unit_price = response_obj.proposed_price
    if unit_price is None and product.fixed_unit_price is not None:
        unit_price = product.fixed_unit_price
    qty = max(product.min_order_quantity, 1)
    buyer = resolve_buyer_for_rfq(rfq)
    wilaya = (getattr(buyer, "wilaya", None) or "").strip() if buyer else ""
    return {
        "quoteResponseId": response_obj.pk,
        "rfqId": rfq.pk,
        "productId": product.pk,
        "productName": product.name,
        "supplierId": response_obj.supplier_id,
        "supplierName": response_obj.supplier.company_name,
        "quantity": qty,
        "minOrderQuantity": product.min_order_quantity,
        "unitPrice": str(unit_price) if unit_price is not None else None,
        "leadTimeDays": response_obj.lead_time_days,
        "specifications": _quote_response_order_specs(rfq, response_obj),
        "existingOrderId": existing_order.pk if existing_order else None,
        "wilaya": wilaya,
        "rfqTitle": (rfq.title or "").strip() or None,
        "rfqMessage": rfq.message,
    }


class QuoteResponseOrderPrefillView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        response_obj = (
            QuoteResponse.objects.filter(pk=pk)
            .select_related(
                "supplier",
                "quote_request",
                "quote_request__product",
                "quote_request__buyer",
            )
            .first()
        )
        if not response_obj or not _user_can_access_quote_response(
            request.user, response_obj
        ):
            return Response(
                {"error": "Proposition introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if request.user.supplier_id:
            return Response(
                {"error": "Réservé à l'acheteur."},
                status=status.HTTP_403_FORBIDDEN,
            )

        existing = Order.objects.filter(quote_response=response_obj).first()
        rfq = response_obj.quote_request
        product = _catalog_product_for_rfq_quote(response_obj.supplier, rfq)
        if not product and not existing:
            return Response(
                {
                    "error": (
                        "Ce fournisseur n'a pas de produit catalogue actif. "
                        "Contactez-le via la messagerie."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if existing:
            product = existing.product
        return Response(
            _serialize_quote_order_prefill(
                request, response_obj, product=product, existing_order=existing
            )
        )


class QuoteResponseConfirmOrderView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        response_obj = (
            QuoteResponse.objects.filter(pk=pk)
            .select_related(
                "supplier",
                "quote_request",
                "quote_request__product",
                "quote_request__buyer",
            )
            .first()
        )
        if not response_obj or not _user_can_access_quote_response(
            request.user, response_obj
        ):
            return Response(
                {"error": "Proposition introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if request.user.supplier_id:
            return Response(
                {"error": "Réservé à l'acheteur."},
                status=status.HTTP_403_FORBIDDEN,
            )

        existing = Order.objects.filter(quote_response=response_obj).first()
        if existing:
            return Response(
                {
                    "order": BuyerOrderSerializer(
                        existing, context={"request": request}
                    ).data,
                    "alreadyExists": True,
                }
            )

        rfq = response_obj.quote_request
        product = _catalog_product_for_rfq_quote(response_obj.supplier, rfq)
        if not product:
            return Response(
                {
                    "error": (
                        "Ce fournisseur n'a pas de produit catalogue actif. "
                        "Contactez-le via la messagerie."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            quantity = int(request.data.get("quantity") or product.min_order_quantity)
        except (TypeError, ValueError):
            return Response(
                {"error": "Quantité invalide."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if quantity < product.min_order_quantity:
            return Response(
                {
                    "error": (
                        f"La quantité minimum est de {product.min_order_quantity} unités."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        wilaya = (request.data.get("wilaya") or request.user.wilaya or "").strip()
        valid_wilayas = {choice[0] for choice in Supplier.WILAYA_CHOICES}
        if wilaya not in valid_wilayas:
            return Response(
                {"error": "Wilaya invalide."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if request.user.wilaya != wilaya:
            User.objects.filter(pk=request.user.pk).update(wilaya=wilaya)

        specifications = (request.data.get("specifications") or "").strip()
        if not specifications:
            specifications = _quote_response_order_specs(rfq, response_obj)

        unit_price = response_obj.proposed_price
        if unit_price is None:
            unit_price = (
                product.fixed_unit_price if product.fixed_unit_price is not None else 0
            )

        with transaction.atomic():
            order = Order.objects.create(
                buyer=request.user,
                supplier=response_obj.supplier,
                product=product,
                quantity=quantity,
                unit_price=unit_price,
                specifications=specifications,
                commercial_snapshot=quote_response_snapshot(
                    response_obj,
                    buyer=request.user,
                    quantity=quantity,
                    unit_price=unit_price,
                ),
                quote_response=response_obj,
                status=Order.Status.PENDING,
                origin=Order.Origin.RFQ,
                delivery_wilaya=wilaya,
            )
            record_status_event(
                order,
                from_status="",
                to_status=Order.Status.PENDING,
                actor=request.user,
                actor_role="buyer",
                note=f"Commande depuis le devis boutique #{response_obj.pk}.",
            )
            register_order_commission(order)
            rbac.log_audit(
                request.user,
                "order.converted",
                entity_type="order",
                entity_id=order.pk,
                new_value={"origin": order.origin, "quoteResponseId": response_obj.pk},
                request=request,
            )
            response_obj.status = QuoteResponse.Status.ACCEPTED
            response_obj.save(update_fields=["status", "updated_at"])
            if rfq.status != RFQRequest.Status.ACCEPTED:
                rfq.status = RFQRequest.Status.ACCEPTED
                rfq.save(update_fields=["status", "updated_at"])

        try:
            crm_service.sync_from_order(order)
        except Exception:
            logger.exception("CRM sync failed for RFQ-born order %s", order.pk)

        log_buyer_event(
            request.user,
            Event.EventType.BUYER_ORDER_CREATED,
            {
                "order_id": order.id,
                "quote_response_id": response_obj.pk,
                "rfq_id": rfq.pk,
            },
        )
        order_note = (
            f"Commande depuis la proposition de devis #{response_obj.pk} — "
            f"{quantity} u. · {product.name}."
        )
        _create_buyer_message(response_obj.supplier, request.user, order_note)
        handle_order_created(response_obj.supplier_id, order.id, buyer=request.user)

        return Response(
            {
                "order": BuyerOrderSerializer(order, context={"request": request}).data,
                "alreadyExists": False,
            },
            status=status.HTTP_201_CREATED,
        )


class OrderProposalPdfView(APIView):
    permission_classes = [IsAuthenticated]
    renderer_classes = [PDFRenderer, JSONRenderer]

    def get(self, request, pk):
        response_obj = (
            OrderResponse.objects.filter(pk=pk)
            .select_related("supplier", "order", "order__product", "order__buyer")
            .first()
        )
        if not response_obj or not _user_can_access_order_response(request.user, response_obj):
            return Response({"error": "Document introuvable."}, status=status.HTTP_404_NOT_FOUND)

        order = response_obj.order
        title = order.product.name if order.product_id else "Proposition commerciale"
        subtitle = f"Commande — quantité {order.quantity}"
        if order.buyer_id and order.buyer:
            client_name = order.buyer.get_full_name() or order.buyer.username
        else:
            client_name = (order.guest_name or "").strip() or "Client"

        context = build_proposal_pdf_context(
            supplier=response_obj.supplier,
            document_title=title,
            subtitle=subtitle,
            client_name=client_name,
            blocks=response_obj.proposal_blocks,
            message=response_obj.message,
            proposed_price=response_obj.proposed_price,
            lead_time_days=response_obj.lead_time_days,
            currency=response_obj.currency,
        )
        html_content = render_proposal_pdf_html(**context)
        try:
            pdf_bytes = html_to_pdf_bytes(html_content)
        except RuntimeError:
            return Response(
                {"error": "Impossible de générer le PDF."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        filename = f"proposition-commande-{response_obj.pk}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response


def _create_buyer_message(supplier, buyer, content):
    Message.objects.create(
        supplier=supplier,
        buyer=buyer,
        sender=buyer,
        content=content,
        read_by_buyer=True,
        read_by_supplier=False,
    )


def _notify_supplier_order_cancelled(order):
    supplier = order.supplier
    product_name = order.product.name if order.product_id else "un produit"
    if order.buyer:
        buyer_label = (
            order.buyer.company_name
            or order.buyer.get_full_name()
            or order.buyer.email
            or "un client"
        ).strip()
    else:
        buyer_label = (order.guest_name or "un client").strip()

    note = (
        f"Commande #{order.pk} annulée par le client.\n"
        f"Produit : {product_name}\n"
        f"Quantité : {order.quantity}"
    )
    if order.buyer:
        _create_buyer_message(supplier, order.buyer, note)

    if supplier.email:
        send_mail(
            f"Commande annulée — {product_name}",
            (
                f"Bonjour {supplier.company_name},\n\n"
                f"{buyer_label} a annulé la commande #{order.pk}.\n\n"
                f"Produit : {product_name}\n"
                f"Quantité : {order.quantity}\n"
            ),
            None,
            [supplier.email],
            fail_silently=True,
        )


def _supplier_favorite_buyer_ids(supplier_id):
    supplier_favorites = set(
        BuyerFavoriteSupplier.objects.filter(supplier_id=supplier_id).values_list(
            "buyer_id", flat=True
        )
    )
    product_favorites = set(
        BuyerFavoriteProduct.objects.filter(
            product__supplier_id=supplier_id
        ).values_list("buyer_id", flat=True)
    )
    return supplier_favorites | product_favorites


def _supplier_contact_buyer_ids(supplier_id):
    message_buyers = set(
        Message.objects.filter(supplier_id=supplier_id).values_list("buyer_id", flat=True)
    )
    order_buyers = set(
        Order.objects.filter(supplier_id=supplier_id).values_list("buyer_id", flat=True)
    )
    rfq_buyers = set(
        RFQRequest.objects.filter(
            supplier_id=supplier_id, buyer_id__isnull=False
        ).values_list("buyer_id", flat=True)
    )
    favorite_buyers = _supplier_favorite_buyer_ids(supplier_id)
    crm_buyers = set(
        CrmContact.objects.filter(
            supplier_id=supplier_id, user_id__isnull=False, is_archived=False
        ).values_list("user_id", flat=True)
    )
    return message_buyers | order_buyers | rfq_buyers | favorite_buyers | crm_buyers


def _contact_last_activity(supplier_id, buyer_id):
    last_msg = (
        Message.objects.filter(supplier_id=supplier_id, buyer_id=buyer_id)
        .order_by("-created_at")
        .first()
    )
    if last_msg:
        return last_msg.created_at, last_msg.content

    last_order = (
        Order.objects.filter(supplier_id=supplier_id, buyer_id=buyer_id)
        .select_related("product")
        .order_by("-created_at")
        .first()
    )
    if last_order:
        product_name = last_order.product.name if last_order.product_id else "commande"
        return last_order.created_at, f"Commande — {product_name}"

    last_rfq = (
        RFQRequest.objects.filter(supplier_id=supplier_id, buyer_id=buyer_id)
        .order_by("-created_at")
        .first()
    )
    if last_rfq:
        return last_rfq.created_at, last_rfq.message or last_rfq.title or "Demande de devis"

    favorite_supplier = (
        BuyerFavoriteSupplier.objects.filter(
            supplier_id=supplier_id, buyer_id=buyer_id
        )
        .order_by("-created_at")
        .first()
    )
    if favorite_supplier:
        return favorite_supplier.created_at, "A ajouté votre entreprise aux favoris"

    favorite_product = (
        BuyerFavoriteProduct.objects.filter(
            product__supplier_id=supplier_id, buyer_id=buyer_id
        )
        .select_related("product")
        .order_by("-created_at")
        .first()
    )
    if favorite_product:
        return (
            favorite_product.created_at,
            f"A ajouté {favorite_product.product.name} aux favoris",
        )

    crm_contact = (
        CrmContact.objects.filter(supplier_id=supplier_id, user_id=buyer_id)
        .order_by("-last_interaction_at", "-created_at")
        .first()
    )
    if crm_contact:
        return (
            crm_contact.last_interaction_at or crm_contact.created_at,
            "Contact CRM",
        )

    return None, ""


def _buyer_linked_to_supplier(supplier_id, buyer_id):
    if Message.objects.filter(supplier_id=supplier_id, buyer_id=buyer_id).exists():
        return True
    if Order.objects.filter(supplier_id=supplier_id, buyer_id=buyer_id).exists():
        return True
    if RFQRequest.objects.filter(
        supplier_id=supplier_id, buyer_id=buyer_id
    ).exists():
        return True
    if BuyerFavoriteSupplier.objects.filter(
        supplier_id=supplier_id, buyer_id=buyer_id
    ).exists():
        return True
    if BuyerFavoriteProduct.objects.filter(
        product__supplier_id=supplier_id, buyer_id=buyer_id
    ).exists():
        return True
    return CrmContact.objects.filter(
        supplier_id=supplier_id, user_id=buyer_id, is_archived=False
    ).exists()


def _mark_messages_read_by_supplier(supplier_id, buyer_id, reader_user_id):
    now = timezone.now()
    Message.objects.filter(
        supplier_id=supplier_id,
        buyer_id=buyer_id,
        read_by_supplier=False,
    ).exclude(sender_id=reader_user_id).update(
        read_by_supplier=True,
        read_by_supplier_at=now,
    )


def _mark_messages_read_by_buyer(supplier_id, buyer_id, reader_user_id):
    now = timezone.now()
    Message.objects.filter(
        supplier_id=supplier_id,
        buyer_id=buyer_id,
        read_by_buyer=False,
    ).exclude(sender_id=reader_user_id).update(
        read_by_buyer=True,
        read_by_buyer_at=now,
    )


class SupplierContactsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        supplier_id = request.user.supplier_id
        buyer_ids = _supplier_contact_buyer_ids(supplier_id)
        buyers = User.objects.filter(id__in=buyer_ids)
        buyers_by_id = {buyer.id: buyer for buyer in buyers}

        contacts = []
        for buyer_id in buyer_ids:
            buyer = buyers_by_id.get(buyer_id)
            if not buyer:
                continue

            last_at, last_text = _contact_last_activity(supplier_id, buyer_id)
            unread_count = Message.objects.filter(
                supplier_id=supplier_id,
                buyer_id=buyer_id,
                read_by_supplier=False,
            ).exclude(sender_id=request.user.id).count()

            contacts.append(
                {
                    "buyerId": buyer_id,
                    "name": contact_display_name(buyer),
                    "company": buyer.company_name or "",
                    "profilePicture": _buyer_profile_picture_url(request, buyer),
                    "lastMessage": (last_text or "")[:160],
                    "lastMessageAt": last_at,
                    "unreadCount": unread_count,
                }
            )

        contacts.sort(
            key=lambda item: item["lastMessageAt"].timestamp()
            if item["lastMessageAt"] is not None
            and hasattr(item["lastMessageAt"], "timestamp")
            else 0,
            reverse=True,
        )

        return Response(SupplierContactSerializer(contacts, many=True).data)


class SupplierContactMessagesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, buyer_id):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        supplier_id = request.user.supplier_id
        if not _buyer_linked_to_supplier(supplier_id, buyer_id):
            return Response(
                {"error": "Contact not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        _mark_messages_read_by_supplier(supplier_id, buyer_id, request.user.id)

        qs = Message.objects.filter(
            supplier_id=supplier_id,
            buyer_id=buyer_id,
        ).select_related("sender", "sender__supplier").order_by("created_at")

        return Response(
            SupplierMessageSerializer(
                qs, many=True, context={"request": request}
            ).data
        )

    def post(self, request, buyer_id):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        supplier_id = request.user.supplier_id
        if not _buyer_linked_to_supplier(supplier_id, buyer_id):
            return Response(
                {"error": "Contact not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = SupplierMessageCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        message = Message.objects.create(
            supplier_id=supplier_id,
            buyer_id=buyer_id,
            sender=request.user,
            content=serializer.validated_data["content"],
            read_by_supplier=True,
            read_by_buyer=False,
        )
        handle_supplier_message_reply(
            supplier_id,
            buyer_id,
            message,
            actor_user=request.user,
        )

        return Response(
            SupplierMessageSerializer(message, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class BuyerSupplierMessagesView(APIView):
    """Buyer ↔ supplier direct conversation on a supplier profile."""

    permission_classes = [IsAuthenticated]

    def _get_supplier(self, pk):
        try:
            return Supplier.objects.get(pk=pk, is_active=True, is_ghost=False)
        except Supplier.DoesNotExist:
            return None

    def get(self, request, pk):
        supplier = self._get_supplier(pk)
        if not supplier:
            return Response(
                {"error": "Supplier not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.user.supplier_id == supplier.id:
            return Response(
                {"error": "Cannot message your own supplier account"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _mark_messages_read_by_buyer(supplier.id, request.user.id, request.user.id)

        qs = (
            Message.objects.filter(supplier=supplier, buyer=request.user)
            .select_related("sender", "sender__supplier")
            .order_by("created_at")
        )

        return Response(
            SupplierMessageSerializer(
                qs, many=True, context={"request": request}
            ).data
        )

    def post(self, request, pk):
        supplier = self._get_supplier(pk)
        if not supplier:
            return Response(
                {"error": "Supplier not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.user.supplier_id == supplier.id:
            return Response(
                {"error": "Cannot message your own supplier account"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = SupplierMessageCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        message = Message.objects.create(
            supplier=supplier,
            buyer=request.user,
            sender=request.user,
            content=serializer.validated_data["content"],
            read_by_buyer=True,
            read_by_supplier=False,
        )

        return Response(
            SupplierMessageSerializer(message, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


def _require_buyer(user):
    return not user.supplier_id


def _resolve_subcategory_for_category_intent(request):
    """Require subcategory_id and a parent category filter (category query param)."""
    raw = request.query_params.get("subcategory_id")
    if not raw or not str(raw).isdigit():
        return None
    category_param = (request.query_params.get("category") or "").strip()
    if not category_param:
        return None
    subcategory = (
        Category.objects.filter(pk=int(raw), parent__isnull=False)
        .select_related("parent")
        .first()
    )
    if subcategory is None or subcategory.parent is None:
        return None
    parent = subcategory.parent
    needle = category_param.lower()
    parent_names = {
        (parent.name_en or "").lower(),
        (parent.name_fr or "").lower(),
        (parent.name_ar or "").lower(),
    }
    if not any(
        needle in name or name in needle for name in parent_names if name
    ):
        return None
    if not subcategory_belongs_to_category(parent, subcategory):
        return None
    return subcategory


def _maybe_sync_category_intent_from_listing(request, supplier_ids):
    user = request.user
    if not user.is_authenticated or user.supplier_id:
        return

    from user.search_vocabulary import resolve_subcategories_from_query

    subcategories = []
    seen_sub_ids: set[int] = set()

    filtered = _resolve_subcategory_for_category_intent(request)
    if filtered is not None:
        subcategories.append(filtered)
        seen_sub_ids.add(filtered.pk)

    query = (request.query_params.get("q") or "").strip()
    if query:
        for sub in resolve_subcategories_from_query(query):
            if sub.pk not in seen_sub_ids:
                subcategories.append(sub)
                seen_sub_ids.add(sub.pk)

    for subcategory in subcategories:
        crm_service.sync_category_intent_for_search_results(
            user, supplier_ids, subcategory
        )


def _own_supplier_id(user):
    return getattr(user, "supplier_id", None)


def _annotate_buyer_favorite_products(qs, user):
    if not user.is_authenticated or user.supplier_id:
        return qs, False
    fav_ids = list(
        BuyerFavoriteProduct.objects.filter(buyer=user).values_list("product_id", flat=True)
    )
    if not fav_ids:
        return qs, False
    return qs.annotate(
        _favorite_rank=Case(
            When(pk__in=fav_ids, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ), True


def _annotate_buyer_favorite_suppliers(qs, user):
    if not user.is_authenticated:
        return qs, False
    fav_ids = list(
        BuyerFavoriteSupplier.objects.filter(buyer=user).values_list("supplier_id", flat=True)
    )
    if not fav_ids:
        return qs, False
    return qs.annotate(
        _favorite_rank=Case(
            When(pk__in=fav_ids, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ), True


def _buyer_contact_supplier_ids(buyer_id):
    message_suppliers = set(
        Message.objects.filter(buyer_id=buyer_id).values_list("supplier_id", flat=True)
    )
    order_suppliers = set(
        Order.objects.filter(buyer_id=buyer_id).values_list("supplier_id", flat=True)
    )
    rfq_suppliers = set(
        RFQRequest.objects.filter(
            buyer_id=buyer_id, supplier_id__isnull=False
        ).values_list("supplier_id", flat=True)
    )
    return message_suppliers | order_suppliers | rfq_suppliers


def _buyer_contact_last_activity(buyer_id, supplier_id):
    last_message = (
        Message.objects.filter(buyer_id=buyer_id, supplier_id=supplier_id)
        .order_by("-created_at")
        .first()
    )
    if last_message:
        return last_message.created_at, last_message.content
    return None, ""


class BuyerUpgradeSupplierView(APIView):
    """Upgrade a logged-in buyer account to a supplier profile."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.supplier_id:
            return Response(
                {
                    "alreadySupplier": True,
                    "supplierId": user.supplier_id,
                }
            )

        return Response(
            {
                "email": user.email,
                "name": _buyer_display_name(user),
                "companyName": user.company_name or "",
                "phone": user.phone_number or "",
                "wilaya": user.wilaya or "",
                "address": user.address or "",
                "wilayaOptions": [
                    {"value": value, "label": label}
                    for value, label in Supplier.WILAYA_CHOICES
                ],
            }
        )

    def post(self, request):
        user = request.user
        if user.supplier_id:
            return Response(
                {"error": "You are already registered as a supplier."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if Supplier.objects.filter(email=user.email).exists():
            return Response(
                {
                    "error": (
                        "A supplier profile already exists for this email. "
                        "Please contact support."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = BuyerUpgradeSupplierSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        supplier = Supplier.objects.create(
            company_name=data["companyName"],
            email=user.email,
            whatsapp_number=data.get("whatsappNumber") or user.phone_number or "",
            wilaya=data.get("wilaya") or user.wilaya or None,
            address=data.get("address") or user.address or "",
            website=data.get("website") or None,
            presentation=data.get("presentation") or "",
            type=data.get("type") or None,
        )

        category_ids = data.get("categoryIds") or []
        subcategory_ids = data.get("subcategoryIds") or []
        if category_ids:
            supplier.interested_categories.set(category_ids)
        if subcategory_ids:
            supplier.interested_subcategories.set(subcategory_ids)

        user.supplier = supplier
        user.save(update_fields=["supplier"])
        handle_supplier_created(supplier, actor_user=user)
        handle_auth_login(user, method="email")

        return Response(_auth_payload(user), status=status.HTTP_201_CREATED)


def _buyer_profile_picture_url(request, user):
    if not getattr(user, "profile_picture", None):
        return None
    try:
        url = user.profile_picture.url
    except (ValueError, OSError):
        return None
    if request is not None:
        return request.build_absolute_uri(url)
    return url


class BuyerProfileView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _serialize(self, user, request=None):
        return {
            "id": user.id,
            "name": _buyer_display_name(user),
            "email": user.email,
            "company": user.company_name or "",
            "phone": user.phone_number or "",
            "wilaya": user.wilaya or "",
            "address": user.address or "",
            "natureOfActivity": user.nature_of_activity or "",
            "annualPurchaseVolume": user.annual_purchase_volume or "",
            "profilePicture": _buyer_profile_picture_url(request, user),
            "wilayaOptions": [
                {"value": value, "label": label}
                for value, label in Supplier.WILAYA_CHOICES
            ],
            "interestedSubcategoryIds": list(
                user.interested_subcategories.values_list("id", flat=True)
            ),
        }

    def get(self, request):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(BuyerProfileDetailSerializer(self._serialize(request.user, request)).data)

    def patch(self, request):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = BuyerProfileDetailSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        data = serializer.validated_data
        previous_company = (user.company_name or "").strip()
        if "name" in data:
            user.first_name = data["name"].strip()
            user.last_name = ""
        if "company" in data:
            user.company_name = data["company"] or None
        if "phone" in data:
            user.phone_number = data["phone"] or None
        if "wilaya" in data:
            user.wilaya = data["wilaya"] or None
        if "address" in data:
            user.address = data["address"] or None
        if "natureOfActivity" in data:
            user.nature_of_activity = data["natureOfActivity"] or None
        if "annualPurchaseVolume" in data:
            user.annual_purchase_volume = data["annualPurchaseVolume"] or None

        profile_picture = request.FILES.get("profilePicture")
        if profile_picture:
            user.profile_picture = profile_picture

        user.save()
        if not previous_company and (user.company_name or "").strip():
            from buyer_space.wallet import grant_company_registration

            grant_company_registration(user)
        return Response(BuyerProfileDetailSerializer(self._serialize(user, request)).data)


class SupplierProfileEditView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _get_supplier(self, request):
        if not request.user.supplier_id:
            return None
        return Supplier.objects.prefetch_related(
            "interested_categories",
            "interested_subcategories",
        ).filter(pk=request.user.supplier_id).first()

    def _coerce_id_list(self, raw):
        if raw is None:
            return None
        if isinstance(raw, list):
            return [int(v) for v in raw]
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw:
                return []
            if raw.startswith("["):
                return [int(v) for v in json.loads(raw)]
            return [int(v) for v in raw.split(",") if v.strip()]
        return [int(raw)]

    def _parse_id_list(self, request, key):
        if hasattr(request.data, "getlist"):
            values = request.data.getlist(key)
            if values:
                if len(values) == 1:
                    return self._coerce_id_list(values[0])
                return [int(v) for v in values if str(v).strip()]
        return self._coerce_id_list(request.data.get(key))

    def get(self, request):
        supplier = self._get_supplier(request)
        if not supplier:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        data = SupplierProfileEditSerializer(
            supplier, context={"request": request}
        ).data
        data["wilayaOptions"] = [
            {"value": value, "label": label}
            for value, label in Supplier.WILAYA_CHOICES
        ]
        return Response(data)

    def patch(self, request):
        supplier = self._get_supplier(request)
        if not supplier:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        payload = {}
        for api_key in (
            "companyName",
            "whatsappNumber",
            "wilaya",
            "address",
            "website",
            "presentation",
            "presentationHtml",
            "type",
            "brandColor",
            "brandButtonColor",
            "brandNeutralColor",
        ):
            if api_key in request.data:
                payload[api_key] = request.data.get(api_key) or ""

        serializer = SupplierProfileEditSerializer(
            supplier,
            data=payload,
            partial=True,
            context={
                "request": request,
                "category_ids": self._parse_id_list(request, "categoryIds"),
                "subcategory_ids": self._parse_id_list(request, "subcategoryIds"),
                "logo_file": request.FILES.get("logo"),
            },
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        previous = {
            "email": supplier.email,
            "website": supplier.website,
            "whatsapp_number": supplier.whatsapp_number,
            "wilaya": supplier.wilaya,
            "address": supplier.address,
        }
        supplier = serializer.save()
        handle_profile_updated(
            supplier,
            actor_user=request.user,
            previous=previous,
        )

        return Response(
            SupplierProfileEditSerializer(
                supplier, context={"request": request}
            ).data
        )


class SupplierSettingsView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def _get_supplier(self, request):
        if not request.user.supplier_id:
            return None
        return Supplier.objects.filter(pk=request.user.supplier_id).first()

    def get(self, request):
        supplier = self._get_supplier(request)
        if not supplier:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        settings_obj = ensure_supplier_settings(supplier)
        return Response(
            SupplierSettingsSerializer(settings_obj).data
        )

    def patch(self, request):
        supplier = self._get_supplier(request)
        if not supplier:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        settings_obj = ensure_supplier_settings(supplier)
        serializer = SupplierSettingsSerializer(
            settings_obj,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(SupplierSettingsSerializer(settings_obj).data)


def _expire_stale_buyer_orders(qs):
    now = timezone.now()
    stale_ids = []
    for order in qs.filter(status__in=[Order.Status.PENDING, Order.Status.IN_NEGOTIATION]):
        lead_days = getattr(order.product, "lead_time_days", None) or 7
        if order.created_at + timedelta(days=max(lead_days, 7)) <= now:
            stale_ids.append(order.id)
    if stale_ids:
        Order.objects.filter(id__in=stale_ids).update(
            status=Order.Status.EXPIRED,
            updated_at=now,
        )


class BuyerOrdersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        qs = (
            Order.objects.filter(buyer=request.user)
            .select_related("product", "supplier")
            .order_by("-created_at")
        )
        _expire_stale_buyer_orders(qs)
        qs = (
            Order.objects.filter(buyer=request.user)
            .select_related(
                "product", "variant", "supplier", "quote_proposal", "supplier_review"
            )
            .prefetch_related("status_events")
            .order_by("-created_at")
        )
        return Response(
            BuyerOrderSerializer(qs, many=True, context={"request": request}).data
        )


class BuyerOrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            order = Order.objects.select_related("product", "supplier").get(
                pk=pk, buyer=request.user
            )
        except Order.DoesNotExist:
            return Response({"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND)

        if request.data.get("action") == "rate":
            try:
                save_order_review(
                    order,
                    request.user,
                    request.data.get("rating"),
                    request.data.get("comment") or "",
                )
            except OrderReviewError as exc:
                status_code = (
                    status.HTTP_400_BAD_REQUEST
                    if exc.code == "invalid_rating"
                    else status.HTTP_409_CONFLICT
                )
                return Response(
                    {"error": str(exc), "code": exc.code},
                    status=status_code,
                )
            order = Order.objects.select_related(
                "product", "supplier", "supplier_review"
            ).get(pk=order.pk)
            return Response(BuyerOrderSerializer(order, context={"request": request}).data)

        if request.data.get("action") == "cancel":
            if order.status != Order.Status.CANCELLED:
                if order.status not in BUYER_CANCELLABLE:
                    return Response(
                        {
                            "error": (
                                "Cette commande est déjà en cours d'exécution. "
                                "Contactez le fournisseur pour l'annuler."
                            ),
                            "code": "order_not_cancellable",
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
                previous = order.status
                order.status = Order.Status.CANCELLED
                order.save(update_fields=["status", "updated_at"])
                record_status_event(
                    order,
                    from_status=previous,
                    to_status=Order.Status.CANCELLED,
                    actor=request.user,
                    actor_role="buyer",
                    note=(request.data.get("note") or "").strip(),
                )
                try:
                    crm_service.sync_order_execution(
                        order, note="Commande annulée par l'acheteur."
                    )
                except Exception:
                    logger.exception("CRM sync failed for buyer-cancelled order %s", order.pk)
                _notify_supplier_order_cancelled(order)

        return Response(
            BuyerOrderSerializer(order, context={"request": request}).data
        )


class BuyerContactsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        buyer_id = request.user.id
        supplier_ids = _buyer_contact_supplier_ids(buyer_id)
        suppliers = Supplier.objects.filter(id__in=supplier_ids, is_active=True, is_ghost=False)
        suppliers_by_id = {supplier.id: supplier for supplier in suppliers}

        rows = []
        for supplier_id in supplier_ids:
            supplier = suppliers_by_id.get(supplier_id)
            if not supplier:
                continue

            last_at, last_text = _buyer_contact_last_activity(buyer_id, supplier_id)
            unread = (
                Message.objects.filter(
                    buyer_id=buyer_id,
                    supplier_id=supplier_id,
                    read_by_buyer=False,
                )
                .exclude(sender_id=buyer_id)
                .count()
            )

            logo = None
            if supplier.logo:
                logo = request.build_absolute_uri(supplier.logo.url)

            rows.append(
                {
                    "supplierId": supplier_id,
                    "name": supplier.company_name,
                    "company": supplier.company_name,
                    "logo": logo,
                    "lastMessage": last_text or "",
                    "lastMessageAt": last_at,
                    "unreadCount": unread,
                }
            )

        rows.sort(key=lambda row: row["lastMessageAt"] or datetime.min, reverse=True)
        return Response(BuyerContactSerializer(rows, many=True).data)


class BuyerInterestSubcategoriesView(APIView):
    """Buyer-declared subcategories of interest (qualified CRM signals)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        ids = list(
            request.user.interested_subcategories.values_list("id", flat=True)
        )
        return Response({"subcategoryIds": ids})

    def put(self, request):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        raw = request.data.get("subcategoryIds")
        if not isinstance(raw, list):
            return Response(
                {"error": "subcategoryIds must be a list."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            ids = [int(v) for v in raw]
        except (TypeError, ValueError):
            return Response(
                {"error": "Invalid subcategory id."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ids = list(dict.fromkeys(ids))
        valid = Category.objects.filter(id__in=ids, parent__isnull=False)
        if valid.count() != len(set(ids)):
            return Response(
                {"error": "Une ou plusieurs sous-catégories sont invalides."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = request.user
        previous = set(
            user.interested_subcategories.values_list("id", flat=True)
        )
        user.interested_subcategories.set(valid)
        added = set(ids) - previous
        for sub in valid.filter(id__in=added):
            crm_service.sync_suppliers_for_buyer_interest_subcategory(user, sub)
        return Response({"subcategoryIds": ids})


class BuyerSearchHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        qs = BuyerSearchHistory.objects.filter(buyer=request.user)
        month = request.query_params.get("month")
        year = request.query_params.get("year")
        if month and year:
            qs = qs.filter(
                created_at__month=int(month),
                created_at__year=int(year),
            )

        return Response(BuyerSearchHistorySerializer(qs, many=True).data)

    def post(self, request):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        query = (request.data.get("query") or "").strip()
        if not query:
            return Response(
                {"error": "query is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        recent_duplicate = (
            BuyerSearchHistory.objects.filter(
                buyer=request.user,
                query__iexact=query,
                created_at__gte=timezone.now() - timedelta(seconds=30),
            )
            .order_by("-created_at")
            .first()
        )
        if recent_duplicate:
            return Response(
                BuyerSearchHistorySerializer(recent_duplicate).data,
                status=status.HTTP_200_OK,
            )

        entry = BuyerSearchHistory.objects.create(
            buyer=request.user,
            query=query,
            rating=request.data.get("rating"),
        )
        return Response(
            BuyerSearchHistorySerializer(entry).data,
            status=status.HTTP_201_CREATED,
        )


class BuyerSearchHistoryDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            entry = BuyerSearchHistory.objects.get(pk=pk, buyer=request.user)
        except BuyerSearchHistory.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        rating = request.data.get("rating")
        if rating is not None:
            entry.rating = int(rating)
            entry.save(update_fields=["rating"])
        return Response(BuyerSearchHistorySerializer(entry).data)

    def delete(self, request, pk):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        deleted, _ = BuyerSearchHistory.objects.filter(
            pk=pk, buyer=request.user
        ).delete()
        if not deleted:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _visit_context_id(context, key):
    raw = (context or {}).get(key)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _visit_media_url(request, file_field):
    if not file_field:
        return None
    try:
        url = file_field.url
    except ValueError:
        return None
    if request is not None and str(url).startswith("/"):
        return request.build_absolute_uri(url)
    return url


class BuyerVisitHistoryView(APIView):
    """Pages the signed-in buyer actually opened: boutiques and products."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        kind = (request.query_params.get("kind") or "boutique").strip().lower()
        if kind not in {"boutique", "produit"}:
            return Response(
                {"error": "kind must be boutique or produit"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        event_type = (
            Event.EventType.BUYER_PRODUCT_VIEW
            if kind == "produit"
            else Event.EventType.BUYER_SUPPLIER_VIEW
        )
        events = list(
            Event.objects.filter(actor_user=request.user, event_type=event_type)
            .order_by("-created_at")[:200]
        )
        if kind == "boutique":
            events = [
                event
                for event in events
                if (event.context or {}).get("source") != "product_page"
            ]

        supplier_ids = {
            supplier_id
            for event in events
            if (supplier_id := _visit_context_id(event.context, "supplier_id"))
        }
        product_ids = {
            product_id
            for event in events
            if (product_id := _visit_context_id(event.context, "product_id"))
        }
        suppliers = {
            supplier.id: supplier
            for supplier in Supplier.objects.filter(id__in=supplier_ids, is_ghost=False)
        }
        products = {
            product.id: product
            for product in Product.objects.filter(id__in=product_ids, is_active=True)
            .select_related("supplier")
            .prefetch_related("images")
        }

        rows = []
        for event in events:
            supplier_id = _visit_context_id(event.context, "supplier_id")
            product_id = _visit_context_id(event.context, "product_id")
            supplier = suppliers.get(supplier_id)
            product = products.get(product_id) if kind == "produit" else None
            if kind == "produit":
                if product is None:
                    continue
                supplier = product.supplier
                image = next(iter(product.images.all()), None)
                thumbnail = _visit_media_url(request, image.image if image else None)
                name = product.name
            else:
                if supplier is None:
                    continue
                thumbnail = _visit_media_url(request, supplier.logo)
                name = supplier.company_name
            seen = timezone.localtime(event.created_at)
            rows.append(
                {
                    "id": event.id,
                    "kind": kind,
                    "name": name,
                    "supplierId": supplier.id if supplier else supplier_id,
                    "supplierName": supplier.company_name if supplier else "",
                    "productId": product.id if product else None,
                    "thumbnail": thumbnail,
                    "date": seen.strftime("%d/%m/%Y"),
                    "time": seen.strftime("%H:%M"),
                }
            )
        return Response(rows)


class BuyerFavoritesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (
            BuyerFavoriteSupplier.objects.filter(buyer=request.user, supplier__is_ghost=False)
            .select_related("supplier")
            .prefetch_related("supplier__interested_categories")
        )
        return Response(
            BuyerFavoriteSupplierSerializer(
                qs, many=True, context={"request": request}
            ).data
        )

    def post(self, request):
        supplier_id = request.data.get("supplierId")
        if not supplier_id:
            return Response(
                {"error": "supplierId is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            supplier = Supplier.objects.get(pk=supplier_id, is_active=True, is_ghost=False)
        except Supplier.DoesNotExist:
            return Response(
                {"error": "Supplier not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if _own_supplier_id(request.user) == supplier.pk:
            return Response(
                {"error": "Vous ne pouvez pas ajouter votre propre boutique aux favoris."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        favorite, created = BuyerFavoriteSupplier.objects.get_or_create(
            buyer=request.user,
            supplier=supplier,
        )
        if created:
            handle_supplier_favorited(
                supplier.id,
                buyer=request.user,
                source="supplier",
            )
            try:
                crm_service.sync_from_favorite(supplier, request.user)
            except Exception:
                logger.exception(
                    "CRM favorite sync failed supplier=%s buyer=%s",
                    supplier.pk,
                    request.user.pk,
                )
        return Response(
            BuyerFavoriteSupplierSerializer(
                favorite, context={"request": request}
            ).data,
            status=status.HTTP_201_CREATED,
        )


class BuyerProductFavoritesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        favorites = (
            BuyerFavoriteProduct.objects.filter(buyer=request.user)
            .select_related(
                "product",
                "product__supplier",
                "product__supplier__settings",
                "product__category",
                "product__subcategory",
                "product__material",
            )
            .prefetch_related(
                "product__images",
                "product__search_tags",
                "product__attribute_values__attribute",
            )
            .order_by("-created_at")
        )
        products = [
            fav.product
            for fav in favorites
            if fav.product.is_active and not fav.product.supplier.is_ghost
        ]
        product_ids = [product.id for product in products]
        return Response(
            {
                "productIds": product_ids,
                "products": ProductListSerializer(
                    products, many=True, context={"request": request}
                ).data,
            }
        )

    def post(self, request):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        product_id = request.data.get("productId")
        if not product_id:
            return Response(
                {"error": "productId is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            product = Product.objects.select_related("supplier").get(
                pk=product_id,
                is_active=True,
                supplier__is_ghost=False,
            )
        except Product.DoesNotExist:
            return Response(
                {"error": "Product not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        favorite, created = BuyerFavoriteProduct.objects.get_or_create(
            buyer=request.user,
            product=product,
        )
        if created:
            handle_supplier_favorited(
                product.supplier_id,
                buyer=request.user,
                source="product",
                product_id=product.id,
            )
            try:
                crm_service.sync_from_favorite(
                    product.supplier,
                    request.user,
                    product=product,
                )
            except Exception:
                logger.exception(
                    "CRM favorite sync failed supplier=%s buyer=%s product=%s",
                    product.supplier_id,
                    request.user.pk,
                    product.pk,
                )
        return Response(
            {
                "id": favorite.id,
                "productId": product.id,
                "supplierId": product.supplier_id,
                "created": created,
            },
            status=status.HTTP_201_CREATED,
        )


class BuyerProductFavoriteDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, product_id):
        if not _require_buyer(request.user):
            return Response(
                {"error": "Buyer account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        deleted, _ = BuyerFavoriteProduct.objects.filter(
            buyer=request.user, product_id=product_id
        ).delete()
        if not deleted:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class BuyerFavoriteDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, supplier_id):
        deleted, _ = BuyerFavoriteSupplier.objects.filter(
            buyer=request.user, supplier_id=supplier_id
        ).delete()
        if not deleted:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupplierFavoritesView(APIView):
    """Buyers who favorited this supplier or one of its products."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        supplier_id = request.user.supplier_id
        entries = []

        supplier_favorites = (
            BuyerFavoriteSupplier.objects.filter(supplier_id=supplier_id)
            .select_related("buyer")
            .order_by("-created_at")
        )
        for favorite in supplier_favorites:
            buyer = favorite.buyer
            entries.append(
                {
                    "id": f"supplier-{favorite.id}",
                    "kind": "supplier",
                    "buyerId": buyer.id,
                    "buyerName": contact_display_name(buyer),
                    "buyerCompany": buyer.company_name or "",
                    "profilePicture": _buyer_profile_picture_url(request, buyer),
                    "productId": None,
                    "productName": None,
                    "createdAt": favorite.created_at,
                }
            )

        product_favorites = (
            BuyerFavoriteProduct.objects.filter(product__supplier_id=supplier_id)
            .select_related("buyer", "product")
            .order_by("-created_at")
        )
        for favorite in product_favorites:
            product = favorite.product
            if not product.is_active:
                continue
            buyer = favorite.buyer
            entries.append(
                {
                    "id": f"product-{favorite.id}",
                    "kind": "product",
                    "buyerId": buyer.id,
                    "buyerName": contact_display_name(buyer),
                    "buyerCompany": buyer.company_name or "",
                    "profilePicture": _buyer_profile_picture_url(request, buyer),
                    "productId": product.id,
                    "productName": product.name,
                    "createdAt": favorite.created_at,
                }
            )

        entries.sort(key=lambda row: row["createdAt"], reverse=True)

        User.objects.filter(pk=request.user.pk).update(
            favorites_notifications_seen_at=timezone.now()
        )

        return Response(
            SupplierFavoriteActivitySerializer(entries, many=True).data
        )


class SupplierFavoritesNotificationsSeenView(APIView):
    """Mark supplier favorite notifications as seen (navbar badge)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.supplier_id:
            return Response(
                {"error": "Supplier account required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        User.objects.filter(pk=request.user.pk).update(
            favorites_notifications_seen_at=timezone.now()
        )
        return Response({"ok": True})


class OrdersNotificationsSeenView(APIView):
    """Mark order notifications as seen (navbar badge)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        User.objects.filter(pk=request.user.pk).update(
            orders_notifications_seen_at=timezone.now()
        )
        return Response({"ok": True})


class RatingsNotificationsSeenView(APIView):
    """Mark buyer-rating notifications as read (navbar badge)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        AppNotification.objects.filter(
            user_id=request.user.pk,
            kind=AppNotification.Kind.SUPPLIER_RATED,
            is_read=False,
        ).update(is_read=True)
        return Response({"ok": True})


class UserNotificationsSummaryView(APIView):
    """Aggregated notification counts for the navbar user menu."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = User.objects.only(
            "id",
            "supplier_id",
            "last_login",
            "favorites_notifications_seen_at",
            "orders_notifications_seen_at",
        ).get(pk=request.user.pk)
        since = user.last_login
        if since is None:
            since = timezone.now() - timedelta(days=30)
        orders_since = user.orders_notifications_seen_at or since

        payload = {
            "role": "supplier" if user.supplier_id else "buyer",
            "messages": 0,
            "orders": 0,
            "rfqs": 0,
            "favorites": 0,
            "ratings": 0,
            "total": 0,
            "rfqNotices": [],
            "ratingNotices": [],
        }

        if user.supplier_id:
            supplier_id = user.supplier_id
            payload["messages"] = Message.objects.filter(
                supplier_id=supplier_id,
                read_by_supplier=False,
            ).exclude(sender_id=user.id).count()
            payload["orders"] = Order.objects.filter(supplier_id=supplier_id).filter(
                Q(status=Order.Status.PENDING, created_at__gt=orders_since)
                | Q(status=Order.Status.CANCELLED, updated_at__gt=orders_since)
            ).count()
            payload["rfqs"] = RFQRequest.objects.filter(
                supplier_id=supplier_id,
                status__in=[
                    RFQRequest.Status.SUBMITTED,
                    RFQRequest.Status.IN_REVIEW,
                ],
            ).count() + AppNotification.objects.filter(
                user_id=user.id,
                is_read=False,
                kind__in=[
                    AppNotification.Kind.QUOTE_INVITED,
                    AppNotification.Kind.QUOTE_LOST,
                ],
            ).count() + QuoteDemand.objects.filter(
                Q(selections__supplier_id=supplier_id)
                | Q(engagements__supplier_id=supplier_id),
                status=QuoteDemand.Status.OPEN,
            ).exclude(proposals__supplier_id=supplier_id).distinct().count()
            favorites_since = user.favorites_notifications_seen_at
            if favorites_since is None:
                favorites_since = user.last_login
            if favorites_since is None:
                favorites_since = timezone.now() - timedelta(days=30)
            supplier_favorites = BuyerFavoriteSupplier.objects.filter(
                supplier_id=supplier_id,
                created_at__gt=favorites_since,
            ).count()
            product_favorites = BuyerFavoriteProduct.objects.filter(
                product__supplier_id=supplier_id,
                created_at__gt=favorites_since,
            ).count()
            payload["favorites"] = supplier_favorites + product_favorites
            rating_notes = AppNotification.objects.filter(
                user_id=user.id,
                is_read=False,
                kind=AppNotification.Kind.SUPPLIER_RATED,
            )
            payload["ratings"] = rating_notes.count()
            payload["ratingNotices"] = list(
                rating_notes.order_by("-created_at").values("id", "title", "body", "link")[:8]
            )
        else:
            payload["messages"] = Message.objects.filter(
                buyer_id=user.id,
                read_by_buyer=False,
            ).exclude(sender_id=user.id).count()
            payload["orders"] = Order.objects.filter(
                buyer_id=user.id,
                updated_at__gt=orders_since,
            ).exclude(
                status__in=[Order.Status.PENDING, Order.Status.CANCELLED]
            ).count()
            payload["rfqs"] = RFQRequest.objects.filter(
                buyer_id=user.id,
                status=RFQRequest.Status.QUOTED,
                updated_at__gt=since,
            ).count() + AppNotification.objects.filter(
                user_id=user.id,
                is_read=False,
                kind__in=[
                    AppNotification.Kind.QUOTE_PROPOSAL,
                    AppNotification.Kind.RFQ_IN_REVIEW,
                ],
            ).count()
            payload["rfqNotices"] = list(
                AppNotification.objects.filter(
                    user_id=user.id,
                    is_read=False,
                    kind=AppNotification.Kind.RFQ_IN_REVIEW,
                )
                .order_by("-created_at")
                .values("id", "title", "body", "link")[:5]
            )

        payload["total"] = (
            payload["messages"]
            + payload["orders"]
            + payload["rfqs"]
            + payload["favorites"]
            + payload["ratings"]
        )
        return Response(payload)
