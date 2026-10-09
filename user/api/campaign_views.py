"""Supplier campaign and extra-template endpoints."""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import campaigns
from user.api.serializers import (
    SupplierListSerializer,
    _normalize_proposal_templates,
    ensure_supplier_settings,
)
from user.entitlements import FEATURE_SPONSORED, require_feature
from user.models import Campaign, Category, PragmadinarService
from user.pragmadinar import (
    InsufficientPragmadinar,
    debit_service,
    format_cost_label,
    get_service,
    wallet_payload,
)
from user.platform_config import pragmadinar_to_dzd

PROPOSAL_TEMPLATE_SERVICE = "promo.proposal_template"
QUOTE_TEMPLATE_SERVICE = "promo.quote_template"


def _supplier_or_denied(request):
    supplier = getattr(request.user, "supplier", None)
    if supplier is None:
        return None, Response(
            {"error": "Un compte fournisseur est requis.", "code": "supplier_required"},
            status=status.HTTP_403_FORBIDDEN,
        )
    return supplier, None


def _supplier_category_names(supplier) -> list[str]:
    root_ids = campaigns.supplier_category_roots([supplier.pk]).get(supplier.pk, set())
    return [
        name_fr or name_en
        for name_fr, name_en in Category.objects.filter(pk__in=root_ids)
        .order_by("sort_order", "name_fr")
        .values_list("name_fr", "name_en")
    ]


class CampaignListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied
        try:
            require_feature(supplier, FEATURE_SPONSORED)
        except PermissionDenied as exc:
            return Response(exc.detail, status=status.HTTP_403_FORBIDDEN)

        results = []
        for campaign in campaigns.campaigns_for(supplier):
            organic = campaigns.organic_views_for(
                supplier, campaign.starts_at, campaign.ends_at
            )
            results.append(campaigns.campaign_payload(campaign, organic_views=organic))
        services = {
            service.code: service
            for service in PragmadinarService.objects.filter(
                code__in=campaigns.FORMAT_SERVICE.values(), is_active=True
            )
        }
        formats = []
        for value, label in Campaign.Format.choices:
            code = campaigns.FORMAT_SERVICE[value]
            service = services.get(code)
            placement = campaigns.FORMAT_PLACEMENT.get(value, "")
            formats.append(
                {
                    "value": value,
                    "label": label,
                    "serviceCode": code,
                    "group": campaigns.FORMAT_GROUP.get(value, ""),
                    "placement": placement,
                    "placementLabel": campaigns.PLACEMENT_LABELS.get(placement, ""),
                    "costLabel": (
                        format_cost_label(
                            service.cost_pragmadinar,
                            service.unit,
                            service.unit_quantity,
                            service.unit_label,
                        )
                        if service
                        else ""
                    ),
                }
            )
        return Response(
            {
                "wallet": wallet_payload(supplier, category="promotion"),
                "formats": formats,
                "objectives": [
                    {"value": v, "label": l} for v, l in Campaign.Objective.choices
                ],
                "maxDurationDays": campaigns.MAX_DURATION_DAYS,
                "contextScopes": [
                    {"value": v, "label": l} for v, l in Campaign.ContextScope.choices
                ],
                "supplierCategories": _supplier_category_names(supplier),
                "results": results,
            }
        )

    def post(self, request):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied
        try:
            campaign = campaigns.create_campaign(
                supplier, request.data, actor=request.user
            )
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        except PermissionDenied as exc:
            return Response(exc.detail, status=status.HTTP_403_FORBIDDEN)
        return Response(
            campaigns.campaign_payload(campaign), status=status.HTTP_201_CREATED
        )


class CampaignDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get(self, request, pk):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return None, denied
        campaign = campaigns.campaigns_for(supplier).filter(pk=pk).first()
        if campaign is None:
            return None, Response(
                {"error": "Campagne introuvable."}, status=status.HTTP_404_NOT_FOUND
            )
        return campaign, None

    def get(self, request, pk):
        campaign, denied = self._get(request, pk)
        if denied:
            return denied
        organic = campaigns.organic_views_for(
            campaign.supplier, campaign.starts_at, campaign.ends_at
        )
        return Response(campaigns.campaign_payload(campaign, organic_views=organic))

    def patch(self, request, pk):
        campaign, denied = self._get(request, pk)
        if denied:
            return denied
        status_value = request.data.get("status")
        context_scope = request.data.get("contextScope")
        try:
            if context_scope:
                campaigns.set_campaign_context_scope(campaign, context_scope)
            if status_value:
                campaigns.set_campaign_status(campaign, status_value)
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        campaign.refresh_from_db()
        return Response(campaigns.campaign_payload(campaign))


class CampaignHitView(APIView):
    """Internal-facing: record a displayed sponsored hit. Used by tests and
    the search layer. Never bills a bot or a self-view."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied
        campaign = campaigns.campaigns_for(supplier).filter(pk=pk).first()
        if campaign is None:
            return Response(
                {"error": "Campagne introuvable."}, status=status.HTTP_404_NOT_FOUND
            )
        campaign, billed = campaigns.record_sponsored_hit(
            campaign,
            kind=request.data.get("kind") or "view",
            request=request,
            actor=request.user,
        )
        return Response(
            {"billed": billed, **campaigns.campaign_payload(campaign)}
        )


class SponsoredBannerView(APIView):
    """Public: the banner campaign to display for a placement, or null."""

    permission_classes = [AllowAny]

    def get(self, request):
        placement = (request.query_params.get("placement") or "").strip()
        if placement not in campaigns.PLACEMENT_BANNER_FORMAT:
            return Response(
                {"error": "Emplacement inconnu.", "code": "unknown_placement"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        params = request.query_params

        def context_roots():
            if placement == "buyer_conversations":
                return campaigns.conversation_context_roots(request.user)
            subcategory = (params.get("subcategory_id") or "").strip()
            return campaigns.search_context_roots(
                query=params.get("q") or "",
                category=params.get("category") or "",
                subcategory_id=int(subcategory) if subcategory.isdigit() else None,
            )

        campaign = campaigns.pick_banner_campaign(placement, context_roots=context_roots)
        if campaign is None:
            return Response(None)
        return Response(
            {
                "campaignId": campaign.id,
                "format": campaign.campaign_format,
                "placement": placement,
                "supplier": SupplierListSerializer(campaign.supplier).data,
            }
        )


class SponsoredBannerHitView(APIView):
    """Public: record a banner impression (view) or click (visit)."""

    permission_classes = [AllowAny]

    def post(self, request, pk):
        campaign = Campaign.objects.filter(
            pk=pk,
            campaign_format__in=campaigns.BANNER_FORMATS,
            status=Campaign.Status.ACTIVE,
        ).first()
        if campaign is None:
            return Response({"billed": False})
        billed = campaigns.record_banner_hit(
            campaign, kind=request.data.get("kind") or "view", request=request
        )
        return Response({"billed": billed})


class ExtraTemplatePurchaseView(APIView):
    """Buy one extra proposal or quote template. Price comes from the catalog."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied
        settings = ensure_supplier_settings(supplier)
        templates = _normalize_proposal_templates(settings.proposal_templates)
        proposal = get_service(PROPOSAL_TEMPLATE_SERVICE)
        quote = get_service(QUOTE_TEMPLATE_SERVICE)
        wallet = wallet_payload(supplier, category="promotion")
        return Response(
            {
                "wallet": wallet,
                "included": 1,
                "owned": len(templates),
                "proposal": {
                    "code": proposal.code,
                    "costPragmadinar": str(proposal.cost_pragmadinar),
                    "costDzd": str(pragmadinar_to_dzd(proposal.cost_pragmadinar)),
                    "costLabel": format_cost_label(
                        proposal.cost_pragmadinar,
                        proposal.unit,
                        proposal.unit_quantity,
                        proposal.unit_label,
                    ),
                    "balanceAfter": str(
                        float(wallet["available"]) - float(proposal.cost_pragmadinar)
                    ),
                },
                "quote": {
                    "code": quote.code,
                    "costPragmadinar": str(quote.cost_pragmadinar),
                    "costDzd": str(pragmadinar_to_dzd(quote.cost_pragmadinar)),
                    "costLabel": format_cost_label(
                        quote.cost_pragmadinar,
                        quote.unit,
                        quote.unit_quantity,
                        quote.unit_label,
                    ),
                    "balanceAfter": str(
                        float(wallet["available"]) - float(quote.cost_pragmadinar)
                    ),
                },
            }
        )

    def post(self, request):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied
        kind = (request.data.get("kind") or "proposal").strip()
        code = QUOTE_TEMPLATE_SERVICE if kind == "quote" else PROPOSAL_TEMPLATE_SERVICE
        settings = ensure_supplier_settings(supplier)
        templates = _normalize_proposal_templates(settings.proposal_templates)
        try:
            ledger = debit_service(
                supplier,
                code,
                ref_type="extra_template",
                ref_id=supplier.pk,
                reason="Modèle supplémentaire",
                actor=request.user,
                idempotency_key=(request.data.get("idempotencyKey") or "").strip(),
            )
        except InsufficientPragmadinar as exc:
            return Response(exc.detail, status=status.HTTP_402_PAYMENT_REQUIRED)
        except PermissionDenied as exc:
            return Response(exc.detail, status=status.HTTP_403_FORBIDDEN)

        extra = {
            "id": f"paid_{ledger.reference}",
            "name": "Nouveau modèle" if kind == "proposal" else "Nouveau devis",
            "isDefault": False,
            "status": "draft",
            "paid": True,
            "blocks": templates[0]["blocks"] if templates else [],
        }
        templates.append(extra)
        settings.proposal_templates = templates
        settings.save(update_fields=["proposal_templates", "updated_at"])
        return Response(
            {
                "template": extra,
                "owned": len(templates),
                "reference": ledger.reference,
                "wallet": wallet_payload(supplier, category="promotion"),
            },
            status=status.HTTP_201_CREATED,
        )
