"""Espace de pilotage — seed, completeness, overview helpers."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q, QuerySet

from user import rbac

PILOTAGE_ACCESS = rbac.PILOTAGE_ACCESS
PILOTAGE_MANAGE = rbac.PILOTAGE_MANAGE
ADMIN_PILOTAGE = rbac.ADMIN_PILOTAGE

WEEK_SPECS = [
    {
        "code": "S1",
        "title": "Cadrage",
        "objective": "Tester le parcours acheteur et cadrer les priorités.",
        "sort_order": 10,
        "moi": [
            "Tester le site comme un acheteur.",
            "Répertorier les bugs : critiques, importants et optionnels.",
            "Choisir le parcours utilisateur essentiel.",
        ],
        "assistant": [
            "Collecter les données fournisseurs existantes.",
            "Vérifier les informations disponibles.",
            "Marquer « Non vérifié » toute information qui ne peut pas être confirmée.",
        ],
    },
    {
        "code": "S2",
        "title": "Fondamentaux",
        "objective": "Corriger le critique et solidifier le catalogue.",
        "sort_order": 20,
        "moi": [
            "Corriger les bugs critiques.",
            "Compléter le parcours essentiel.",
            "Tester après chaque correction.",
        ],
        "assistant": [
            "Compléter 15 à 20 fiches fournisseurs.",
            "Organiser produits, catégories et photos.",
            "Préparer la liste des entreprises à contacter.",
        ],
    },
    {
        "code": "S3",
        "title": "Test réel",
        "objective": "Présenter le site à de vraies entreprises acheteuses.",
        "sort_order": 30,
        "moi": [
            "Présenter le site à de vraies entreprises acheteuses.",
            "Comprendre comment elles recherchent actuellement leurs fournisseurs.",
            "Documenter les objections et difficultés rencontrées.",
        ],
        "assistant": [
            "Préparer la liste des acheteurs et leurs coordonnées professionnelles.",
            "Mettre à jour les fiches fournisseurs.",
            "Consigner les résultats des contacts et les relances nécessaires.",
        ],
    },
    {
        "code": "S4",
        "title": "Stabilisation et décision",
        "objective": "Stabiliser, mesurer la demande et décider du mois suivant.",
        "sort_order": 40,
        "moi": [
            "Corriger les problèmes détectés pendant les tests.",
            "Retester le parcours complet.",
            "Évaluer les signaux de demande réelle.",
            "Définir les priorités du mois suivant.",
        ],
        "assistant": [
            "Compléter les données manquantes.",
            "Relancer les entreprises intéressées.",
            "Produire un rapport synthétique.",
        ],
    },
]

METRIC_SPECS = [
    {
        "key": "essential_tasks_tested",
        "label": "Tâches essentielles testées",
        "sort_order": 10,
        "targets": {
            "S1": ("5", 5),
            "S2": ("8", 8),
            "S3": ("10", 10),
            "S4": ("10", 10),
        },
    },
    {
        "key": "complete_supplier_profiles",
        "label": "Fiches fournisseurs complètes, cumulées",
        "sort_order": 20,
        "targets": {
            "S1": ("5", 5),
            "S2": ("15", 15),
            "S3": ("20", 20),
            "S4": ("20+", 20),
        },
    },
    {
        "key": "buyer_companies_contacted",
        "label": "Entreprises acheteuses contactées, cumulées",
        "sort_order": 30,
        "targets": {
            "S1": ("0–2", 2),
            "S2": ("3–5", 5),
            "S3": ("7–10", 10),
            "S4": ("10+", 10),
        },
    },
    {
        "key": "serious_conversations",
        "label": "Conversations sérieuses, cumulées",
        "sort_order": 40,
        "targets": {
            "S1": ("0", 0),
            "S2": ("1", 1),
            "S3": ("2", 2),
            "S4": ("3+", 3),
        },
    },
    {
        "key": "weekly_reviews_done",
        "label": "Revues hebdomadaires complétées",
        "sort_order": 50,
        "targets": {
            "S1": ("1", 1),
            "S2": ("1", 1),
            "S3": ("1", 1),
            "S4": ("1", 1),
        },
    },
]

NON_NEGOTIABLE_RULES = [
    (
        "Ne pas démarrer une nouvelle fonctionnalité avant d'avoir terminé les "
        "priorités en cours, sauf bug critique."
    ),
    (
        "Ne pas recruter sous le coup de la fatigue : recruter seulement lorsqu'une "
        "tâche précise peut être définie."
    ),
    "Ne pas mesurer la réussite d'une journée au nombre d'heures seul.",
    (
        "Un contact sans réponse n'est pas une conversation ; l'enthousiasme n'est "
        "pas une preuve de volonté de payer."
    ),
]


def ensure_pilotage_permissions() -> None:
    """Idempotent: register pilotage permissions and ADMIN_PILOTAGE role."""
    from user.models import PlatformRole

    if PILOTAGE_ACCESS not in rbac.ALL_PERMISSIONS:
        rbac.ALL_PERMISSIONS.append(PILOTAGE_ACCESS)
    if PILOTAGE_MANAGE not in rbac.ALL_PERMISSIONS:
        rbac.ALL_PERMISSIONS.append(PILOTAGE_MANAGE)

    ultra = PlatformRole.objects.filter(code=rbac.ULTRA_ADMIN).first()
    if ultra is not None:
        perms = list(ultra.permissions or [])
        changed = False
        for code in (PILOTAGE_ACCESS, PILOTAGE_MANAGE):
            if code not in perms:
                perms.append(code)
                changed = True
        if changed:
            ultra.permissions = perms
            ultra.save(update_fields=["permissions"])

    PlatformRole.objects.update_or_create(
        code=ADMIN_PILOTAGE,
        defaults={
            "label": "Administrateur pilotage",
            "permissions": [PILOTAGE_ACCESS],
            "is_system": True,
        },
    )


def can_access_pilotage(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if not user.is_active:
        return False
    if not getattr(user, "is_staff", False) and not rbac.is_ultra_admin(user):
        return False
    if rbac.is_ultra_admin(user):
        return True
    return rbac.has_permission(user, PILOTAGE_ACCESS)


def can_manage_pilotage(user) -> bool:
    return rbac.is_ultra_admin(user) or rbac.has_permission(user, PILOTAGE_MANAGE)


def is_supplier_profile_complete(supplier) -> bool:
    """Reliable complete supplier sheet criteria for the 20-supplier goal."""
    if getattr(supplier, "is_ghost", False):
        return False
    if not getattr(supplier, "is_active", True):
        return False
    if not (supplier.company_name or "").strip():
        return False
    if not (supplier.email or "").strip():
        return False
    if not (supplier.wilaya or "").strip():
        return False
    presentation = (supplier.presentation or "").strip()
    if len(presentation) < 40:
        return False
    if not supplier.logo:
        return False
    if not supplier.products.exists():
        return False
    return True


def count_complete_suppliers() -> int:
    from user.models import Supplier

    qs = Supplier.objects.filter(is_active=True, is_ghost=False).prefetch_related(
        "products"
    )
    return sum(1 for s in qs if is_supplier_profile_complete(s))


@transaction.atomic
def seed_pilotage_catalog(*, force_checklist: bool = False) -> dict:
    """Create weeks, checklist, and metric targets without duplicating rows."""
    from user.pilotage_models import (
        PilotageGoalState,
        PilotageWeek,
        PilotageWeekChecklistItem,
        PilotageWeeklyMetric,
    )

    ensure_pilotage_permissions()
    PilotageGoalState.objects.get_or_create(pk=1)

    created_weeks = 0
    created_items = 0
    created_metrics = 0
    weeks = {}

    for spec in WEEK_SPECS:
        week, was_created = PilotageWeek.objects.get_or_create(
            code=spec["code"],
            defaults={
                "title": spec["title"],
                "objective": spec["objective"],
                "sort_order": spec["sort_order"],
            },
        )
        if was_created:
            created_weeks += 1
        else:
            # Keep title/objective in sync with catalog unless customized heavily —
            # only fill empty objective.
            updates = []
            if not week.title:
                week.title = spec["title"]
                updates.append("title")
            if not week.objective:
                week.objective = spec["objective"]
                updates.append("objective")
            if week.sort_order != spec["sort_order"]:
                week.sort_order = spec["sort_order"]
                updates.append("sort_order")
            if updates:
                week.save(update_fields=[*updates, "updated_at"])
        weeks[week.code] = week

        existing_titles = set(
            week.checklist_items.values_list("lane", "title")
        )
        for lane, titles in (("moi", spec["moi"]), ("assistant", spec["assistant"])):
            for idx, title in enumerate(titles):
                if (lane, title) in existing_titles and not force_checklist:
                    continue
                if (lane, title) in existing_titles:
                    continue
                PilotageWeekChecklistItem.objects.create(
                    week=week,
                    lane=lane,
                    title=title,
                    sort_order=(idx + 1) * 10,
                )
                created_items += 1

    for mspec in METRIC_SPECS:
        for week_code, (label, value) in mspec["targets"].items():
            week = weeks[week_code]
            _, was_created = PilotageWeeklyMetric.objects.get_or_create(
                metric_key=mspec["key"],
                week=week,
                defaults={
                    "metric_label": mspec["label"],
                    "target_label": label,
                    "target_value": value,
                    "actual_value": 0,
                    "sort_order": mspec["sort_order"],
                },
            )
            if was_created:
                created_metrics += 1
            else:
                PilotageWeeklyMetric.objects.filter(
                    metric_key=mspec["key"], week=week
                ).update(
                    metric_label=mspec["label"],
                    sort_order=mspec["sort_order"],
                )

    return {
        "weeks": created_weeks,
        "checklist_items": created_items,
        "metrics": created_metrics,
    }


def build_overview() -> dict:
    from user.pilotage_models import PilotageGoalState, PilotageMarketContact

    goals, _ = PilotageGoalState.objects.get_or_create(pk=1)
    product_reached = bool(
        goals.product_journey_tested and goals.product_no_critical_bugs
    )
    suppliers_complete = count_complete_suppliers()
    contacts = PilotageMarketContact.objects.filter(is_deleted=False)
    attempted = contacts.filter(
        status__in=[
            PilotageMarketContact.Status.ATTEMPTED,
            PilotageMarketContact.Status.ESTABLISHED,
            PilotageMarketContact.Status.SERIOUS,
        ]
    ).count()
    established = contacts.filter(
        status__in=[
            PilotageMarketContact.Status.ESTABLISHED,
            PilotageMarketContact.Status.SERIOUS,
        ]
    ).count()
    serious = contacts.filter(status=PilotageMarketContact.Status.SERIOUS).count()

    return {
        "product": {
            "objective": "1 parcours utilisateur fonctionnel, sans bug critique connu",
            "journeyTested": goals.product_journey_tested,
            "noCriticalBugs": goals.product_no_critical_bugs,
            "reached": product_reached,
            "status": "Atteint" if product_reached else "En cours",
            "progressPct": 100 if product_reached else (
                50 if goals.product_journey_tested or goals.product_no_critical_bugs else 0
            ),
        },
        "suppliers": {
            "objective": "20 fiches fournisseurs complètes et fiables",
            "completeCount": suppliers_complete,
            "target": 20,
            "progressPct": min(100, int(round(100 * suppliers_complete / 20))),
            "status": "Atteint" if suppliers_complete >= 20 else "En cours",
        },
        "market": {
            "objective": (
                "10 entreprises acheteuses contactées et 3 conversations sérieuses"
            ),
            "contactedCount": established,
            "contactedTarget": 10,
            "attemptedCount": attempted,
            "seriousCount": serious,
            "seriousTarget": 3,
            "contactedProgressPct": min(
                100, int(round(100 * established / 10))
            ),
            "seriousProgressPct": min(100, int(round(100 * serious / 3))),
            "status": (
                "Atteint"
                if established >= 10 and serious >= 3
                else "En cours"
            ),
        },
        "rules": NON_NEGOTIABLE_RULES,
    }


def admin_stats_queryset() -> QuerySet:
    from django.contrib.auth import get_user_model

    from user.models import PlatformRole, RoleGrant

    User = get_user_model()
    pilotage_role_ids = list(
        PlatformRole.objects.filter(
            code__in=[ADMIN_PILOTAGE, rbac.ULTRA_ADMIN]
        ).values_list("id", flat=True)
    )
    grant_user_ids = RoleGrant.objects.filter(
        role_id__in=pilotage_role_ids, revoked_at__isnull=True
    ).values_list("user_id", flat=True)
    return User.objects.filter(
        Q(is_superuser=True) | Q(id__in=grant_user_ids) | Q(is_staff=True)
    ).distinct()


def build_admin_tracking() -> list[dict]:
    from user.pilotage_models import PilotageBoardTask, PilotageTaskEvent

    users = admin_stats_queryset().order_by("email")
    rows = []
    for user in users:
        tasks = PilotageBoardTask.objects.filter(is_deleted=False, assignee=user)
        assigned = tasks.count()
        in_progress = tasks.filter(status=PilotageBoardTask.Status.IN_PROGRESS).count()
        done = tasks.filter(status=PilotageBoardTask.Status.DONE).count()
        blocked = tasks.filter(status=PilotageBoardTask.Status.BLOCKED).count()
        completion = 0.0 if assigned == 0 else round(100.0 * done / assigned, 1)
        last_status = (
            PilotageTaskEvent.objects.filter(
                task__assignee=user, action=PilotageTaskEvent.Action.STATUS_CHANGED
            )
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        rows.append(
            {
                "id": user.id,
                "email": user.email,
                "firstName": user.first_name or "",
                "lastName": user.last_name or "",
                "isUltraAdmin": rbac.is_ultra_admin(user),
                "assignedCount": assigned,
                "inProgressCount": in_progress,
                "doneCount": done,
                "blockedCount": blocked,
                "completionRate": completion,
                "lastActivityAt": last_status.isoformat() if last_status else None,
            }
        )
    return rows


def notify_task_assigned(*, assignee, task, actor) -> None:
    if assignee is None or actor is None or assignee.id == actor.id:
        return
    from user.models import AppNotification

    AppNotification.objects.create(
        user=assignee,
        kind=AppNotification.Kind.PILOTAGE_TASK_ASSIGNED,
        title="Nouvelle tâche de pilotage",
        body=f'«{task.title}» vous a été affectée.',
        link="/admin/pilotage",
    )


def notify_task_blocked(*, task, actor) -> None:
    from django.contrib.auth import get_user_model

    from user.models import AppNotification, RoleGrant

    User = get_user_model()
    ultra_ids = set(
        RoleGrant.objects.filter(
            role__code=rbac.ULTRA_ADMIN, revoked_at__isnull=True
        ).values_list("user_id", flat=True)
    )
    ultra_ids.update(
        User.objects.filter(is_superuser=True, is_active=True).values_list(
            "id", flat=True
        )
    )
    for uid in ultra_ids:
        if actor and uid == actor.id:
            continue
        AppNotification.objects.create(
            user_id=uid,
            kind=AppNotification.Kind.PILOTAGE_TASK_BLOCKED,
            title="Tâche de pilotage bloquée",
            body=f'«{task.title}» est passée au statut Bloqué.',
            link="/admin/pilotage",
        )
