import asyncio

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps.conversations.models import Conversation, Message, Participant
from apps.conversations.services import fanout
from apps.conversations.services.message_service import post_message

User = get_user_model()

IN_MEMORY_LAYER = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


@override_settings(CHANNEL_LAYERS=IN_MEMORY_LAYER)
class PostMessageTests(TransactionTestCase):
    def setUp(self):
        self.sender = User.objects.create_user(
            username="sender", email="sender@example.com", password="password"
        )
        self.peer = User.objects.create_user(
            username="peer", email="peer@example.com", password="password"
        )
        self.conversation = Conversation.objects.create(created_by=self.sender)
        Participant.objects.create(user=self.sender, conversation=self.conversation)
        Participant.objects.create(user=self.peer, conversation=self.conversation)

    async def _listen(self, group):
        await get_channel_layer().group_add(group, group)

    async def _next(self, group, timeout=2):
        return await asyncio.wait_for(get_channel_layer().receive(group), timeout)

    async def _nothing_on(self, group, timeout=0.3):
        try:
            frame = await asyncio.wait_for(get_channel_layer().receive(group), timeout)
        except TimeoutError:
            return True
        raise AssertionError(f"unexpected frame on {group}: {frame}")

    def test_persists_and_points_the_conversation_at_the_message(self):
        message = post_message(self.conversation, self.sender, "hello")
        self.conversation.refresh_from_db()

        self.assertEqual(message.content, "hello")
        self.assertEqual(self.conversation.last_message_id, message.id)

    def test_announces_to_the_conversation_room(self):
        room = fanout.conversation_group(self.conversation.id)

        async def _test():
            await self._listen(room)
            await database_sync_to_async(post_message)(
                self.conversation, self.sender, "hello"
            )
            frame = await self._next(room)
            self.assertEqual(frame["type"], fanout.NEW_MESSAGE)
            self.assertEqual(frame["message"]["content"], "hello")

        async_to_sync(_test)()

    def test_announces_to_every_participants_sidebar(self):
        peer_group = fanout.user_group(self.peer.id)

        async def _test():
            await self._listen(peer_group)
            await database_sync_to_async(post_message)(
                self.conversation, self.sender, "hello"
            )
            frame = await self._next(peer_group)
            self.assertEqual(frame["type"], fanout.CONVERSATION_UPDATED)

        async_to_sync(_test)()

    def test_a_rolled_back_send_announces_nothing(self):
        """The point of the transaction: no message, so nobody hears about one."""
        room = fanout.conversation_group(self.conversation.id)

        def _fails_after_posting():
            with transaction.atomic():
                post_message(self.conversation, self.sender, "doomed")
                raise RuntimeError("simulated failure")

        async def _test():
            await self._listen(room)
            with self.assertRaises(RuntimeError):
                await database_sync_to_async(_fails_after_posting)()
            self.assertTrue(await self._nothing_on(room))

        async_to_sync(_test)()
        self.assertEqual(Message.objects.count(), 0)

    def test_rest_send_reaches_the_conversation_room(self):
        """Regression: the REST path used to skip the new-message fanout."""
        room = fanout.conversation_group(self.conversation.id)

        async def _test():
            await self._listen(room)

            def _post():
                client = APIClient()
                client.force_authenticate(user=self.sender)
                return client.post(
                    f"/api/conversations/{self.conversation.id}/messages/",
                    {"content": "over http"},
                    format="json",
                )

            response = await database_sync_to_async(_post)()
            self.assertEqual(response.status_code, 201)

            frame = await self._next(room)
            self.assertEqual(frame["type"], fanout.NEW_MESSAGE)
            self.assertEqual(frame["message"]["content"], "over http")

        async_to_sync(_test)()
