from datetime import date, datetime

from django.test import TestCase
from django.utils import timezone

from user.models import Event
from user.reporting_period import aware_range_for_local_dates, filter_created_in_local_period


class ReportingPeriodTests(TestCase):
    def test_local_midnight_event_stays_on_same_calendar_day(self):
        tz = timezone.get_current_timezone()
        local_day = date(2026, 10, 2)
        lo, hi = aware_range_for_local_dates(local_day, local_day)
        at_night = timezone.make_aware(datetime(2026, 10, 2, 23, 30), tz)
        event = Event.objects.create(
            event_type=Event.EventType.BUYER_SEARCH,
            actor_role=Event.ActorRole.VISITOR,
            context={"query": "test"},
        )
        Event.objects.filter(pk=event.pk).update(created_at=at_night)
        count = filter_created_in_local_period(
            Event.objects.filter(event_type=Event.EventType.BUYER_SEARCH),
            local_day,
            local_day,
        ).count()
        self.assertEqual(count, 1)
