# Backend API

API usage and endpoint references for backend services.

## Access

- Admin: `http://127.0.0.1:8000/admin/`
- Profiling: `http://127.0.0.1:8000/silk/`

## Auth Endpoints

- `POST /api/register/`
- `POST /api/login/`

## WebSocket Endpoints

Real-time messaging is handled via WebSockets.

### Conversations
- **URL:** `ws://127.0.0.1:8000/ws/conversations/<conversation_id>/`
- **Authentication:** the `access_token` httpOnly cookie, which the browser sends on the handshake automatically. Nothing else is accepted — a token in the query string would be recorded by every proxy and access log in the path.
- **Rejection codes:** `4001` no token, `4002` invalid or expired token, `4003` not a participant. The handshake is accepted before the close so the code reaches the browser; a pre-accept close is reported as `1006` with the code discarded.
- **Description:** Connect to this endpoint to receive and send messages in real-time for a specific conversation.

#### Protocol
The WebSocket connection uses a JSON-based action/event routing system.

**Actions (Client → Server):**
- `send_message`: Post a new message.
- `typing`: Signal typing status.
- `read`: Mark a message as read.

**Events (Server → Client):**
- `new_message`: Incoming message notification.
- `typing`: Typing status update from others.
- `read_receipt`: Read receipt from others.
- `error`: Error messages.

For full payload schemas, see [Shared API Contracts](./api-contracts.md#websocket-message-format).
