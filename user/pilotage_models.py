"""Espace de pilotage — 30-day execution plan models."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class PilotageWeek(models.Model):
    class Status(models.TextChoices):
        UPCOMING = "upcoming", "À venir"
        IN_PROGRESS = "in_progress", "En cours"
        DONE = "done", "Terminée"

    code = models.CharField(max_length=8, unique=True)  # S1..S4
    title = models.CharField(max_length=120)
    objective = models.TextField(blank=True, default="")
    deliverable = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.UPCOMING
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "code"]
        verbose_name = "Pilotage week"
        verbose_name_plural = "Pilotage weeks"

    def __str__(self):
        return f"{self.code} — {self.title}"


class PilotageWeekChecklistItem(models.Model):
    class Lane(models.TextChoices):
        MOI = "moi", "Moi"
        ASSISTANT = "assistant", "Assistant"

    week = models.ForeignKey(
        PilotageWeek, on_delete=models.CASCADE, related_name="checklist_items"
    )
    lane = models.CharField(max_length=20, choices=Lane.choices)
    title = models.CharField(max_length=255)
    is_done = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["lane", "sort_order", "id"]
        verbose_name = "Pilotage week checklist item"
        verbose_name_plural = "Pilotage week checklist items"

    def __str__(self):
        return f"{self.week.code}/{self.lane}: {self.title}"


class PilotageBoardTask(models.Model):
    class FunctionalOwner(models.TextChoices):
        MOI = "moi", "Moi"
        ASSISTANT = "assistant", "Assistant"
        MOI_CURSOR = "moi_cursor", "Moi + Cursor"

    class Status(models.TextChoices):
        TODO = "todo", "À faire"
        IN_PROGRESS = "in_progress", "En cours"
        DONE = "done", "Fait"
        BLOCKED = "blocked", "Bloqué"

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    functional_owner = models.CharField(
        max_length=20, choices=FunctionalOwner.choices, default=FunctionalOwner.MOI
    )
    week = models.ForeignKey(
        PilotageWeek,
        on_delete=models.PROTECT,
        related_name="board_tasks",
        null=True,
        blank=True,
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pilotage_assigned_tasks",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pilotage_assigned_by_tasks",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pilotage_created_tasks",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.TODO, db_index=True
    )
    due_date = models.DateField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        verbose_name = "Pilotage board task"
        verbose_name_plural = "Pilotage board tasks"

    def __str__(self):
        return self.title


class PilotageTaskEvent(models.Model):
    class Action(models.TextChoices):
        CREATED = "created", "Création"
        UPDATED = "updated", "Modification"
        ASSIGNED = "assigned", "Affectation"
        STATUS_CHANGED = "status_changed", "Changement de statut"
        DELETED = "deleted", "Suppression"

    task = models.ForeignKey(
        PilotageBoardTask, on_delete=models.CASCADE, related_name="events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pilotage_task_events",
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    old_value = models.JSONField(default=dict, blank=True)
    new_value = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Pilotage task event"
        verbose_name_plural = "Pilotage task events"

    def __str__(self):
        return f"{self.action} on task #{self.task_id}"


class PilotageWeeklyMetric(models.Model):
    metric_key = models.CharField(max_length=64, db_index=True)
    metric_label = models.CharField(max_length=120)
    week = models.ForeignKey(
        PilotageWeek, on_delete=models.CASCADE, related_name="metrics"
    )
    target_label = models.CharField(max_length=32)
    target_value = models.PositiveIntegerField(
        help_text="Valeur numérique pour comparaison (borne haute ou cible exacte)."
    )
    actual_value = models.PositiveIntegerField(default=0)
    sort_order = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "metric_key", "week__sort_order"]
        unique_together = [("metric_key", "week")]
        verbose_name = "Pilotage weekly metric"
        verbose_name_plural = "Pilotage weekly metrics"

    def __str__(self):
        return f"{self.metric_key} / {self.week.code}"


class PilotageDailyLog(models.Model):
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pilotage_daily_logs",
    )
    log_date = models.DateField(db_index=True)
    mission = models.TextField()
    result = models.TextField(blank=True, default="")
    tomorrow_step = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-log_date", "-created_at"]
        unique_together = [("author", "log_date")]
        verbose_name = "Pilotage daily log"
        verbose_name_plural = "Pilotage daily logs"

    def __str__(self):
        return f"{self.log_date} — {self.author_id}"


class PilotageGoalState(models.Model):
    """Singleton row (pk=1) for product-journey overview flags."""

    product_journey_tested = models.BooleanField(default=False)
    product_no_critical_bugs = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "Pilotage goal state"
        verbose_name_plural = "Pilotage goal state"

    def __str__(self):
        return "Pilotage goals"


class PilotageMarketContact(models.Model):
    class Status(models.TextChoices):
        ATTEMPTED = "attempted", "Contact tenté"
        ESTABLISHED = "established", "Contact établi"
        SERIOUS = "serious", "Conversation sérieuse"

    company_name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ATTEMPTED
    )
    notes = models.TextField(blank=True, default="")
    contacted_at = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pilotage_market_contacts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ["-updated_at", "-id"]
        verbose_name = "Pilotage market contact"
        verbose_name_plural = "Pilotage market contacts"

    def __str__(self):
        return f"{self.company_name} ({self.status})"
