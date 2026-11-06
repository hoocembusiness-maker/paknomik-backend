from django.test import TestCase
from rest_framework.test import APIClient

from user.events import log_buyer_event, log_system_event
from user.models import Event, User


class AdminEventRoleTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', email='admin@example.com', password='pw-123456', is_staff=True
        )
        self.buyer = User.objects.create_user(
            username='buyer', email='buyer@example.com', password='pw-123456'
        )

    def test_staff_buyer_events_are_tagged_admin(self):
        event = log_buyer_event(self.admin, Event.EventType.BUYER_SEARCH, {'query': 'carton'})
        self.assertEqual(event.actor_role, Event.ActorRole.ADMIN)

    def test_regular_buyer_keeps_buyer_role(self):
        event = log_buyer_event(self.buyer, Event.EventType.BUYER_SEARCH, {'query': 'carton'})
        self.assertEqual(event.actor_role, Event.ActorRole.BUYER)

    def test_system_events_stay_system(self):
        event = log_system_event(Event.EventType.SYSTEM_APPEAR_IN_SEARCH, {'supplier_id': 1})
        self.assertEqual(event.actor_role, Event.ActorRole.SYSTEM)

    def test_admin_events_are_split_from_buyer_counts(self):
        log_buyer_event(self.admin, Event.EventType.BUYER_SEARCH, {'query': 'a'})
        log_buyer_event(self.buyer, Event.EventType.BUYER_SEARCH, {'query': 'b'})
        client = APIClient()
        client.force_authenticate(self.admin)

        counts = client.get('/api/admin/events/meta/').json()['counts']
        self.assertEqual(counts['buyer'], 1)
        self.assertEqual(counts['admin'], 1)

        buyer_rows = client.get('/api/admin/events/', {'category': 'buyer'}).json()['results']
        self.assertEqual([row['actorUserId'] for row in buyer_rows], [self.buyer.id])
        admin_rows = client.get('/api/admin/events/', {'category': 'admin'}).json()['results']
        self.assertEqual([row['actorUserId'] for row in admin_rows], [self.admin.id])
