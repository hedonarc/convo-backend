from django.test import SimpleTestCase

from apps.conversations.consumers import ConversationConsumer, UserConsumer
from apps.conversations.services import fanout


class EventTypesResolveToHandlersTests(SimpleTestCase):
    """The channel layer dispatches an event by calling the method it names.

    Nothing checks that at import time, so a renamed handler stops receiving
    without any error. These tests are that check.
    """

    def test_conversation_events_have_handlers(self):
        for event_type in fanout.CONVERSATION_EVENTS:
            with self.subTest(event_type=event_type):
                self.assertTrue(
                    callable(getattr(ConversationConsumer, event_type, None)),
                    f"ConversationConsumer has no handler named {event_type!r}",
                )

    def test_user_events_have_handlers(self):
        for event_type in fanout.USER_EVENTS:
            with self.subTest(event_type=event_type):
                self.assertTrue(
                    callable(getattr(UserConsumer, event_type, None)),
                    f"UserConsumer has no handler named {event_type!r}",
                )

    def test_every_declared_event_is_routed(self):
        """A new constant that nobody classified would never be sent."""
        declared = {
            value
            for name, value in vars(fanout).items()
            if name.isupper() and isinstance(value, str)
        }
        self.assertEqual(declared, fanout.CONVERSATION_EVENTS | fanout.USER_EVENTS)


class GroupNameTests(SimpleTestCase):
    def test_conversation_group_is_stable(self):
        self.assertEqual(fanout.conversation_group(7), "conversation_7")

    def test_user_group_is_stable(self):
        self.assertEqual(fanout.user_group(7), "user_7")

    def test_groups_do_not_collide(self):
        self.assertNotEqual(fanout.conversation_group(7), fanout.user_group(7))
