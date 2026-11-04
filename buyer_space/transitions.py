from django.db import transaction

from buyer_space.models import ServiceRequest, ServiceRequestEvent
from user.models import AppNotification

ALLOWED_TRANSITIONS = {
    ServiceRequest.Status.SUBMITTED: {ServiceRequest.Status.NEEDS_INFO},
    ServiceRequest.Status.NEEDS_INFO: {ServiceRequest.Status.IN_PROGRESS},
    ServiceRequest.Status.IN_PROGRESS: {
        ServiceRequest.Status.NEEDS_INFO,
        ServiceRequest.Status.RESULT_READY,
    },
    ServiceRequest.Status.RESULT_READY: {ServiceRequest.Status.CLOSED},
    ServiceRequest.Status.CLOSED: set(),
}

STATUS_LABELS = dict(ServiceRequest.Status.choices)


class TransitionError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@transaction.atomic
def transition_status(service_request: ServiceRequest, to_status: str, actor) -> ServiceRequest:
    current = service_request.status
    if to_status not in ALLOWED_TRANSITIONS.get(current, set()):
        raise TransitionError(
            f"Transition interdite : {STATUS_LABELS.get(current, current)} → "
            f"{STATUS_LABELS.get(to_status, to_status)}."
        )
    service_request.status = to_status
    if actor is not None and getattr(actor, "is_staff", False):
        service_request.assigned_to = actor
    service_request.save(update_fields=["status", "assigned_to", "updated_at"])
    ServiceRequestEvent.objects.create(
        request=service_request,
        actor=actor,
        from_status=current,
        to_status=to_status,
    )
    AppNotification.objects.create(
        user=service_request.requested_by,
        kind=AppNotification.Kind.SERVICE_REQUEST,
        title=f"{service_request.reference} — {STATUS_LABELS.get(to_status, to_status)}",
        body=f"Votre demande est passée à « {STATUS_LABELS.get(to_status, to_status)} ».",
        link=f"/buyer/demandes/{service_request.pk}",
    )
    return service_request
