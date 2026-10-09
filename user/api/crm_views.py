"""
JSON API for the supplier CRM (contacts, companies, opportunities).

Each request is tied to the logged-in supplier. Base CRM (companies, contacts,
pipeline, activities) is free for every supplier — no plan or Pragmadinar balance
check on these routes.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.utils import timezone
from user.reporting_period import resolve_report_period
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import crm
from user.entitlements import entitlements_payload, plan_code_for, require_feature
from user.models import (
    CrmActivity,
    CrmCompany,
    CrmContact,
    Opportunity,
    Order,
    PragmadinarLedger,
    PragmadinarWallet,
    QuoteDemand,
)
from user.pragmadinar import format_pragmadinar_amount, wallet_payload
from user.api.serializers import SupplierOrderSerializer


class SupplierScopedView(APIView):
    """Resolves and enforces supplier ownership for all CRM routes."""

    permission_classes = [IsAuthenticated]
    required_feature = None

    def get_supplier(self, request):
        supplier = getattr(request.user, "supplier", None)
        if supplier is None:
            return None, Response(
                {
                    "error": "Un compte fournisseur est requis.",
                    "code": "supplier_required",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        if self.required_feature:
            require_feature(supplier, self.required_feature)
        return supplier, None


def _decimal_or_none(raw):
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _parse_dt(raw):
    if not raw:
        return None
    from django.utils.dateparse import parse_datetime

    parsed = parse_datetime(str(raw))
    if parsed and timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed


# ── Entitlements ────────────────────────────────────────────────────────────


class SupplierEntitlementsView(SupplierScopedView):
    required_feature = None

    def get(self, request):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied
        return Response(entitlements_payload(supplier))


# ── Companies ───────────────────────────────────────────────────────────────

COMPANY_WRITABLE = [
    "name",
    "sector",
    "wilaya",
    "size",
    "status",
    "website",
    "notes",
]


class CrmCompanyListView(SupplierScopedView):
    def get(self, request):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied
        qs = crm.companies_for(supplier).prefetch_related("contacts", "opportunities")
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(sector__icontains=search)
                | Q(contacts__first_name__icontains=search)
                | Q(contacts__last_name__icontains=search)
                | Q(contacts__email__icontains=search)
            ).distinct()
        status_filter = (request.query_params.get("status") or "").strip()
        if status_filter:
            qs = qs.filter(status=status_filter)
        return Response(
            {
                "results": [crm.company_payload(c) for c in qs[:300]],
                "statuses": [
                    {"value": v, "label": l} for v, l in CrmCompany.Status.choices
                ],
            }
        )

    def post(self, request):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied
        name = (request.data.get("name") or "").strip()
        if not name:
            return Response(
                {"error": "Le nom de l'entreprise est obligatoire."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload = {
            key: request.data.get(key) or ""
            for key in COMPANY_WRITABLE
            if key != "name"
        }
        payload["status"] = payload.get("status") or CrmCompany.Status.PROSPECT
        if not payload.get("website"):
            payload["website"] = None
        company = CrmCompany.objects.create(
            supplier=supplier, name=name, owner_user=request.user, **payload
        )
        return Response(
            crm.company_payload(company), status=status.HTTP_201_CREATED
        )


class CrmCompanyDetailView(SupplierScopedView):
    def _get(self, request, pk):
        supplier, denied = self.get_supplier(request)
        if denied:
            return None, denied
        company = crm.companies_for(supplier, include_archived=True).filter(pk=pk).first()
        if company is None:
            return None, Response(
                {"error": "Entreprise introuvable."}, status=status.HTTP_404_NOT_FOUND
            )
        return company, None

    def get(self, request, pk):
        company, denied = self._get(request, pk)
        if denied:
            return denied
        payload = crm.company_payload(company)
        payload["contacts"] = [
            crm.contact_payload(c) for c in company.contacts.filter(is_archived=False)
        ]
        payload["opportunities"] = [
            crm.opportunity_payload(o) for o in company.opportunities.all()[:50]
        ]
        payload["activities"] = [
            crm.activity_payload(a) for a in company.activities.all()[:50]
        ]
        return Response(payload)

    def patch(self, request, pk):
        company, denied = self._get(request, pk)
        if denied:
            return denied
        changed = []
        for key in COMPANY_WRITABLE:
            if key in request.data:
                value = request.data.get(key)
                if key == "website" and not value:
                    value = None
                elif key != "website":
                    value = value or ""
                setattr(company, key, value)
                changed.append(key)
        if changed:
            company.save(update_fields=changed + ["updated_at"])
        return Response(crm.company_payload(company))

    def delete(self, request, pk):
        """Soft delete: history and linked opportunities are preserved."""
        company, denied = self._get(request, pk)
        if denied:
            return denied
        company.is_archived = True
        company.save(update_fields=["is_archived", "updated_at"])
        return Response({"archived": True})


# ── Contacts ────────────────────────────────────────────────────────────────

CONTACT_WRITABLE = [
    "first_name",
    "last_name",
    "job_title",
    "decision_role",
    "email",
    "phone",
    "whatsapp",
    "preferred_channel",
    "notes",
]


class CrmContactListView(SupplierScopedView):
    def get(self, request):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied
        qs = crm.contacts_for(supplier)
        company_id = request.query_params.get("companyId")
        if company_id:
            qs = qs.filter(company_id=company_id)
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(email__icontains=search)
                | Q(company__name__icontains=search)
            )
        return Response(
            {
                "results": [crm.contact_payload(c) for c in qs[:300]],
                "channels": [
                    {"value": v, "label": l} for v, l in CrmContact.Channel.choices
                ],
            }
        )

    def post(self, request):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied
        payload = {key: request.data.get(key) or "" for key in CONTACT_WRITABLE}
        company_id = request.data.get("companyId")
        if company_id:
            company = crm.companies_for(supplier).filter(pk=company_id).first()
        else:
            company = None
        if company is None:
            label = (
                f"{payload.get('first_name', '')} {payload.get('last_name', '')}".strip()
                or "Contact manuel"
            )
            company, _ = crm.ensure_company(supplier, name=label[:255])
        payload["preferred_channel"] = (
            payload.get("preferred_channel") or CrmContact.Channel.MESSAGING
        )
        contact = CrmContact.objects.create(
            supplier=supplier,
            company=company,
            contact_consent=True,  # manually entered by the supplier
            consent_updated_at=timezone.now(),
            **payload,
        )
        return Response(crm.contact_payload(contact), status=status.HTTP_201_CREATED)


class CrmContactDetailView(SupplierScopedView):
    def _get(self, request, pk):
        supplier, denied = self.get_supplier(request)
        if denied:
            return None, denied
        contact = (
            crm.contacts_for(supplier, include_archived=True).filter(pk=pk).first()
        )
        if contact is None:
            return None, Response(
                {"error": "Contact introuvable."}, status=status.HTTP_404_NOT_FOUND
            )
        return contact, None

    def get(self, request, pk):
        contact, denied = self._get(request, pk)
        if denied:
            return denied
        payload = crm.contact_payload(contact)
        payload["activities"] = [
            crm.activity_payload(a) for a in contact.activities.all()[:50]
        ]
        return Response(payload)

    def patch(self, request, pk):
        contact, denied = self._get(request, pk)
        if denied:
            return denied
        # A supplier may never flip a platform buyer's consent by itself.
        changed = []
        for key in CONTACT_WRITABLE:
            if key in request.data:
                if contact.user_id is not None and key in {
                    "email",
                    "phone",
                    "whatsapp",
                }:
                    continue
                setattr(contact, key, request.data.get(key) or "")
                changed.append(key)
        if changed:
            contact.save(update_fields=changed + ["updated_at"])
        return Response(crm.contact_payload(contact))

    def delete(self, request, pk):
        contact, denied = self._get(request, pk)
        if denied:
            return denied
        contact.is_archived = True
        contact.save(update_fields=["is_archived", "updated_at"])
        return Response({"archived": True})


# ── Opportunities ───────────────────────────────────────────────────────────


class OpportunityListView(SupplierScopedView):
    def get(self, request):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied
        qs = crm.opportunities_for(supplier)
        stage = (request.query_params.get("stage") or "").strip()
        if stage:
            qs = qs.filter(stage=stage)
        return Response(
            {
                "results": [crm.opportunity_payload(o) for o in qs[:300]],
                "stages": [
                    {"value": v, "label": l} for v, l in Opportunity.Stage.choices
                ],
                "sources": [
                    {"value": v, "label": l} for v, l in Opportunity.Source.choices
                ],
            }
        )

    def post(self, request):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied
        company = (
            crm.companies_for(supplier)
            .filter(pk=request.data.get("companyId"))
            .first()
        )
        contact = (
            crm.contacts_for(supplier).filter(pk=request.data.get("contactId")).first()
        )
        opportunity = Opportunity.objects.create(
            supplier=supplier,
            company=company,
            contact=contact,
            title=(request.data.get("title") or "Nouvelle opportunité")[:255],
            source=request.data.get("source") or Opportunity.Source.DIRECT_CONTACT,
            stage=request.data.get("stage") or Opportunity.Stage.PROSPECT,
            estimated_value=_decimal_or_none(request.data.get("estimatedValue")),
            next_action=(request.data.get("nextAction") or "")[:255],
            next_action_at=_parse_dt(request.data.get("nextActionAt")),
        )
        return Response(
            crm.opportunity_payload(opportunity), status=status.HTTP_201_CREATED
        )


class OpportunityDetailView(SupplierScopedView):
    def _get(self, request, pk):
        supplier, denied = self.get_supplier(request)
        if denied:
            return None, denied
        opportunity = crm.opportunities_for(supplier).filter(pk=pk).first()
        if opportunity is None:
            return None, Response(
                {"error": "Opportunité introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return opportunity, None

    def get(self, request, pk):
        opportunity, denied = self._get(request, pk)
        if denied:
            return denied
        payload = crm.opportunity_payload(opportunity)
        payload["activities"] = [
            crm.activity_payload(a) for a in opportunity.activities.all()[:100]
        ]
        payload["contact"] = (
            crm.contact_payload(opportunity.contact) if opportunity.contact_id else None
        )
        order = opportunity.order if opportunity.order_id else None
        payload["order"] = (
            SupplierOrderSerializer(order, context={"request": request}).data
            if order is not None and order.supplier_id == opportunity.supplier_id
            else None
        )
        return Response(payload)

    def patch(self, request, pk):
        opportunity, denied = self._get(request, pk)
        if denied:
            return denied

        stage = request.data.get("stage")
        if stage and stage != opportunity.stage:
            valid = {v for v, _ in Opportunity.Stage.choices}
            if stage not in valid:
                return Response(
                    {"error": "Étape inconnue."}, status=status.HTTP_400_BAD_REQUEST
                )
            lost_reason = (request.data.get("lostReason") or "").strip()
            if stage == Opportunity.Stage.LOST and not lost_reason:
                return Response(
                    {
                        "error": "Indiquez la raison de la perte.",
                        "code": "lost_reason_required",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            crm.set_stage(
                opportunity,
                stage,
                actor=request.user,
                lost_reason=lost_reason,
                note=(request.data.get("note") or "").strip(),
            )

        changed = []
        if "title" in request.data:
            opportunity.title = (request.data.get("title") or "")[:255]
            changed.append("title")
        if "estimatedValue" in request.data:
            opportunity.estimated_value = _decimal_or_none(
                request.data.get("estimatedValue")
            )
            changed.append("estimated_value")
        if "probability" in request.data:
            try:
                opportunity.probability = max(
                    0, min(100, int(request.data.get("probability")))
                )
                changed.append("probability")
            except (TypeError, ValueError):
                pass
        if "nextAction" in request.data:
            opportunity.next_action = (request.data.get("nextAction") or "")[:255]
            changed.append("next_action")
        if "nextActionAt" in request.data:
            opportunity.next_action_at = _parse_dt(request.data.get("nextActionAt"))
            changed.append("next_action_at")
        if changed:
            opportunity.save(update_fields=changed + ["updated_at"])

        opportunity.refresh_from_db()
        return Response(crm.opportunity_payload(opportunity))


class OpportunityPipelineView(SupplierScopedView):
    def get(self, request):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied
        period, start, end = _crm_period(request)
        rows = crm.pipeline_rows(supplier, start, end)
        open_opps, won_opps = crm.split_open_and_won(rows)
        return Response(
            {
                "period": period.as_dict(),
                "columns": crm.pipeline_payload(supplier, start, end, rows=rows),
                "profitability": crm.profitability_payload(
                    supplier, period, won_opps, open_opps
                ),
            }
        )


# ── Activities ──────────────────────────────────────────────────────────────


class CrmActivityListView(SupplierScopedView):
    def get(self, request):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied
        qs = crm.activities_for(supplier)
        scope = (request.query_params.get("scope") or "").strip()
        if scope == "todo":
            qs = qs.filter(done_at__isnull=True, due_at__isnull=False)
        elif scope == "overdue":
            qs = qs.filter(done_at__isnull=True, due_at__lt=timezone.now())
        opportunity_id = request.query_params.get("opportunityId")
        if opportunity_id:
            qs = qs.filter(opportunity_id=opportunity_id)
        return Response(
            {
                "results": [crm.activity_payload(a) for a in qs[:300]],
                "kinds": [
                    {"value": v, "label": l} for v, l in CrmActivity.Kind.choices
                ],
            }
        )

    def post(self, request):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied
        title = (request.data.get("title") or "").strip()
        if not title:
            return Response(
                {"error": "Le titre est obligatoire."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        opportunity = (
            crm.opportunities_for(supplier)
            .filter(pk=request.data.get("opportunityId"))
            .first()
        )
        company = (
            crm.companies_for(supplier)
            .filter(pk=request.data.get("companyId"))
            .first()
        )
        contact = (
            crm.contacts_for(supplier).filter(pk=request.data.get("contactId")).first()
        )
        activity = crm.record_activity(
            supplier,
            kind=request.data.get("kind") or CrmActivity.Kind.NOTE,
            title=title,
            body=(request.data.get("body") or "").strip(),
            opportunity=opportunity,
            company=company,
            contact=contact,
            due_at=_parse_dt(request.data.get("dueAt")),
            created_by=request.user,
        )
        return Response(crm.activity_payload(activity), status=status.HTTP_201_CREATED)


class CrmActivityDetailView(SupplierScopedView):
    def patch(self, request, pk):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied
        activity = crm.activities_for(supplier).filter(pk=pk).first()
        if activity is None:
            return Response(
                {"error": "Activité introuvable."}, status=status.HTTP_404_NOT_FOUND
            )
        locked_kinds = {
            CrmActivity.Kind.SYSTEM,
            CrmActivity.Kind.STAGE_CHANGE,
        }
        if "done" in request.data:
            activity.done_at = timezone.now() if request.data.get("done") else None
            activity.save(update_fields=["done_at"])
            if activity.done_at is not None:
                return Response(crm.activity_payload(activity))

        if activity.done_at is not None:
            return Response(
                {
                    "error": "Une activité terminée ne peut plus être modifiée.",
                    "code": "activity_closed",
                },
                status=status.HTTP_409_CONFLICT,
            )
        if activity.kind in locked_kinds:
            return Response(
                {
                    "error": "Cette activité système n'est pas modifiable.",
                    "code": "activity_locked",
                },
                status=status.HTTP_409_CONFLICT,
            )

        update_fields = []
        if "dueAt" in request.data:
            activity.due_at = _parse_dt(request.data.get("dueAt"))
            update_fields.append("due_at")
        if "kind" in request.data:
            kind = (request.data.get("kind") or "").strip()
            valid = {value for value, _label in CrmActivity.Kind.choices}
            if kind not in valid:
                return Response(
                    {"error": "Type d'activité invalide."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if kind in locked_kinds:
                return Response(
                    {
                        "error": "Ce type est réservé au système.",
                        "code": "activity_kind_locked",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            activity.kind = kind
            update_fields.append("kind")
        if "title" in request.data:
            title = (request.data.get("title") or "").strip()
            if not title:
                return Response(
                    {"error": "Le titre est obligatoire."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            activity.title = title[:255]
            update_fields.append("title")
        if "body" in request.data:
            activity.body = (request.data.get("body") or "").strip()
            update_fields.append("body")
        if update_fields:
            activity.save(update_fields=update_fields)
        return Response(crm.activity_payload(activity))

    def delete(self, request, pk):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied
        activity = crm.activities_for(supplier).filter(pk=pk).first()
        if activity is None:
            return Response(
                {"error": "Activité introuvable."}, status=status.HTTP_404_NOT_FOUND
            )
        locked_kinds = {
            CrmActivity.Kind.SYSTEM,
            CrmActivity.Kind.STAGE_CHANGE,
        }
        if activity.kind in locked_kinds:
            return Response(
                {
                    "error": "Cette activité système ne peut pas être supprimée.",
                    "code": "activity_locked",
                },
                status=status.HTTP_409_CONFLICT,
            )
        activity.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Dashboard ───────────────────────────────────────────────────────────────


def _crm_period(request):
    period = resolve_report_period(request.query_params)
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(period.start, time.min), tz)
    end = timezone.make_aware(datetime.combine(period.end + timedelta(days=1), time.min), tz)
    return period, start, end


class SupplierCrmDashboardView(SupplierScopedView):
    """CRM dashboard. Period comes from ``period`` preset or year/month params."""

    required_feature = None

    def get(self, request):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied

        now = timezone.now()
        period, start, end = _crm_period(request)

        rows = crm.pipeline_rows(supplier, start, end)
        open_opps, _won_started = crm.split_open_and_won(rows)
        by_stage = {
            column["stage"]: {"count": column["count"], "value": column["totalValue"]}
            for column in crm.pipeline_payload(supplier, start, end, rows=rows)
            if column["count"]
        }

        won_month = list(
            Opportunity.objects.filter(
                supplier=supplier,
                stage=Opportunity.Stage.WON,
                closed_at__gte=start,
                closed_at__lt=end,
            ).select_related("order", "company")
        )
        lost_month = Opportunity.objects.filter(
            supplier=supplier,
            stage=Opportunity.Stage.LOST,
            closed_at__gte=start,
            closed_at__lt=end,
        ).count()
        won_count = len(won_month)
        won_value = sum((crm.opportunity_value(opp) or 0) for opp in won_month)

        demands_month = Opportunity.objects.filter(
            supplier=supplier,
            stage=Opportunity.Stage.REQUEST_RECEIVED,
            created_at__gte=start,
            created_at__lt=end,
        ).count()

        orders_month = Order.objects.filter(
            supplier=supplier, created_at__gte=start, created_at__lt=end
        )

        overdue = crm.activities_for(supplier).filter(
            done_at__isnull=True, due_at__lt=now
        ).count()
        upcoming = crm.activities_for(supplier).filter(
            done_at__isnull=True, due_at__gte=now, due_at__lte=now + timedelta(days=7)
        ).count()

        return Response(
            {
                "period": period.as_dict(),
                "plan": plan_code_for(supplier),
                "pipeline": by_stage,
                "month": {
                    "demandsReceived": demands_month,
                    "opportunitiesWon": won_count,
                    "opportunitiesLost": lost_month,
                    "wonValue": str(won_value),
                    "orders": orders_month.count(),
                    "ordersDelivered": orders_month.filter(
                        status=Order.Status.DELIVERED
                    ).count(),
                },
                "profitability": crm.profitability_payload(
                    supplier, period, won_month, open_opps
                ),
                "companies": crm.companies_for(supplier).count(),
                "contacts": crm.contacts_for(supplier).count(),
                "activities": {"overdue": overdue, "upcoming": upcoming},
                "entitlements": entitlements_payload(supplier),
                "wallet": wallet_payload(supplier),
            }
        )


# ── Pragmadinar transactions ───────────────────────────────────────────────────────


class SupplierPragmadinarTransactionsView(SupplierScopedView):
    required_feature = None

    def get(self, request):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied
        entries = list(
            PragmadinarLedger.objects.filter(wallet__supplier=supplier)
            .select_related("service", "campaign", "wallet")
            .order_by("-created_at")[:200]
        )
        running_balances = {
            wallet.id: wallet.balance
            for wallet in PragmadinarWallet.objects.filter(supplier=supplier)
        }
        serialized = []
        for entry in entries:
            balance_after = running_balances.get(entry.wallet_id, Decimal("0"))
            running_balances[entry.wallet_id] = balance_after - entry.amount
            serialized.append(
                {
                    "id": entry.id,
                    "reference": entry.reference,
                    "entryType": entry.entry_type,
                    "entryTypeLabel": entry.get_entry_type_display(),
                    "status": entry.status,
                    "statusLabel": entry.get_status_display(),
                    "amount": format_pragmadinar_amount(entry.amount),
                    "amountDzd": str(entry.amount_dzd),
                    "rateDzd": str(entry.rate_dzd),
                    "balanceAfter": format_pragmadinar_amount(balance_after),
                    "walletCategory": entry.wallet.category,
                    "reason": entry.reason,
                    "serviceCode": entry.service.code if entry.service_id else "",
                    "serviceLabel": entry.service.label if entry.service_id else "",
                    "campaignId": entry.campaign_id,
                    "campaignName": entry.campaign.name if entry.campaign_id else "",
                    "refType": entry.ref_type,
                    "refId": entry.ref_id,
                    "createdAt": entry.created_at,
                }
            )
        return Response(
            {
                "wallet": wallet_payload(supplier),
                "results": serialized,
            }
        )
