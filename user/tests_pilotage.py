"""Tests for Espace de pilotage permissions, tasks, seed and stats."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from user import rbac
from user.models import PlatformRole, RoleGrant
from user.pilotage import seed_pilotage_catalog
from user.pilotage_models import (
    PilotageBoardTask,
    PilotageDailyLog,
    PilotageTaskEvent,
    PilotageWeek,
    PilotageWeeklyMetric,
)

User = get_user_model()


class PilotageApiTests(TestCase):
    def setUp(self):
        rbac.seed_roles()
        seed_pilotage_catalog()
        self.ultra = User.objects.create_user(
            username="ultra_pilot",
            email="ultra-pilot@example.com",
            password="x",
            is_staff=True,
        )
        RoleGrant.objects.create(
            user=self.ultra,
            role=PlatformRole.objects.get(code=rbac.ULTRA_ADMIN),
        )
        self.assistant = User.objects.create_user(
            username="asst_pilot",
            email="asst-pilot@example.com",
            password="x",
            is_staff=True,
        )
        RoleGrant.objects.create(
            user=self.assistant,
            role=PlatformRole.objects.get(code=rbac.ADMIN_PILOTAGE),
        )
        self.other = User.objects.create_user(
            username="other_pilot",
            email="other-pilot@example.com",
            password="x",
            is_staff=True,
        )
        RoleGrant.objects.create(
            user=self.other,
            role=PlatformRole.objects.get(code=rbac.ADMIN_PILOTAGE),
        )
        self.client = APIClient()

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    def test_ultra_sees_overview_assistant_forbidden(self):
        self._auth(self.ultra)
        res = self.client.get("/api/admin/pilotage/overview/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("product", res.json())
        self.assertIn("suppliers", res.json())
        self.assertIn("market", res.json())

        self._auth(self.assistant)
        res = self.client.get("/api/admin/pilotage/overview/")
        self.assertEqual(res.status_code, 403)

    def test_assistant_sees_only_own_tasks(self):
        week = PilotageWeek.objects.get(code="S1")
        mine = PilotageBoardTask.objects.create(
            title="Ma tâche",
            week=week,
            assignee=self.assistant,
            assigned_by=self.ultra,
            created_by=self.ultra,
        )
        other = PilotageBoardTask.objects.create(
            title="Tâche autre",
            week=week,
            assignee=self.other,
            assigned_by=self.ultra,
            created_by=self.ultra,
        )
        self._auth(self.assistant)
        res = self.client.get("/api/admin/pilotage/tasks/")
        self.assertEqual(res.status_code, 200)
        ids = {row["id"] for row in res.json()}
        self.assertIn(mine.id, ids)
        self.assertNotIn(other.id, ids)

        res = self.client.get(f"/api/admin/pilotage/tasks/{other.id}/")
        self.assertEqual(res.status_code, 403)

    def test_assistant_cannot_reassign_or_delete(self):
        week = PilotageWeek.objects.get(code="S1")
        task = PilotageBoardTask.objects.create(
            title="Statut only",
            week=week,
            assignee=self.assistant,
            assigned_by=self.ultra,
            created_by=self.ultra,
        )
        self._auth(self.assistant)
        res = self.client.patch(
            f"/api/admin/pilotage/tasks/{task.id}/",
            {"assigneeId": self.other.id},
            format="json",
        )
        self.assertEqual(res.status_code, 403)

        res = self.client.delete(f"/api/admin/pilotage/tasks/{task.id}/")
        self.assertEqual(res.status_code, 403)

        res = self.client.patch(
            f"/api/admin/pilotage/tasks/{task.id}/",
            {"status": "in_progress"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "in_progress")
        self.assertTrue(
            PilotageTaskEvent.objects.filter(
                task=task,
                action=PilotageTaskEvent.Action.STATUS_CHANGED,
                actor=self.assistant,
            ).exists()
        )

    def test_assignment_records_actor_and_assignee(self):
        self._auth(self.ultra)
        res = self.client.post(
            "/api/admin/pilotage/tasks/",
            {
                "title": "Nouvelle",
                "weekCode": "S2",
                "assigneeId": self.assistant.id,
                "functionalOwner": "assistant",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        task_id = res.json()["id"]
        ev = PilotageTaskEvent.objects.filter(
            task_id=task_id, action=PilotageTaskEvent.Action.ASSIGNED
        ).first()
        self.assertIsNotNone(ev)
        self.assertEqual(ev.actor_id, self.ultra.id)
        self.assertEqual(ev.new_value.get("assigneeId"), self.assistant.id)

    def test_seed_idempotent(self):
        weeks_before = PilotageWeek.objects.count()
        metrics_before = PilotageWeeklyMetric.objects.count()
        first = seed_pilotage_catalog()
        second = seed_pilotage_catalog()
        self.assertEqual(PilotageWeek.objects.count(), weeks_before)
        self.assertEqual(PilotageWeeklyMetric.objects.count(), metrics_before)
        self.assertEqual(first["weeks"], 0)
        self.assertEqual(second["metrics"], 0)
        self.assertEqual(PilotageWeek.objects.count(), 4)
        self.assertEqual(PilotageWeeklyMetric.objects.count(), 20)

    def test_metrics_and_daily_log_persist(self):
        self._auth(self.ultra)
        metric = PilotageWeeklyMetric.objects.filter(week__code="S1").first()
        res = self.client.patch(
            "/api/admin/pilotage/metrics/",
            {"updates": [{"id": metric.id, "actualValue": 3}]},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        metric.refresh_from_db()
        self.assertEqual(metric.actual_value, 3)

        res = self.client.post(
            "/api/admin/pilotage/daily-logs/",
            {
                "mission": "Tester le parcours",
                "result": "3 bugs listés",
                "tomorrowStep": "Corriger le critique",
            },
            format="json",
        )
        self.assertIn(res.status_code, (200, 201))
        # Double submit same day → upsert, no second row
        res2 = self.client.post(
            "/api/admin/pilotage/daily-logs/",
            {
                "mission": "Tester le parcours (bis)",
                "result": "ok",
                "tomorrowStep": "suite",
            },
            format="json",
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(
            PilotageDailyLog.objects.filter(
                author=self.ultra, log_date=date.today()
            ).count(),
            1,
        )

    def test_admin_stats_empty_completion_is_zero(self):
        self._auth(self.ultra)
        res = self.client.get("/api/admin/pilotage/admins/")
        self.assertEqual(res.status_code, 200)
        rows = res.json()["admins"]
        asst = next(r for r in rows if r["id"] == self.assistant.id)
        self.assertEqual(asst["assignedCount"], 0)
        self.assertEqual(asst["completionRate"], 0.0)

    def test_assistant_cannot_read_metrics_or_other_logs(self):
        PilotageDailyLog.objects.create(
            author=self.ultra,
            log_date=date.today() - timedelta(days=1),
            mission="Privé ultra",
        )
        self._auth(self.assistant)
        self.assertEqual(self.client.get("/api/admin/pilotage/metrics/").status_code, 403)
        self.assertEqual(self.client.get("/api/admin/pilotage/admins/").status_code, 403)
        logs = self.client.get("/api/admin/pilotage/daily-logs/").json()
        self.assertEqual(logs, [])
