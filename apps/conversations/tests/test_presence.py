from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.conversations.models import Conversation, Participant
from apps.conversations.services import presence

User = get_user_model()


class _FakeRedis:
    """
    Minimal in-memory stand-in for the Redis ops presence.py uses.

    Holds three kinds of data behind a single `store` dict:
      - hashes (dict-of-dicts) — `presence:user:<id>`
      - strings — `presence:conn:<id>:<channel>`
      - sorted sets (dict[member]=score) — `presence:conns:<id>` (index)

    TTLs are ignored; tests don't advance time. Status-recompute logic in
    presence.py prunes by score (ZSET), so tests that need to simulate
    expiry can ZREM directly or set a score in the past.
    """

    def __init__(self):
        self.store: dict = {}

    # ── Hash ops ──────────────────────────────────────────────────────────

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

    # ── String ops ────────────────────────────────────────────────────────

    def set(self, key, value, ex=None):  # noqa: ARG002 — ex ignored in tests
        self.store[key] = str(value)

    def get(self, key):
        value = self.store.get(key)
        # Defensively avoid returning hash/zset structures as strings.
        if isinstance(value, dict):
            return None
        return value

    def mget(self, keys):
        return [self.get(k) for k in keys]

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    def exists(self, *keys):
        return sum(1 for k in keys if k in self.store)

    def expire(self, key, _seconds):
        # TTL is a real-Redis concern. The fake doesn't model time, so
        # EXPIRE is a no-op when the key exists and returns 0 otherwise
        # — matches the contract the production code relies on (it
        # ignores EXPIRE's return value).
        return 1 if key in self.store else 0

    # ── Sorted-set ops ────────────────────────────────────────────────────

    def zadd(self, key, mapping):
        bucket = self.store.setdefault(key, {})
        if not isinstance(bucket, dict):
            raise TypeError(f"WRONGTYPE on {key}")
        for member, score in mapping.items():
            bucket[member] = float(score)

    def zrange(self, key, start, end):
        bucket = self.store.get(key, {})
        members = sorted(bucket.items(), key=lambda kv: kv[1])
        names = [m for m, _ in members]
        if end == -1:
            return names[start:]
        return names[start : end + 1]

    def zrem(self, key, *members):
        bucket = self.store.get(key)
        if not isinstance(bucket, dict):
            return
        for m in members:
            bucket.pop(m, None)
        if not bucket:
            self.store.pop(key, None)

    def zremrangebyscore(self, key, min_score, max_score):
        bucket = self.store.get(key)
        if not isinstance(bucket, dict):
            return
        to_remove = [m for m, s in bucket.items() if min_score <= s <= max_score]
        for m in to_remove:
            bucket.pop(m, None)
        if not bucket:
            self.store.pop(key, None)

    # ── Pipeline ──────────────────────────────────────────────────────────

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

    def test_expired_index_entries_are_pruned_on_recompute(self):
        """
        Regression: stale connections left over from prior sessions (browser
        crash, dev-server kill, missed disconnect) used to accumulate in
        Redis forever — every one of them was treated as `online`, so the
        user-level status was permanently pinned to ONLINE and the
        `_persist_status` no-op guard suppressed all broadcasts. Visibility
        flips silently dropped.

        Now: every recompute checks each indexed channel for an
        actual per-channel key. Members whose key has TTL'd out get
        pruned from the index in the same pass. We simulate the
        TTL-expired case by leaving the ZSET member behind while
        omitting (or deleting) the per-channel key.
        """
        presence.mark_online(self.alice.id, "live_tab")

        # Plant a zombie: it's in the index, but its per-channel key
        # never existed (or has already expired). This is what the
        # state looks like in real Redis when the per-channel key's
        # TTL runs out before the index member is touched again.
        index_key = presence._conns_index_key(self.alice.id)
        self.fake.zadd(index_key, {"zombie_tab": 0})

        # Flip the live tab away — without pruning, the zombie would
        # be treated as a live ONLINE channel and the user-level
        # status would stay pinned to ONLINE, suppressing the broadcast.
        changed, payload = presence.mark_visibility(self.alice.id, "live_tab", False)

        self.assertTrue(changed)
        self.assertEqual(payload["status"], "away")
        # And the zombie should be gone from the index.
        self.assertNotIn("zombie_tab", self.fake.zrange(index_key, 0, -1))

    def test_heartbeat_preserves_status_and_keeps_connection_live(self):
        presence.mark_online(self.alice.id, "ch_a")
        presence.mark_visibility(self.alice.id, "ch_a", False)
        self.assertEqual(presence.get_status(self.alice.id)["status"], "away")

        presence.heartbeat(self.alice.id, "ch_a")

        # Status is unchanged after a heartbeat — heartbeats are pure
        # TTL refreshes, never state transitions.
        self.assertEqual(presence.get_status(self.alice.id)["status"], "away")
        # And the connection is still tracked in the index.
        self.assertIn(
            "ch_a", self.fake.zrange(presence._conns_index_key(self.alice.id), 0, -1)
        )

    def test_heartbeat_on_dead_channel_is_noop(self):
        # No mark_online for this channel — heartbeat shouldn't resurrect it.
        presence.heartbeat(self.alice.id, "never_connected")
        self.assertEqual(
            self.fake.zrange(presence._conns_index_key(self.alice.id), 0, -1), []
        )

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
