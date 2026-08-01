import asyncio

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings

from apps.conversations.models import Conversation, Message, Participant
from apps.conversations.services.delivery import is_peer_message, record_delivery

User = get_user_model()

IN_MEMORY_LAYER = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


async def receive_soon(layer, channel):
    """Read one frame, failing rather than blocking forever when none arrives."""
    return await asyncio.wait_for(layer.receive(channel), timeout=2)


class IsPeerMessageTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="reader", email="reader@example.com", password="password"
        )

    def test_own_message_is_not_a_delivery(self):
        self.assertFalse(is_peer_message({"sender": self.user.id}, self.user))

    def test_peer_message_is_a_delivery(self):
        self.assertTrue(is_peer_message({"sender": self.user.id + 1}, self.user))

    def test_deleted_message_is_not_a_delivery(self):
        message = {"sender": self.user.id + 1, "is_deleted": True}
        self.assertFalse(is_peer_message(message, self.user))

    def test_missing_message_is_not_a_delivery(self):
        self.assertFalse(is_peer_message(None, self.user))


@override_settings(CHANNEL_LAYERS=IN_MEMORY_LAYER)
class RecordDeliveryTests(TransactionTestCase):
    def setUp(self):
        self.sender = User.objects.create_user(
            username="sender", email="sender@example.com", password="password"
        )
        self.recipient = User.objects.create_user(
            username="recipient", email="recipient@example.com", password="password"
        )
        self.outsider = User.objects.create_user(
            username="outsider", email="outsider@example.com", password="password"
        )
        self.conversation = Conversation.objects.create(created_by=self.sender)
        Participant.objects.create(user=self.sender, conversation=self.conversation)
        self.participant = Participant.objects.create(
            user=self.recipient, conversation=self.conversation
        )
        self.message = Message.objects.create(
            conversation=self.conversation, sender=self.sender, content="hi"
        )
        self.conversation.last_message = self.message
        self.conversation.save(update_fields=["last_message", "updated_at"])

    def _record(self, user, message_id):
        return async_to_sync(record_delivery)(user, self.conversation.id, message_id)

    def _pointer(self):
        self.participant.refresh_from_db()
        return self.participant.last_delivered_message_id

    def test_advances_the_pointer(self):
        self.assertTrue(self._record(self.recipient, self.message.id))
        self.assertEqual(self._pointer(), self.message.id)

    def test_repeat_delivery_is_a_no_op(self):
        self._record(self.recipient, self.message.id)

        self.assertFalse(self._record(self.recipient, self.message.id))
        self.assertEqual(self._pointer(), self.message.id)

    def test_pointer_never_moves_backwards(self):
        newer = Message.objects.create(
            conversation=self.conversation, sender=self.sender, content="later"
        )
        self._record(self.recipient, newer.id)

        self.assertFalse(self._record(self.recipient, self.message.id))
        self.assertEqual(self._pointer(), newer.id)

    def test_non_participant_records_nothing(self):
        self.assertFalse(self._record(self.outsider, self.message.id))
        self.assertIsNone(self._pointer())

    def test_announces_the_receipt_to_the_conversation_room(self):
        async def _test():
            layer = get_channel_layer()
            await layer.group_add(f"conversation_{self.conversation.id}", "listener")

            await record_delivery(self.recipient, self.conversation.id, self.message.id)

            frame = await receive_soon(layer, "listener")
            self.assertEqual(frame["type"], "delivered_event")
            self.assertEqual(frame["user_id"], self.recipient.id)
            self.assertEqual(frame["message_id"], self.message.id)

        async_to_sync(_test)()

    def test_announces_a_fresh_snapshot_to_every_participant(self):
        async def _test():
            layer = get_channel_layer()
            await layer.group_add(f"user_{self.sender.id}", "sender-tab")

            await record_delivery(self.recipient, self.conversation.id, self.message.id)

            frame = await receive_soon(layer, "sender-tab")
            self.assertEqual(frame["type"], "conversation_updated_event")
            receipts = frame["conversation"]["delivery_receipts"]
            self.assertEqual(receipts[str(self.recipient.id)], self.message.id)

        async_to_sync(_test)()
