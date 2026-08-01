from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from apps.conversations.models import Conversation, Participant
from config.asgi import application

AUTH_COOKIE = settings.SIMPLE_JWT["AUTH_COOKIE"]


def socket(path, user=None, token=None):
    """A communicator authenticated the way a browser is — via the cookie."""
    headers = []
    if user is not None:
        token = str(AccessToken.for_user(user))
    if token is not None:
        headers.append((b"cookie", f"{AUTH_COOKIE}={token}".encode()))
    return WebsocketCommunicator(application, path, headers=headers)


User = get_user_model()

IN_MEMORY_LAYER = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


class WebSocketAuthTests(TransactionTestCase):
    async def _close_frame(self, communicator):
        """Connect and return the close frame the client ends up with.

        A rejection is delivered as accept-then-close so the code survives the
        handshake, which means `connect()` reports success and the real verdict
        arrives in the following frame.
        """
        connected, _ = await communicator.connect()
        self.assertTrue(
            connected, "handshake must be accepted for a close code to reach the client"
        )
        return await communicator.receive_output()

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password")
        self.token = str(AccessToken.for_user(self.user))
        self.conversation = Conversation.objects.create(created_by=self.user)
        Participant.objects.create(user=self.user, conversation=self.conversation)

    def test_connect_authenticated(self):
        """A valid token opens the socket and keeps it open."""

        async def _test():
            communicator = socket(
                f"/ws/conversations/{self.conversation.id}/", self.user
            )
            connected, _ = await communicator.connect()

            self.assertTrue(connected)
            self.assertEqual(communicator.scope["user"].username, "testuser")
            self.assertTrue(await communicator.receive_nothing())

            await communicator.disconnect()

        async_to_sync(_test)()

    def test_missing_token_closes_with_4001(self):
        """A missing token reaches the client as 4001, not a 1006 handshake failure."""

        async def _test():
            communicator = socket(f"/ws/conversations/{self.conversation.id}/")
            frame = await self._close_frame(communicator)

            self.assertEqual(frame["type"], "websocket.close")
            self.assertEqual(frame["code"], 4001)

        async_to_sync(_test)()

    def test_invalid_token_closes_with_4002(self):
        """An invalid token reaches the client as 4002 so it can try a refresh."""

        async def _test():
            communicator = socket(
                f"/ws/conversations/{self.conversation.id}/", token="invalid_token"
            )
            frame = await self._close_frame(communicator)

            self.assertEqual(frame["type"], "websocket.close")
            self.assertEqual(frame["code"], 4002)

        async_to_sync(_test)()

    def test_non_participant_closes_with_4003(self):
        """Authorization failures are distinguishable from auth failures."""
        outsider = User.objects.create_user(
            username="outsider", email="outsider@example.com", password="password"
        )

        async def _test():
            communicator = socket(
                f"/ws/conversations/{self.conversation.id}/", outsider
            )
            frame = await self._close_frame(communicator)

            self.assertEqual(frame["type"], "websocket.close")
            self.assertEqual(frame["code"], 4003)

        async_to_sync(_test)()

    def test_user_socket_missing_token_closes_with_4001(self):
        """The per-user socket rejects through the same path."""

        async def _test():
            communicator = socket("/ws/user/")
            frame = await self._close_frame(communicator)

            self.assertEqual(frame["type"], "websocket.close")
            self.assertEqual(frame["code"], 4001)

        async_to_sync(_test)()

    def test_a_token_in_the_query_string_is_ignored(self):
        """Tokens in URLs end up in proxy and access logs, so only the cookie counts."""

        async def _test():
            communicator = WebsocketCommunicator(
                application,
                f"/ws/conversations/{self.conversation.id}/?token={self.token}",
            )
            frame = await self._close_frame(communicator)

            self.assertEqual(frame["type"], "websocket.close")
            self.assertEqual(frame["code"], 4001)

        async_to_sync(_test)()


@override_settings(CHANNEL_LAYERS=IN_MEMORY_LAYER)
class DeliveryReceiptFlowTests(TransactionTestCase):
    """End-to-end cover for the chain both consumers now route through."""

    def setUp(self):
        self.sender = User.objects.create_user(
            username="sender", email="sender@example.com", password="password"
        )
        self.recipient = User.objects.create_user(
            username="recipient", email="recipient@example.com", password="password"
        )
        self.conversation = Conversation.objects.create(created_by=self.sender)
        self.sender_participant = Participant.objects.create(
            user=self.sender, conversation=self.conversation
        )
        self.recipient_participant = Participant.objects.create(
            user=self.recipient, conversation=self.conversation
        )

    async def _connect(self, path, user):
        communicator = socket(path, user)
        await communicator.connect()
        return communicator

    async def _first_frame(self, communicator, frame_type, limit=6):
        """Return the first frame of *frame_type*, or None within *limit* frames."""
        for _ in range(limit):
            if await communicator.receive_nothing(timeout=0.3):
                return None
            frame = await communicator.receive_json_from()
            if frame["type"] == frame_type:
                return frame
        return None

    async def _receipt_seen_by_sender(self, recipient_path):
        """Send a message and report the delivery receipt the sender gets back.

        Disconnects unconditionally — a communicator left open by a failed
        assertion holds a database connection and hangs teardown instead of
        letting the test fail.
        """
        sender_socket = await self._connect(
            f"/ws/conversations/{self.conversation.id}/", self.sender
        )
        recipient_socket = await self._connect(recipient_path, self.recipient)
        try:
            await sender_socket.send_json_to(
                {"action": "send_message", "data": {"content": "hello"}}
            )
            return await self._first_frame(sender_socket, "delivered_receipt")
        finally:
            await sender_socket.disconnect()
            await recipient_socket.disconnect()

    def _delivered_pointer(self):
        self.recipient_participant.refresh_from_db()
        return self.recipient_participant.last_delivered_message_id

    def test_peer_in_the_room_reports_delivery_to_the_sender(self):
        receipt = async_to_sync(self._receipt_seen_by_sender)(
            f"/ws/conversations/{self.conversation.id}/"
        )

        self.assertIsNotNone(receipt, "sender never saw the delivery receipt")
        self.assertEqual(receipt["data"]["user_id"], self.recipient.id)
        self.assertIsNotNone(self._delivered_pointer())

    def test_peer_outside_the_room_still_records_delivery(self):
        """The recipient is only on the per-user socket, viewing another chat."""
        receipt = async_to_sync(self._receipt_seen_by_sender)("/ws/user/")

        self.assertIsNotNone(receipt, "sender never saw the delivery receipt")
        self.assertEqual(receipt["data"]["user_id"], self.recipient.id)
        self.assertIsNotNone(self._delivered_pointer())
