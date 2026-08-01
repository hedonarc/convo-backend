from django.test import SimpleTestCase, override_settings

from apps.conversations.services import redis_store


class RedisStoreSeamTests(SimpleTestCase):
    def setUp(self):
        redis_store.reset()
        self.addCleanup(redis_store.reset)

    def test_test_settings_select_the_fake_adapter(self):
        self.assertEqual(
            redis_store.client().__class__.__module__.split(".")[0], "fakeredis"
        )

    def test_client_is_built_once(self):
        self.assertIs(redis_store.client(), redis_store.client())

    def test_changing_the_adapter_rebuilds_the_client(self):
        """Overriding settings in a test must not leave a stale connection."""
        first = redis_store.client()
        with override_settings(REDIS_CLIENT=redis_store.__name__ + ".fake_client"):
            self.assertIsNot(redis_store.client(), first)

    def test_the_fake_adapter_speaks_redis(self):
        client = redis_store.client()
        client.set("presence:probe", "online", ex=60)

        self.assertEqual(client.get("presence:probe"), "online")
        self.assertGreater(client.ttl("presence:probe"), 0)


@override_settings(REDIS_URL="redis://example.invalid:6379/2")
class RealClientTests(SimpleTestCase):
    def setUp(self):
        redis_store.reset()
        self.addCleanup(redis_store.reset)

    def test_real_client_is_built_from_the_url_setting(self):
        """One setting drives the presence store; the channel layer reads it too."""
        pool = redis_store.real_client().connection_pool

        self.assertEqual(pool.connection_kwargs["host"], "example.invalid")
        self.assertEqual(pool.connection_kwargs["db"], 2)
