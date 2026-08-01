import asyncio

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings

from apps.conversations.models import Conversation, Message, Participant
from apps.conversations.services import fanout
from apps.conversations.services.conversation_service import (
    conversation_for_participant,
)
from apps.conversations.services.read_receipts import record_read

User = get_user_model()

IN_MEMORY_LAYER = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


@override_settings(CHANNEL_LAYERS=IN_MEMORY_LAYER)
class RecordReadTests(TransactionTestCase):
    def setUp(self):
        self.sender = User.objects.create_user(
            username="sender", email="sender@example.com", password="password"
        )
        self.reader = User.objects.create_user(
            username="reader", email="reader@example.com", password="password"
        )
        self.conversation = Conversation.objects.create(created_by=self.sender)
        Participant.objects.create(user=self.sender, conversation=self.conversation)
        self.participant = Participant.objects.create(
            user=self.reader, conversation=self.conversation
        )
        self.message = Message.objects.create(
            conversation=self.conversation, sender=self.sender, content="hi"
        )

        self.elsewhere = Conversation.objects.create(created_by=self.sender)
        Participant.objects.create(user=self.sender, conversation=self.elsewhere)
        self.other_message = Message.objects.create(
            conversation=self.elsewhere, sender=self.sender, content="private"
        )

    def _read(self, message_id):
        return async_to_sync(record_read)(self.reader, self.conversation, message_id)

    def _pointer(self):
        self.participant.refresh_from_db()
        return self.participant.last_read_message_id

    def test_advances_the_pointer(self):
        self.assertTrue(self._read(self.message.id))
        self.assertEqual(self._pointer(), self.message.id)

    def test_refuses_a_message_from_another_conversation(self):
        """A client must not be able to mark messages it cannot see as read."""
        self.assertFalse(self._read(self.other_message.id))
        self.assertIsNone(self._pointer())

    def test_refuses_a_message_that_does_not_exist(self):
        self.assertFalse(self._read(999_999))
        self.assertIsNone(self._pointer())

    def test_refuses_a_soft_deleted_message(self):
        self.message.is_deleted = True
        self.message.save(update_fields=["is_deleted"])

        self.assertFalse(self._read(self.message.id))
        self.assertIsNone(self._pointer())

    def test_announces_the_receipt_to_the_conversation_room(self):
        room = fanout.conversation_group(self.conversation.id)

        async def _test():
            layer = get_channel_layer()
            await layer.group_add(room, "listener")

            await record_read(self.reader, self.conversation, self.message.id)

            frame = await asyncio.wait_for(layer.receive("listener"), timeout=2)
            self.assertEqual(frame["type"], fanout.READ_RECEIPT)
            self.assertEqual(frame["user_id"], self.reader.id)
            self.assertEqual(frame["message_id"], self.message.id)

        async_to_sync(_test)()

    def test_announces_a_fresh_snapshot_for_the_unread_dot(self):
        async def _test():
            layer = get_channel_layer()
            await layer.group_add(fanout.user_group(self.sender.id), "sender-tab")

            await record_read(self.reader, self.conversation, self.message.id)

            frame = await asyncio.wait_for(layer.receive("sender-tab"), timeout=2)
            self.assertEqual(frame["type"], fanout.CONVERSATION_UPDATED)
            receipts = frame["conversation"]["read_receipts"]
            self.assertEqual(receipts[str(self.reader.id)], self.message.id)

        async_to_sync(_test)()


class ConversationForParticipantTests(TransactionTestCase):
    def setUp(self):
        self.member = User.objects.create_user(
            username="member", email="member@example.com", password="password"
        )
        self.outsider = User.objects.create_user(
            username="outsider", email="outsider@example.com", password="password"
        )
        self.conversation = Conversation.objects.create(created_by=self.member)
        Participant.objects.create(user=self.member, conversation=self.conversation)

    def test_returns_the_conversation_for_a_participant(self):
        found = conversation_for_participant(self.member, self.conversation.id)
        self.assertEqual(found, self.conversation)

    def test_returns_none_for_an_outsider(self):
        self.assertIsNone(
            conversation_for_participant(self.outsider, self.conversation.id)
        )

    def test_returns_none_for_a_missing_conversation(self):
        self.assertIsNone(conversation_for_participant(self.member, 999_999))
