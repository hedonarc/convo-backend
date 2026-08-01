from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from apps.conversations.models import Conversation, Participant
from config.asgi import application

User = get_user_model()


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
            communicator = WebsocketCommunicator(
                application,
                f"/ws/conversations/{self.conversation.id}/?token={self.token}",
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
            communicator = WebsocketCommunicator(
                application,
                f"/ws/conversations/{self.conversation.id}/",
            )
            frame = await self._close_frame(communicator)

            self.assertEqual(frame["type"], "websocket.close")
            self.assertEqual(frame["code"], 4001)

        async_to_sync(_test)()

    def test_invalid_token_closes_with_4002(self):
        """An invalid token reaches the client as 4002 so it can try a refresh."""

        async def _test():
            communicator = WebsocketCommunicator(
                application,
                f"/ws/conversations/{self.conversation.id}/?token=invalid_token",
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
        outsider_token = str(AccessToken.for_user(outsider))

        async def _test():
            communicator = WebsocketCommunicator(
                application,
                f"/ws/conversations/{self.conversation.id}/?token={outsider_token}",
            )
            frame = await self._close_frame(communicator)

            self.assertEqual(frame["type"], "websocket.close")
            self.assertEqual(frame["code"], 4003)

        async_to_sync(_test)()

    def test_user_socket_missing_token_closes_with_4001(self):
        """The per-user socket rejects through the same path."""

        async def _test():
            communicator = WebsocketCommunicator(application, "/ws/user/")
            frame = await self._close_frame(communicator)

            self.assertEqual(frame["type"], "websocket.close")
            self.assertEqual(frame["code"], 4001)

        async_to_sync(_test)()
