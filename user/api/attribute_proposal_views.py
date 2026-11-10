"""Staff queue for technical attributes proposed by suppliers."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from user.api.admin_views import _deny, _trace
from user.attribute_proposals import (
    generalize_proposal,
    proposal_queryset,
    reject_proposal,
    serialize_proposal,
)
from user.models import TechnicalAttributeProposal
from user.rbac import PRODUCTS_MANAGE


class AdminTechnicalAttributeProposalsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _deny(request, PRODUCTS_MANAGE)
        if denied:
            return denied
        wanted = request.query_params.get("status") or TechnicalAttributeProposal.PENDING
        if wanted not in (TechnicalAttributeProposal.PENDING, TechnicalAttributeProposal.REJECTED):
            wanted = TechnicalAttributeProposal.PENDING
        rows = [serialize_proposal(attribute) for attribute in proposal_queryset(wanted)]
        return Response({"results": rows, "count": len(rows)})


class AdminTechnicalAttributeProposalActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        denied = _deny(request, PRODUCTS_MANAGE)
        if denied:
            return denied
        try:
            proposal = TechnicalAttributeProposal.objects.select_related(
                "supplier", "category", "category__parent", "attribute", "attribute__unit"
            ).get(attribute_id=pk)
        except TechnicalAttributeProposal.DoesNotExist:
            return Response({"error": "Proposition introuvable."}, status=status.HTTP_404_NOT_FOUND)

        action = request.data.get("action")
        try:
            if action == "generalize":
                unit_symbol = request.data.get("unitSymbol") if "unitSymbol" in request.data else None
                attribute = proposal.attribute
                attribute = generalize_proposal(
                    proposal,
                    name=request.data.get("name") or attribute.name_fr or attribute.name_en,
                    attribute_type=request.data.get("attributeType") or attribute.attribute_type,
                    unit_symbol=unit_symbol,
                )
                _trace(
                    request,
                    "admin.technical_attribute.generalize",
                    entity_type="technical_attribute",
                    entity_id=attribute.id,
                    extra={"proposalId": pk, "name": attribute.name_fr},
                )
            elif action == "reject":
                attribute = reject_proposal(proposal)
                _trace(
                    request,
                    "admin.technical_attribute.reject",
                    entity_type="technical_attribute",
                    entity_id=attribute.id,
                    extra={"name": attribute.name_fr},
                )
            else:
                return Response(
                    {"error": "Action attendue : generalize ou reject."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        proposal = (
            TechnicalAttributeProposal.objects.select_related(
                "supplier", "category", "category__parent", "attribute", "attribute__unit"
            )
            .prefetch_related("attribute__product_values")
            .filter(attribute_id=attribute.id)
            .first()
        )
        if proposal is None:
            return Response(
                {
                    "id": attribute.id,
                    "name": attribute.name_fr or attribute.name_en,
                    "attributeType": attribute.attribute_type,
                    "unitSymbol": attribute.unit.symbol if attribute.unit_id else "",
                    "status": "catalog",
                    "categoryId": None,
                    "categoryLabel": "",
                    "supplierId": None,
                    "supplierName": "",
                    "productCount": attribute.product_values.values("product_id").distinct().count(),
                    "sampleValues": [],
                }
            )
        return Response(serialize_proposal(proposal))
