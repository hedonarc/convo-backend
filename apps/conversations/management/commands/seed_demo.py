"""Populate a local database with a chat history worth looking at.

An empty Convo tells you nothing: no unread dot, no ticks, no ordering, no
scrollback. This fills it with a cast and a set of conversations chosen to
put every visible state on screen at once — read, delivered, sent, unread,
edited, deleted, empty.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.conversations.models import Conversation, Message, Participant
from apps.conversations.services.conversation_service import (
    generate_conversation_key,
)

User = get_user_model()

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo12345"  # noqa: S105 — local-only fixture, never shipped

# Marks accounts this command owns, so --fresh knows what it may delete.
DEMO_EMAIL_DOMAIN = "convo.demo"


@dataclass
class Peer:
    username: str
    first_name: str
    last_name: str


@dataclass
class Line:
    """One message. `mine` is from the account you log in as."""

    mine: bool
    text: str
    minutes_ago: int
    edited: bool = False
    deleted: bool = False


def me(text: str, minutes_ago: int, **kwargs) -> Line:
    return Line(True, text, minutes_ago, **kwargs)


def them(text: str, minutes_ago: int, **kwargs) -> Line:
    return Line(False, text, minutes_ago, **kwargs)


@dataclass
class Thread:
    peer: str
    lines: list[Line] = field(default_factory=list)
    # How far each side has got. "read" shows double ticks, "delivered" a
    # grey pair, "sent" a single tick, "unread" leaves the dot on the sidebar.
    state: str = "read"


PEERS = [
    Peer("maya", "Maya", "Chen"),
    Peer("tomas", "Tomás", "Ferreira"),
    Peer("priya", "Priya", "Nair"),
    Peer("dan", "Dan", "Kowalski"),
    Peer("saeed", "Saeed", "Al-Amin"),
]

THREADS = [
    Thread(
        peer="maya",
        state="unread",
        lines=[
            them("morning! did the presence work land?", 94),
            me("yep, went out last night", 91),
            me("recompute is a single Lua script now instead of six round trips", 91),
            them(
                "nice. so no more of that race where two tabs fought over status?", 88
            ),
            me(
                "right — Redis runs the script to completion, nothing can interleave",
                86,
            ),
            them("I'll take it for a spin after standup", 84),
            them("one thing though, the away toggle felt sticky on my second tab", 12),
            them("might just be my wifi, but worth a look", 11),
        ],
    ),
    Thread(
        peer="tomas",
        state="delivered",
        lines=[
            them("got a sec?", 320),
            me("sure", 318),
            them(
                "the sidebar isn't reordering for chats I don't have open",
                316,
            ),
            me(
                "that's the per-user channel, it fans out wherever you are",
                314,
            ),
            me("what does the network tab say, is the frame arriving?", 313),
            them("checking", 300),
            them("ok it IS arriving, so it's the client dropping it", 296),
            me("then I know where to look. thanks for narrowing it", 180),
        ],
    ),
    Thread(
        peer="priya",
        state="read",
        lines=[
            them("design review moved to 3", 1500),
            me("works for me", 1498),
            them("I put the new empty states in figma, page 4", 1495),
            me("the one with the illustration or the plain copy version?", 1490),
            them(
                "plain copy. the illustration felt heavy for something you see daily",
                1488,
            ),
            me("agreed, and it's one less asset to ship", 1485),
            me("I'll wire it up this week", 1483, edited=True),
            them("🙏", 1480),
        ],
    ),
    Thread(
        peer="dan",
        state="sent",
        lines=[
            them("are we still on for friday?", 2900),
            me("yes! same place?", 2880),
            them("same place. 7pm", 2875),
            me("perfect", 2870),
            me(
                "actually — can we do 7.30? standup ran long all week",
                45,
            ),
        ],
    ),
    Thread(
        peer="saeed",
        state="read",
        lines=[
            them("sent you the draft", 4400),
            me("reading now", 4390),
            me("this bit doesn't belong here", 4385, deleted=True),
            me("sorry, wrong thread", 4384),
            me(
                "draft looks good. the section on failure modes is the strongest part",
                4380,
            ),
            them("that was the bit I nearly cut!", 4375),
            me("glad you didn't", 4370),
        ],
    ),
]


class Command(BaseCommand):
    help = "Fill a local database with a demo cast and chat history."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fresh",
            action="store_true",
            help="Delete demo accounts and every conversation first. Local only.",
        )
        parser.add_argument(
            "--as",
            dest="as_username",
            default=DEMO_USERNAME,
            help=(
                f"Attach the conversations to this account instead of "
                f"'{DEMO_USERNAME}'. Created if missing."
            ),
        )

    def handle(self, *args, **options):
        self._refuse_outside_local()

        username = options["as_username"]
        with transaction.atomic():
            if options["fresh"]:
                self._wipe()
            me = self._account(username)
            peers = {p.username: self._peer(p) for p in PEERS}
            for thread in THREADS:
                self._build(me, peers[thread.peer], thread)
            self._empty_conversation(me, peers["saeed"])

        self._report(username)

    # ── Guards ──────────────────────────────────────────────────────────────

    def _refuse_outside_local(self):
        """Demo data must never reach a real database."""
        if not settings.DEBUG:
            raise CommandError(
                "seed_demo refuses to run with DEBUG=False. It creates accounts "
                "with known passwords and is only ever meant for local use."
            )

    # ── Building blocks ─────────────────────────────────────────────────────

    def _wipe(self):
        Message.objects.all().delete()
        Conversation.objects.all().delete()
        User.objects.filter(email__endswith=DEMO_EMAIL_DOMAIN).delete()
        self.stdout.write(self.style.WARNING("cleared conversations and demo accounts"))

    def _account(self, username):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": f"{username}@{DEMO_EMAIL_DOMAIN}",
                "first_name": "Demo",
                "last_name": "User",
            },
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=["password"])
        return user

    def _peer(self, peer: Peer):
        user, created = User.objects.get_or_create(
            username=peer.username,
            defaults={
                "email": f"{peer.username}@{DEMO_EMAIL_DOMAIN}",
                "first_name": peer.first_name,
                "last_name": peer.last_name,
            },
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=["password"])
        return user

    def _build(self, me, peer, thread: Thread):
        conversation = self._conversation(me, peer)
        mine = Participant.objects.get(user=me, conversation=conversation)
        theirs = Participant.objects.get(user=peer, conversation=conversation)

        created = [self._message(conversation, me, peer, line) for line in thread.lines]
        last = created[-1]

        # Conversation.updated_at drives sidebar order, and auto_now would
        # stamp it "now" for every thread. Set it alongside last_message.
        conversation.last_message = last
        conversation.save(update_fields=["last_message"])
        Conversation.objects.filter(pk=conversation.pk).update(
            updated_at=last.created_at, created_at=created[0].created_at
        )

        self._pointers(thread, created, me, mine, theirs)

    def _conversation(self, me, peer):
        key = generate_conversation_key(me, peer)
        conversation, created = Conversation.objects.get_or_create(
            conversation_key=key, defaults={"created_by": me}
        )
        if created:
            Participant.objects.bulk_create(
                [
                    Participant(user=me, conversation=conversation),
                    Participant(user=peer, conversation=conversation),
                ]
            )
        return conversation

    def _message(self, conversation, me, peer, line: Line):
        sent_at = timezone.now() - timedelta(minutes=line.minutes_ago)
        message = Message.objects.create(
            conversation=conversation,
            sender=me if line.mine else peer,
            content="" if line.deleted else line.text,
            prev_content=line.text if line.deleted or line.edited else "",
            is_deleted=line.deleted,
            deleted_at=sent_at if line.deleted else None,
            edited_at=sent_at + timedelta(minutes=1) if line.edited else None,
        )
        # created_at is auto_now_add, so it can only be set after the insert.
        Message.objects.filter(pk=message.pk).update(
            created_at=sent_at, updated_at=sent_at
        )
        message.refresh_from_db()
        return message

    def _pointers(self, thread: Thread, created, me, mine, theirs):
        """Set how far each side has read, which is what draws the ticks."""
        last_id = created[-1].id
        my_last = max((m.id for m in created if m.sender_id == me.id), default=None)
        # The newest message the peer sent, so "unread" can sit just behind it.
        their_first_unread = next(
            (m.id for m in created if m.sender_id != me.id and m.id > (my_last or 0)),
            None,
        )

        if thread.state == "unread":
            # I have not caught up; they have seen everything of mine.
            mine.last_read_message_id = (their_first_unread or last_id) - 1
            mine.last_delivered_message_id = last_id
            theirs.last_read_message_id = my_last
            theirs.last_delivered_message_id = my_last
        elif thread.state == "delivered":
            mine.last_read_message_id = last_id
            mine.last_delivered_message_id = last_id
            theirs.last_delivered_message_id = my_last
            theirs.last_read_message_id = (my_last or last_id) - 1
        elif thread.state == "sent":
            mine.last_read_message_id = last_id
            mine.last_delivered_message_id = last_id
            theirs.last_read_message_id = (my_last or last_id) - 1
            theirs.last_delivered_message_id = (my_last or last_id) - 1
        else:  # read
            mine.last_read_message_id = last_id
            mine.last_delivered_message_id = last_id
            theirs.last_read_message_id = last_id
            theirs.last_delivered_message_id = last_id

        mine.save(update_fields=["last_read_message_id", "last_delivered_message_id"])
        theirs.save(update_fields=["last_read_message_id", "last_delivered_message_id"])

    def _empty_conversation(self, me, peer):
        """One thread with no messages, so the empty state is reachable.

        Prefers the superuser, who already exists in most local setups, but
        falls back to a demo peer — on a fresh clone with no superuser this
        state would otherwise be silently missing.

        Aged deliberately: a brand new conversation would sort to the top of
        the sidebar and make the app look empty at a glance.
        """
        other = User.objects.filter(is_superuser=True).exclude(pk=me.pk).first()
        if other is None:
            other = self._peer(Peer("nadia", "Nadia", "Haddad"))
        conversation = self._conversation(me, other)
        stale = timezone.now() - timedelta(days=6)
        Conversation.objects.filter(pk=conversation.pk).update(
            updated_at=stale, created_at=stale
        )

    # ── Output ──────────────────────────────────────────────────────────────

    def _report(self, username):
        self.stdout.write(
            self.style.SUCCESS(
                f"\nSeeded {len(THREADS)} conversations for '{username}'.\n\n"
                f"  log in with   {username} / {DEMO_PASSWORD}\n"
                f"  peers         {', '.join(p.username for p in PEERS)} "
                f"(same password)\n\n"
                "Open two browsers with different accounts to watch typing, "
                "presence and receipts move in real time.\n"
            )
        )
