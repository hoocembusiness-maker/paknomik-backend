"""Buyer rating of a supplier after a delivered order."""

from django.db.models import Avg, Count, FloatField, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce

from user.models import Order, OrderSupplierReview

RATEABLE_STATUSES = (Order.Status.DELIVERED, Order.Status.COMPLETED)
COMMENT_MAX = 500


class OrderReviewError(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def annotate_delivery_reviews(qs, *, supplier_field="pk"):
    """Attach _delivery_review_avg and _delivery_review_count without multiplying rows."""
    reviews = OrderSupplierReview.objects.filter(supplier_id=OuterRef(supplier_field))
    average = reviews.values("supplier_id").annotate(value=Avg("rating")).values("value")[:1]
    total = reviews.values("supplier_id").annotate(value=Count("id")).values("value")[:1]
    return qs.annotate(
        _delivery_review_avg=Subquery(average, output_field=FloatField()),
        _delivery_review_count=Coalesce(
            Subquery(total, output_field=IntegerField()),
            0,
        ),
    )


def delivery_review_stats(obj):
    """Return (average 0–5, count). Unrated suppliers average 0."""
    cached = getattr(obj, "_delivery_review_stats", None)
    if cached is not None:
        return cached
    count = getattr(obj, "_delivery_review_count", None)
    average = getattr(obj, "_delivery_review_avg", None)
    if count is None:
        supplier = obj if hasattr(obj, "delivery_reviews") else getattr(obj, "supplier", None)
        if supplier is None:
            return 0, 0
        row = supplier.delivery_reviews.aggregate(average=Avg("rating"), total=Count("id"))
        average = row["average"]
        count = row["total"] or 0
    count = int(count or 0)
    if count <= 0 or average is None:
        result = (0, 0)
    else:
        result = (round(float(average), 1), count)
    try:
        obj._delivery_review_stats = result
    except (AttributeError, TypeError):
        pass
    return result


def save_order_review(order, buyer, rating, comment=""):
    if order.status not in RATEABLE_STATUSES:
        raise OrderReviewError(
            "Vous pourrez noter ce fournisseur lorsque la commande sera marquée livrée.",
            "order_not_delivered",
        )
    try:
        stars = int(rating)
    except (TypeError, ValueError):
        stars = 0
    if stars < 1 or stars > 5:
        raise OrderReviewError("La note doit être comprise entre 1 et 5.", "invalid_rating")
    text = (comment or "").strip()[:COMMENT_MAX]
    review, _created = OrderSupplierReview.objects.update_or_create(
        order=order,
        defaults={
            "buyer": buyer,
            "supplier": order.supplier,
            "rating": stars,
            "comment": text,
        },
    )
    return review
