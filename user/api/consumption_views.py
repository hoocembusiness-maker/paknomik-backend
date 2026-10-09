from rest_framework.response import Response

from user.api.crm_views import SupplierScopedView
from user.pragmadinar_consumption import build_consumption_dashboard
from user.reporting_period import resolve_report_period


class SupplierPragmadinarConsumptionView(SupplierScopedView):
    """Read-only consumption dashboard; viewing it never debits anything."""

    required_feature = None

    def get(self, request):
        supplier, denied = self.get_supplier(request)
        if denied:
            return denied
        period = resolve_report_period(request.query_params)
        return Response(build_consumption_dashboard(supplier, period))
