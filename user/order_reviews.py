"""Buyer rating of a supplier after a delivered order."""

from django.db.models import Avg, Count, FloatField, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from user.models import AppNotification, Order, OrderSupplierReview, SupplierSettings

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
    previous = (
        OrderSupplierReview.objects.filter(order=order).only("rating", "comment").first()
    )
    review, created = OrderSupplierReview.objects.update_or_create(
        order=order,
        defaults={
            "buyer": buyer,
            "supplier": order.supplier,
            "rating": stars,
            "comment": text,
        },
    )
    changed = (
        created
        or previous is None
        or previous.rating != stars
        or (previous.comment or "") != text
    )
    if changed:
        notify_supplier_of_review(review, created=created)
    return review


def buyer_public_name(user):
    """Name shown to the supplier. Never the e-mail address."""
    name = (user.get_full_name() or "").strip()
    if name:
        return name
    company = (getattr(user, "company_name", "") or "").strip()
    if company:
        return company
    if getattr(user, "first_name", ""):
        return user.first_name
    return "Un acheteur"


def supplier_rating_is_public(supplier) -> bool:
    """Boutique header shows buyer ratings unless the supplier hides them."""
    try:
        settings_obj = supplier.settings
    except SupplierSettings.DoesNotExist:
        settings_obj = None
    raw = (getattr(settings_obj, "page_design", None) or {}) if settings_obj else {}
    return str(raw.get("showRating", "true")).lower() not in ("false", "0", "no")


def public_review_rows(supplier):
    reviews = (
        supplier.delivery_reviews.select_related("buyer", "order__product")
        .order_by("-created_at", "-id")
    )
    rows = []
    for review in reviews:
        product = getattr(review.order, "product", None)
        rows.append(
            {
                "id": review.id,
                "rating": review.rating,
                "comment": review.comment or "",
                "createdAt": review.created_at.isoformat(),
                "updatedAt": review.updated_at.isoformat(),
                "buyerName": buyer_public_name(review.buyer),
                "productName": product.name if product is not None else "",
            }
        )
    return rows


def notify_supplier_of_review(review, *, created):
    buyer_name = buyer_public_name(review.buyer)
    when = timezone.localtime(review.updated_at or review.created_at)
    date_label = when.strftime("%d/%m/%Y %H:%M")
    comment = (review.comment or "").strip()
    detail = f"« {comment} »" if comment else "Sans commentaire"
    title = "Nouvelle note" if created else "Note mise à jour"
    body = f"{buyer_name} · {date_label} · {review.rating}/5\n{detail}"
    link = f"/suppliers/{review.supplier_id}?reviews=1&from=supplier"
    for user in review.supplier.users.all():
        AppNotification.objects.create(
            user=user,
            kind=AppNotification.Kind.SUPPLIER_RATED,
            title=title,
            body=body,
            link=link,
        )
