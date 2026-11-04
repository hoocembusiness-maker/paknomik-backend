"""API for Espace de pilotage (founder + sous-admins)."""

from __future__ import annotations

from datetime import date

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import rbac
from user.pilotage import (
    build_admin_tracking,
    build_overview,
    can_access_pilotage,
    can_manage_pilotage,
    notify_task_assigned,
    notify_task_blocked,
    seed_pilotage_catalog,
)
from user.pilotage_models import (
    PilotageBoardTask,
    PilotageDailyLog,
    PilotageGoalState,
    PilotageMarketContact,
    PilotageTaskEvent,
    PilotageWeek,
    PilotageWeekChecklistItem,
    PilotageWeeklyMetric,
)


def _denied(message="Accès refusé.", code="permission_denied", http=403):
    return Response({"error": message, "code": code}, status=http)


def _require_access(request):
    user = request.user
    if not user or not user.is_authenticated:
        return _denied("Authentification requise.", "unauthenticated", 401)
    if not user.is_active:
        return _denied("Compte désactivé.", "account_disabled")
    if not can_access_pilotage(user):
        return _denied(
            "L'Espace de pilotage est réservé aux administrateurs autorisés.",
            "pilotage_forbidden",
        )
    return None


def _require_ultra(request):
    denied = _require_access(request)
    if denied:
        return denied
    if not can_manage_pilotage(request.user):
        return _denied(
            "Réservé à l'ultra-administrateur.",
            "ultra_admin_required",
        )
    return None


def _user_brief(user):
    if user is None:
        return None
    return {
        "id": user.id,
        "email": user.email,
        "firstName": user.first_name or "",
        "lastName": user.last_name or "",
    }


def _serialize_checklist(item: PilotageWeekChecklistItem):
    return {
        "id": item.id,
        "lane": item.lane,
        "title": item.title,
        "isDone": item.is_done,
        "sortOrder": item.sort_order,
    }


def _serialize_week(week: PilotageWeek):
    items = list(week.checklist_items.all())
    return {
        "id": week.id,
        "code": week.code,
        "title": week.title,
        "objective": week.objective,
        "deliverable": week.deliverable,
        "status": week.status,
        "sortOrder": week.sort_order,
        "updatedAt": week.updated_at.isoformat() if week.updated_at else None,
        "moi": [_serialize_checklist(i) for i in items if i.lane == "moi"],
        "assistant": [
            _serialize_checklist(i) for i in items if i.lane == "assistant"
        ],
    }


def _serialize_task(task: PilotageBoardTask, *, include_events=False):
    overdue = bool(
        task.due_date
        and task.status not in (PilotageBoardTask.Status.DONE,)
        and task.due_date < date.today()
    )
    payload = {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "functionalOwner": task.functional_owner,
        "weekCode": task.week.code if task.week_id else None,
        "weekId": task.week_id,
        "assignee": _user_brief(task.assignee),
        "assignedBy": _user_brief(task.assigned_by),
        "createdBy": _user_brief(task.created_by),
        "status": task.status,
        "dueDate": task.due_date.isoformat() if task.due_date else None,
        "isOverdue": overdue,
        "createdAt": task.created_at.isoformat() if task.created_at else None,
        "updatedAt": task.updated_at.isoformat() if task.updated_at else None,
    }
    if include_events:
        payload["events"] = [
            {
                "id": e.id,
                "action": e.action,
                "actor": _user_brief(e.actor),
                "oldValue": e.old_value or {},
                "newValue": e.new_value or {},
                "createdAt": e.created_at.isoformat() if e.created_at else None,
            }
            for e in task.events.select_related("actor").all()[:100]
        ]
    return payload


def _serialize_daily_log(log: PilotageDailyLog):
    return {
        "id": log.id,
        "logDate": log.log_date.isoformat(),
        "mission": log.mission,
        "result": log.result,
        "tomorrowStep": log.tomorrow_step,
        "author": _user_brief(log.author),
        "createdAt": log.created_at.isoformat() if log.created_at else None,
        "updatedAt": log.updated_at.isoformat() if log.updated_at else None,
    }


def _visible_tasks_qs(user):
    qs = PilotageBoardTask.objects.filter(is_deleted=False).select_related(
        "week", "assignee", "assigned_by", "created_by"
    )
    if can_manage_pilotage(user):
        return qs
    return qs.filter(assignee=user)


def _record_event(task, actor, action, old_value=None, new_value=None):
    PilotageTaskEvent.objects.create(
        task=task,
        actor=actor,
        action=action,
        old_value=old_value or {},
        new_value=new_value or {},
    )


class PilotageBootstrapView(APIView):
    """Ensure catalog exists; return role flags + rules."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _require_access(request)
        if denied:
            return denied
        seed_pilotage_catalog()
        ultra = can_manage_pilotage(request.user)
        return Response(
            {
                "isUltraAdmin": ultra,
                "canManage": ultra,
                "canAccess": True,
                "rules": build_overview()["rules"],
            }
        )


class PilotageOverviewView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _require_ultra(request)
        if denied:
            return denied
        seed_pilotage_catalog()
        return Response(build_overview())

    def patch(self, request):
        denied = _require_ultra(request)
        if denied:
            return denied
        goals, _ = PilotageGoalState.objects.get_or_create(pk=1)
        data = request.data or {}
        if "journeyTested" in data:
            goals.product_journey_tested = bool(data.get("journeyTested"))
        if "noCriticalBugs" in data:
            goals.product_no_critical_bugs = bool(data.get("noCriticalBugs"))
        if "notes" in data:
            goals.notes = str(data.get("notes") or "")
        goals.updated_by = request.user
        goals.save()
        return Response(build_overview())


class PilotageWeeksView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _require_access(request)
        if denied:
            return denied
        seed_pilotage_catalog()
        weeks = PilotageWeek.objects.prefetch_related("checklist_items").all()
        return Response([_serialize_week(w) for w in weeks])


class PilotageWeekDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, week_id):
        denied = _require_access(request)
        if denied:
            return denied
        # Sous-admins may toggle their checklist / deliverable notes if assigned
        # work exists; week status/deliverable edits require manage.
        week = get_object_or_404(
            PilotageWeek.objects.prefetch_related("checklist_items"), pk=week_id
        )
        data = request.data or {}
        manage = can_manage_pilotage(request.user)

        if "status" in data or "deliverable" in data or "objective" in data:
            if not manage:
                return _denied("Modification de la fiche semaine réservée à l'ultra-admin.")
            if "status" in data:
                status_val = str(data.get("status") or "")
                if status_val not in PilotageWeek.Status.values:
                    return Response(
                        {"error": "Statut de semaine invalide.", "code": "invalid_status"},
                        status=400,
                    )
                week.status = status_val
            if "deliverable" in data:
                week.deliverable = str(data.get("deliverable") or "")
            if "objective" in data:
                week.objective = str(data.get("objective") or "")
            week.save()

        checklist_updates = data.get("checklist") or []
        if checklist_updates:
            for row in checklist_updates:
                item_id = row.get("id")
                if not item_id:
                    continue
                item = week.checklist_items.filter(pk=item_id).first()
                if item is None:
                    continue
                if "isDone" in row:
                    item.is_done = bool(row.get("isDone"))
                    item.save(update_fields=["is_done", "updated_at"])

        week.refresh_from_db()
        week = PilotageWeek.objects.prefetch_related("checklist_items").get(pk=week.pk)
        return Response(_serialize_week(week))


class PilotageTasksView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _require_access(request)
        if denied:
            return denied
        qs = _visible_tasks_qs(request.user)
        week = request.query_params.get("week")
        status_f = request.query_params.get("status")
        assignee = request.query_params.get("assignee")
        search = (request.query_params.get("q") or "").strip()
        if week:
            qs = qs.filter(week__code=week)
        if status_f:
            qs = qs.filter(status=status_f)
        if assignee and can_manage_pilotage(request.user):
            qs = qs.filter(assignee_id=assignee)
        if search:
            qs = qs.filter(title__icontains=search)
        return Response([_serialize_task(t) for t in qs[:500]])

    def post(self, request):
        denied = _require_ultra(request)
        if denied:
            return denied
        data = request.data or {}
        title = (data.get("title") or "").strip()
        if not title:
            return Response(
                {"error": "Le titre est obligatoire.", "code": "title_required"},
                status=400,
            )
        owner = data.get("functionalOwner") or PilotageBoardTask.FunctionalOwner.MOI
        if owner not in PilotageBoardTask.FunctionalOwner.values:
            return Response(
                {"error": "Responsable fonctionnel invalide.", "code": "invalid_owner"},
                status=400,
            )
        week = None
        week_code = data.get("weekCode")
        if week_code:
            week = PilotageWeek.objects.filter(code=week_code).first()
            if week is None:
                return Response(
                    {"error": "Semaine inconnue.", "code": "invalid_week"},
                    status=400,
                )
        assignee = None
        assignee_id = data.get("assigneeId")
        if assignee_id:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            assignee = User.objects.filter(pk=assignee_id, is_active=True).first()
            if assignee is None:
                return Response(
                    {"error": "Administrateur introuvable.", "code": "invalid_assignee"},
                    status=400,
                )
        due = data.get("dueDate") or None
        due_date = None
        if due:
            try:
                due_date = date.fromisoformat(str(due)[:10])
            except ValueError:
                return Response(
                    {"error": "Date d'échéance invalide.", "code": "invalid_due_date"},
                    status=400,
                )

        with transaction.atomic():
            task = PilotageBoardTask.objects.create(
                title=title,
                description=str(data.get("description") or ""),
                functional_owner=owner,
                week=week,
                assignee=assignee,
                assigned_by=request.user if assignee else None,
                created_by=request.user,
                status=PilotageBoardTask.Status.TODO,
                due_date=due_date,
            )
            _record_event(
                task,
                request.user,
                PilotageTaskEvent.Action.CREATED,
                new_value={"title": title},
            )
            if assignee:
                _record_event(
                    task,
                    request.user,
                    PilotageTaskEvent.Action.ASSIGNED,
                    new_value={
                        "assigneeId": assignee.id,
                        "assignedById": request.user.id,
                    },
                )
                notify_task_assigned(
                    assignee=assignee, task=task, actor=request.user
                )
        return Response(_serialize_task(task), status=201)


class PilotageTaskDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        denied = _require_access(request)
        if denied:
            return denied
        task = get_object_or_404(
            PilotageBoardTask.objects.select_related(
                "week", "assignee", "assigned_by", "created_by"
            ),
            pk=task_id,
            is_deleted=False,
        )
        if not can_manage_pilotage(request.user) and task.assignee_id != request.user.id:
            return _denied("Cette tâche ne vous est pas affectée.")
        include_events = can_manage_pilotage(request.user) or (
            task.assignee_id == request.user.id
        )
        return Response(_serialize_task(task, include_events=include_events))

    def patch(self, request, task_id):
        denied = _require_access(request)
        if denied:
            return denied
        task = get_object_or_404(
            PilotageBoardTask.objects.select_related("week", "assignee"),
            pk=task_id,
            is_deleted=False,
        )
        manage = can_manage_pilotage(request.user)
        is_assignee = task.assignee_id == request.user.id
        if not manage and not is_assignee:
            return _denied("Cette tâche ne vous est pas affectée.")

        data = request.data or {}
        # Sous-admin: status only
        if not manage:
            if set(data.keys()) - {"status"}:
                return _denied(
                    "Un sous-admin ne peut modifier que le statut de ses tâches."
                )
            new_status = data.get("status")
            if new_status not in PilotageBoardTask.Status.values:
                return Response(
                    {"error": "Statut invalide.", "code": "invalid_status"},
                    status=400,
                )
            old = task.status
            if old != new_status:
                task.status = new_status
                task.save(update_fields=["status", "updated_at"])
                _record_event(
                    task,
                    request.user,
                    PilotageTaskEvent.Action.STATUS_CHANGED,
                    old_value={"status": old},
                    new_value={"status": new_status},
                )
                if new_status == PilotageBoardTask.Status.BLOCKED:
                    notify_task_blocked(task=task, actor=request.user)
            return Response(_serialize_task(task))

        # Ultra / manage
        changed = False
        if "title" in data:
            title = (data.get("title") or "").strip()
            if not title:
                return Response(
                    {"error": "Le titre est obligatoire.", "code": "title_required"},
                    status=400,
                )
            if title != task.title:
                _record_event(
                    task,
                    request.user,
                    PilotageTaskEvent.Action.UPDATED,
                    old_value={"title": task.title},
                    new_value={"title": title},
                )
                task.title = title
                changed = True
        if "description" in data:
            desc = str(data.get("description") or "")
            if desc != task.description:
                task.description = desc
                changed = True
        if "functionalOwner" in data:
            owner = data.get("functionalOwner")
            if owner not in PilotageBoardTask.FunctionalOwner.values:
                return Response(
                    {"error": "Responsable fonctionnel invalide.", "code": "invalid_owner"},
                    status=400,
                )
            task.functional_owner = owner
            changed = True
        if "weekCode" in data:
            code = data.get("weekCode")
            if code:
                week = PilotageWeek.objects.filter(code=code).first()
                if week is None:
                    return Response(
                        {"error": "Semaine inconnue.", "code": "invalid_week"},
                        status=400,
                    )
                task.week = week
            else:
                task.week = None
            changed = True
        if "dueDate" in data:
            due = data.get("dueDate")
            if due:
                try:
                    task.due_date = date.fromisoformat(str(due)[:10])
                except ValueError:
                    return Response(
                        {"error": "Date d'échéance invalide.", "code": "invalid_due_date"},
                        status=400,
                    )
            else:
                task.due_date = None
            changed = True
        if "assigneeId" in data:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            assignee_id = data.get("assigneeId")
            old_id = task.assignee_id
            if assignee_id:
                assignee = User.objects.filter(pk=assignee_id, is_active=True).first()
                if assignee is None:
                    return Response(
                        {"error": "Administrateur introuvable.", "code": "invalid_assignee"},
                        status=400,
                    )
                task.assignee = assignee
                task.assigned_by = request.user
            else:
                task.assignee = None
                task.assigned_by = None
            if old_id != task.assignee_id:
                _record_event(
                    task,
                    request.user,
                    PilotageTaskEvent.Action.ASSIGNED,
                    old_value={"assigneeId": old_id},
                    new_value={
                        "assigneeId": task.assignee_id,
                        "assignedById": request.user.id,
                    },
                )
                notify_task_assigned(
                    assignee=task.assignee, task=task, actor=request.user
                )
            changed = True
        if "status" in data:
            new_status = data.get("status")
            if new_status not in PilotageBoardTask.Status.values:
                return Response(
                    {"error": "Statut invalide.", "code": "invalid_status"},
                    status=400,
                )
            if new_status != task.status:
                old = task.status
                task.status = new_status
                _record_event(
                    task,
                    request.user,
                    PilotageTaskEvent.Action.STATUS_CHANGED,
                    old_value={"status": old},
                    new_value={"status": new_status},
                )
                if new_status == PilotageBoardTask.Status.BLOCKED:
                    notify_task_blocked(task=task, actor=request.user)
                changed = True
        if changed:
            task.save()
        return Response(_serialize_task(task))

    def delete(self, request, task_id):
        denied = _require_ultra(request)
        if denied:
            return denied
        task = get_object_or_404(PilotageBoardTask, pk=task_id, is_deleted=False)
        task.is_deleted = True
        task.save(update_fields=["is_deleted", "updated_at"])
        _record_event(
            task,
            request.user,
            PilotageTaskEvent.Action.DELETED,
            old_value={"title": task.title},
        )
        return Response({"ok": True})


class PilotageMetricsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _require_ultra(request)
        if denied:
            return denied
        seed_pilotage_catalog()
        metrics = PilotageWeeklyMetric.objects.select_related("week").all()
        by_key: dict[str, dict] = {}
        for m in metrics:
            row = by_key.setdefault(
                m.metric_key,
                {
                    "key": m.metric_key,
                    "label": m.metric_label,
                    "sortOrder": m.sort_order,
                    "weeks": {},
                },
            )
            row["weeks"][m.week.code] = {
                "id": m.id,
                "targetLabel": m.target_label,
                "targetValue": m.target_value,
                "actualValue": m.actual_value,
            }
        ordered = sorted(by_key.values(), key=lambda r: r["sortOrder"])
        return Response({"metrics": ordered, "weekCodes": ["S1", "S2", "S3", "S4"]})

    def patch(self, request):
        denied = _require_ultra(request)
        if denied:
            return denied
        updates = request.data.get("updates") or []
        for row in updates:
            metric_id = row.get("id")
            if not metric_id:
                continue
            metric = PilotageWeeklyMetric.objects.filter(pk=metric_id).first()
            if metric is None:
                continue
            if "actualValue" in row:
                try:
                    metric.actual_value = max(0, int(row.get("actualValue")))
                except (TypeError, ValueError):
                    return Response(
                        {"error": "Valeur réelle invalide.", "code": "invalid_actual"},
                        status=400,
                    )
            if "targetValue" in row:
                try:
                    metric.target_value = max(0, int(row.get("targetValue")))
                except (TypeError, ValueError):
                    return Response(
                        {"error": "Cible invalide.", "code": "invalid_target"},
                        status=400,
                    )
            if "targetLabel" in row:
                metric.target_label = str(row.get("targetLabel") or "")[:32]
            metric.save()
        return self.get(request)


class PilotageDailyLogsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _require_access(request)
        if denied:
            return denied
        qs = PilotageDailyLog.objects.select_related("author").all()
        if not can_manage_pilotage(request.user):
            qs = qs.filter(author=request.user)
        log_date = request.query_params.get("date")
        if log_date:
            qs = qs.filter(log_date=log_date)
        return Response([_serialize_daily_log(x) for x in qs[:200]])

    def post(self, request):
        denied = _require_access(request)
        if denied:
            return denied
        data = request.data or {}
        mission = (data.get("mission") or "").strip()
        if not mission:
            return Response(
                {"error": "La mission du jour est obligatoire.", "code": "mission_required"},
                status=400,
            )
        log_date_raw = data.get("logDate")
        if log_date_raw:
            try:
                log_date = date.fromisoformat(str(log_date_raw)[:10])
            except ValueError:
                return Response(
                    {"error": "Date invalide.", "code": "invalid_date"},
                    status=400,
                )
        else:
            log_date = timezone.localdate()

        # Idempotent upsert avoids double-submit duplicates
        log, created = PilotageDailyLog.objects.update_or_create(
            author=request.user,
            log_date=log_date,
            defaults={
                "mission": mission,
                "result": str(data.get("result") or ""),
                "tomorrow_step": str(data.get("tomorrowStep") or ""),
            },
        )
        return Response(
            _serialize_daily_log(log),
            status=201 if created else 200,
        )


class PilotageDailyLogDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, log_id):
        denied = _require_access(request)
        if denied:
            return denied
        log = get_object_or_404(PilotageDailyLog, pk=log_id)
        if not can_manage_pilotage(request.user) and log.author_id != request.user.id:
            return _denied("Journal non autorisé.")
        data = request.data or {}
        if "mission" in data:
            mission = (data.get("mission") or "").strip()
            if not mission:
                return Response(
                    {"error": "La mission du jour est obligatoire.", "code": "mission_required"},
                    status=400,
                )
            log.mission = mission
        if "result" in data:
            log.result = str(data.get("result") or "")
        if "tomorrowStep" in data:
            log.tomorrow_step = str(data.get("tomorrowStep") or "")
        log.save()
        return Response(_serialize_daily_log(log))


class PilotageAdminsTrackingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _require_ultra(request)
        if denied:
            return denied
        return Response({"admins": build_admin_tracking()})


class PilotageAdminDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, admin_id):
        denied = _require_ultra(request)
        if denied:
            return denied
        from django.contrib.auth import get_user_model

        User = get_user_model()
        admin = get_object_or_404(User, pk=admin_id)
        tasks = (
            PilotageBoardTask.objects.filter(is_deleted=False, assignee=admin)
            .select_related("week", "assignee", "assigned_by", "created_by")
            .prefetch_related("events__actor")
        )
        return Response(
            {
                "admin": _user_brief(admin),
                "tasks": [
                    _serialize_task(t, include_events=True) for t in tasks
                ],
            }
        )


class PilotageAssignableAdminsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _require_ultra(request)
        if denied:
            return denied
        from django.contrib.auth import get_user_model

        from user.models import RoleGrant
        from user.pilotage import ADMIN_PILOTAGE

        User = get_user_model()
        ids = set(
            RoleGrant.objects.filter(
                role__code__in=[ADMIN_PILOTAGE, rbac.ULTRA_ADMIN],
                revoked_at__isnull=True,
            ).values_list("user_id", flat=True)
        )
        ids.update(
            User.objects.filter(is_superuser=True, is_active=True).values_list(
                "id", flat=True
            )
        )
        users = User.objects.filter(id__in=ids, is_active=True).order_by("email")
        return Response([_user_brief(u) for u in users])


class PilotageMarketContactsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _require_ultra(request)
        if denied:
            return denied
        rows = PilotageMarketContact.objects.filter(is_deleted=False).select_related(
            "created_by"
        )[:200]
        return Response(
            [
                {
                    "id": c.id,
                    "companyName": c.company_name,
                    "status": c.status,
                    "notes": c.notes,
                    "contactedAt": c.contacted_at.isoformat() if c.contacted_at else None,
                    "createdBy": _user_brief(c.created_by),
                }
                for c in rows
            ]
        )

    def post(self, request):
        denied = _require_ultra(request)
        if denied:
            return denied
        data = request.data or {}
        name = (data.get("companyName") or "").strip()
        if not name:
            return Response(
                {"error": "Nom d'entreprise obligatoire.", "code": "name_required"},
                status=400,
            )
        st = data.get("status") or PilotageMarketContact.Status.ATTEMPTED
        if st not in PilotageMarketContact.Status.values:
            return Response(
                {"error": "Statut invalide.", "code": "invalid_status"},
                status=400,
            )
        contacted = None
        if data.get("contactedAt"):
            try:
                contacted = date.fromisoformat(str(data.get("contactedAt"))[:10])
            except ValueError:
                return Response(
                    {"error": "Date invalide.", "code": "invalid_date"},
                    status=400,
                )
        contact = PilotageMarketContact.objects.create(
            company_name=name,
            status=st,
            notes=str(data.get("notes") or ""),
            contacted_at=contacted,
            created_by=request.user,
        )
        return Response(
            {
                "id": contact.id,
                "companyName": contact.company_name,
                "status": contact.status,
                "notes": contact.notes,
                "contactedAt": contact.contacted_at.isoformat()
                if contact.contacted_at
                else None,
            },
            status=201,
        )


class PilotageMarketContactDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, contact_id):
        denied = _require_ultra(request)
        if denied:
            return denied
        contact = get_object_or_404(
            PilotageMarketContact, pk=contact_id, is_deleted=False
        )
        data = request.data or {}
        if "companyName" in data:
            name = (data.get("companyName") or "").strip()
            if not name:
                return Response(
                    {"error": "Nom d'entreprise obligatoire.", "code": "name_required"},
                    status=400,
                )
            contact.company_name = name
        if "status" in data:
            st = data.get("status")
            if st not in PilotageMarketContact.Status.values:
                return Response(
                    {"error": "Statut invalide.", "code": "invalid_status"},
                    status=400,
                )
            contact.status = st
        if "notes" in data:
            contact.notes = str(data.get("notes") or "")
        contact.save()
        return Response(
            {
                "id": contact.id,
                "companyName": contact.company_name,
                "status": contact.status,
                "notes": contact.notes,
            }
        )

    def delete(self, request, contact_id):
        denied = _require_ultra(request)
        if denied:
            return denied
        contact = get_object_or_404(
            PilotageMarketContact, pk=contact_id, is_deleted=False
        )
        contact.is_deleted = True
        contact.save(update_fields=["is_deleted", "updated_at"])
        return Response({"ok": True})
