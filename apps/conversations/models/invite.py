import secrets

from django.conf import settings
from django.db import models


class ConversationInvite(models.Model):
    email = models.EmailField()
    token = models.CharField(max_length=64, unique=True, db_index=True)
    conversation = models.OneToOneField(
        "conversations.Conversation",
        on_delete=models.CASCADE,
        related_name="invitation",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_invites",
    )
    is_accepted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Number of times the invite email was *successfully* dispatched. Starts
    # at 0; the view bumps it to 1 inside the same transaction that wraps
    # send_mail, so a row only ever exists with count >= 1. Reminder sends
    # bump it further — also only on a successful SMTP exchange.
    invite_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("email", "conversation")

    def __str__(self):
        return f"Invite to {self.email} for {self.conversation.id}"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    @classmethod
    def get_pending(cls, *, email: str, created_by) -> "ConversationInvite | None":
        """
        Return an unaccepted invite for this sender+email pair, or None.
        Email is normalised (stripped + lowercased) before the lookup so
        'User@Example.com ' and 'user@example.com' resolve to the same record.
        """
        return (
            cls.objects.filter(
                email=email,  # already normalised by the caller
                created_by=created_by,
                is_accepted=False,
            )
            .select_related("conversation")
            .first()
        )
