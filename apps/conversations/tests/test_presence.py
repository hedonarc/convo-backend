from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.conversations.models import Conversation, Participant
from apps.conversations.services import presence

User = get_user_model()


class _FakeRedis:
    """Minimal in-memory stand-in for the Redis hash ops presence.py uses."""

    def __init__(self):
        self.store: dict[str, dict[str, str]] = {}

    def hset(self, key, field=None, value=None, mapping=None):
        bucket = self.store.setdefault(key, {})
        if mapping:
            bucket.update({k: str(v) for k, v in mapping.items()})
        elif field is not None:
            bucket[field] = str(value)

    def hget(self, key, field):
        return self.store.get(key, {}).get(field)

    def hgetall(self, key):
        return dict(self.store.get(key, {}))

    def hvals(self, key):
        return list(self.store.get(key, {}).values())

    def hexists(self, key, field):
        return field in self.store.get(key, {})

    def hdel(self, key, field):
        if key in self.store:
            self.store[key].pop(field, None)

    def pipeline(self):
        outer = self

        class _Pipe:
            def __init__(self):
                self.ops = []

            def hgetall(self, key):
                self.ops.append(key)
                return self

            def execute(self):
                return [dict(outer.store.get(k, {})) for k in self.ops]

        return _Pipe()


class PresenceServiceTests(TestCase):
    def setUp(self):
        self.fake = _FakeRedis()
        self.redis_patcher = patch.object(presence, "_redis", return_value=self.fake)
        self.redis_patcher.start()
        self.addCleanup(self.redis_patcher.stop)

        self.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="x"
        )
        self.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password="x"
        )
        self.charlie = User.objects.create_user(
            username="charlie", email="charlie@example.com", password="x"
        )

    def _share_conversation(self, *users):
        conv = Conversation.objects.create(created_by=users[0])
        for u in users:
            Participant.objects.create(conversation=conv, user=u)

    def test_mark_online_then_offline_flips_status(self):
        changed, payload = presence.mark_online(self.alice.id, "ch_a")
        self.assertTrue(changed)
        self.assertEqual(payload["status"], "online")

        # Same channel re-marking online is a no-op transition.
        changed_again, _ = presence.mark_online(self.alice.id, "ch_a")
        self.assertFalse(changed_again)

        changed_off, payload_off = presence.mark_offline(self.alice.id, "ch_a")
        self.assertTrue(changed_off)
        self.assertEqual(payload_off["status"], "offline")

    def test_multi_tab_user_stays_online_until_last_conn_drops(self):
        presence.mark_online(self.alice.id, "ch_tab1")
        presence.mark_online(self.alice.id, "ch_tab2")

        changed, payload = presence.mark_offline(self.alice.id, "ch_tab1")
        # Still has tab2 → stays online → no transition.
        self.assertFalse(changed)
        self.assertEqual(payload["status"], "online")

        changed, payload = presence.mark_offline(self.alice.id, "ch_tab2")
        self.assertTrue(changed)
        self.assertEqual(payload["status"], "offline")

    def test_visibility_hides_only_when_all_tabs_hidden(self):
        presence.mark_online(self.alice.id, "ch_tab1")
        presence.mark_online(self.alice.id, "ch_tab2")

        # Hide one tab → still online (other tab is visible).
        changed, payload = presence.mark_visibility(self.alice.id, "ch_tab1", False)
        self.assertFalse(changed)
        self.assertEqual(payload["status"], "online")

        # Hide the second tab → user goes away.
        changed, payload = presence.mark_visibility(self.alice.id, "ch_tab2", False)
        self.assertTrue(changed)
        self.assertEqual(payload["status"], "away")

        # First tab returns to view → online again.
        changed, payload = presence.mark_visibility(self.alice.id, "ch_tab1", True)
        self.assertTrue(changed)
        self.assertEqual(payload["status"], "online")

    def test_visibility_on_unknown_channel_is_noop(self):
        changed, _ = presence.mark_visibility(self.alice.id, "ghost_channel", False)
        self.assertFalse(changed)

    def test_get_peer_user_ids_only_includes_conversation_partners(self):
        # Alice ↔ Bob share a conversation; Charlie does not.
        self._share_conversation(self.alice, self.bob)

        peers = presence.get_peer_user_ids(self.alice.id)

        self.assertIn(self.bob.id, peers)
        self.assertNotIn(self.charlie.id, peers)
        self.assertNotIn(self.alice.id, peers)  # never include self

    def test_get_statuses_defaults_unknown_users_to_offline(self):
        presence.mark_online(self.alice.id, "ch_a")
        result = presence.get_statuses([self.alice.id, self.bob.id])

        self.assertEqual(result[self.alice.id]["status"], "online")
        self.assertEqual(result[self.bob.id]["status"], "offline")
        self.assertIsNone(result[self.bob.id]["last_seen_at"])


class PresenceEndpointTests(TestCase):
    def setUp(self):
        self.fake = _FakeRedis()
        self.redis_patcher = patch.object(presence, "_redis", return_value=self.fake)
        self.redis_patcher.start()
        self.addCleanup(self.redis_patcher.stop)

        self.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="x"
        )
        self.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password="x"
        )
        conv = Conversation.objects.create(created_by=self.alice)
        Participant.objects.create(conversation=conv, user=self.alice)
        Participant.objects.create(conversation=conv, user=self.bob)

    def test_endpoint_returns_self_and_peers(self):
        from rest_framework_simplejwt.tokens import RefreshToken

        self.client.cookies["access_token"] = str(
            RefreshToken.for_user(self.alice).access_token
        )
        presence.mark_online(self.bob.id, "bob_ch")

        response = self.client.get("/api/presence/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(str(self.alice.id), response.data)
        self.assertIn(str(self.bob.id), response.data)
        self.assertEqual(response.data[str(self.bob.id)]["status"], "online")
        # Alice never marked online → offline.
        self.assertEqual(response.data[str(self.alice.id)]["status"], "offline")


class PresenceBroadcastAudienceTests(TestCase):
    """
    Regression: `broadcast_presence` used to send only to peers, so a user
    who flipped their own status via the UserMenu never received the event
    — the menu's "active" checkmark never moved and the change looked
    broken. The fix includes self in the audience.
    """

    def setUp(self):
        self.fake = _FakeRedis()
        self.redis_patcher = patch.object(presence, "_redis", return_value=self.fake)
        self.redis_patcher.start()
        self.addCleanup(self.redis_patcher.stop)

        self.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="x"
        )
        self.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password="x"
        )
        conv = Conversation.objects.create(created_by=self.alice)
        Participant.objects.create(conversation=conv, user=self.alice)
        Participant.objects.create(conversation=conv, user=self.bob)

    def test_broadcast_includes_self_alongside_peers(self):
        sent_to: list[str] = []

        class _Layer:
            async def group_send(self, group, _payload):
                sent_to.append(group)

        with patch.object(presence, "get_channel_layer", return_value=_Layer()):
            presence.mark_online(self.alice.id, "ch")
            presence.broadcast_presence(self.alice.id)

        # Alice initiated the change and must receive it (so her menu
        # updates), and Bob is a conversation peer so he gets it too.
        self.assertIn(f"user_{self.alice.id}", sent_to)
        self.assertIn(f"user_{self.bob.id}", sent_to)
