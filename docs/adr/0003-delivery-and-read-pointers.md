# 3. Delivery and read pointers advance in one place each

Date: 2026-08-02
Status: Accepted

## Context

A message reaching someone can arrive on either of two sockets, depending on
what they happen to be looking at:

- the **room group** (`conversation_<id>`) if they have that chat open
- the **per-user channel** (`user_<id>`) if they are reading a different one

Both must advance their delivery pointer, or the sender sits on a single tick
whenever the recipient is elsewhere in the app.

The rule was therefore implemented twice, once in each consumer. The two
copies had subtly different guards, each fanned out its own receipt, and
`UserConsumer` reached into `conversation_<id>` — a group the other consumer
owns. Each carried a comment arguing that the pair could not recurse; between
them that argument ran to 22 lines, and it was the only thing standing between
the design and an infinite loop.

## Decision

One module owns each pointer:

- `services/delivery.py` — `record_delivery(user, conversation_id, message_id)`
- `services/read_receipts.py` — `record_read(user, conversation, message_id)`

Announcing is **part of** recording, not a separate step a caller can forget.
Both consumers call the module and forward its result.

Termination is not an argument spread across docstrings. It is one monotonic
`UPDATE`:

```python
Participant.objects.filter(...)
    .exclude(last_delivered_message_id__gte=message_id)
    .update(last_delivered_message_id=message_id)
```

A pointer already at or past the message updates no rows, so the second pass
moves nothing and stops. The database enforces it, so concurrent sockets
racing on the same pointer are safe too.

## Consequences

- The rule is readable in one function, and testable without a
  `WebsocketCommunicator` or a channel layer.
- Adding a third delivery trigger — a push notification, say — means one call,
  not a third copy of the invariant.
- `record_delivery` returning "the pointer moved" doubles as "a fresher
  snapshot is already on the wire", which is how `UserConsumer` knows to drop
  the stale one it was about to send.

## Alternatives considered

**Only advance delivery from the room group.** Simpler, one call site. Wrong:
recipients reading another conversation would never be marked delivered, which
is the bug that produced the duplicate implementation in the first place.

**A single `record_receipt(kind=...)`.** The two pointers have the same shape,
so this is tempting. Rejected because they differ where it matters: delivery
is inferred by the server from arrival, reading is asserted by the client and
must be validated against the conversation. Merging them would put a
`kind` branch through the middle of both rules.
