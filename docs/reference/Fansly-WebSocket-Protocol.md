---
status: current
---

# Fansly WebSocket Protocol Reference

Reverse-engineered from browser DevTools captures and `main.js` EventService.
All observations verified against live traffic on 2026-04-15.

## Endpoints

| Endpoint                       | Purpose                                         |
| ------------------------------ | ----------------------------------------------- |
| `wss://wsv3.fansly.com/?v=3`   | Main event bus — session, pings, service events |
| `wss://chatws.fansly.com/?v=3` | Livestream chat — chat room messages            |

Both share the same authentication handshake. The session response from `chatws`
includes `ip` and `metadata` (user agent) fields that `wsv3` returns as null.

## Authentication Handshake

### Client Request (t=1)

```json
{ "t": 1, "d": "{\"token\": \"<BASE64_TOKEN>\", \"v\": 3}" }
```

The token is base64-encoded and decodes to `<sessionId>:1:2:<hex>`.

### Server Response (t=1)

```json
{
  "t": 1,
  "d": "{\"session\": {
    \"id\": \"<sessionId>\",
    \"token\": \"<refreshed_token>\",
    \"accountId\": \"<accountId>\",
    \"deviceId\": null,
    \"status\": 2,
    \"ip\": null,
    \"lastUsed\": 1776222201000,
    \"twofa_at\": null,
    \"metadata\": null,
    \"createdAt\": 1763047608000,
    \"checkToken\": null,
    \"websocketSessionId\": \"<wsSessionId>\"
  }}"
}
```

Notable: The response `token` is different (refreshed) from what was sent.
The `chatws` endpoint populates `ip` and `metadata` (stringified JSON with user agent).

## Encoding

All messages use double-JSON encoding: `{"t": <int>, "d": "<stringified JSON>"}`.
ServiceEvents (t=10000) use triple-JSON: outer envelope -> `{serviceId, event}` -> payload.
Some payloads have quad-JSON nesting (e.g., chat message metadata).

## Message Types (top-level `t` field)

| Type  | Name         | Direction | Description                                                               |
| ----- | ------------ | --------- | ------------------------------------------------------------------------- |
| 0     | ErrorEvent   | Server    | Error with `code` field (401=unauthorized, 429=rate limited)              |
| 1     | SessionEvent | Both      | Client: auth request. Server: session verified.                           |
| 2     | PingResponse | Server    | Response to client's bare `"p"` ping                                      |
| 10000 | ServiceEvent | Server    | Real-time notifications (see Service IDs below)                           |
| 10001 | Batch        | Server    | Array of messages, recursively unpacked                                   |
| 46001 | ChatRoomJoin | Client    | Chat room subscribe request: `{chatRoomId}` (= `1000 * SVC_CHATROOM + 1`) |

### Ping Protocol

- Client sends bare string `"p"` every 20-25 seconds (randomized jitter)
- Server responds with `{"t": 2, "d": "{\"lastPing\": <ms_timestamp>}"}`
- If no response within 1.2x the ping interval, client resets connection
- These constants match `main.js`: `pingInterval_`, `pingTimeout_ = 1.2 * pingInterval_`

### Reconnection

- Exponential backoff: 1.5s base, 2x per attempt, capped at 15s
- Maximum 5 reconnection attempts
- From `main.js`: `reconnect_timeout_ = 1500`, caps at `15000`

## Service IDs (within ServiceEvent t=10000)

Complete enum from `main.js` EventService.ServiceIds:

```
 1  PostService              17  CCBillService           33  WebPushService
 2  MediaService             18  AffiliateService        34  NotesService
 3  FollowerService          19  SagaService             35  CCBillServiceNew
 4  GroupService             20  EmailService            36  TimelineStatsService
 5  MessageService           21  ReportService           37  PayopService
 6  WalletService            22  TwoFAService            38  VaultService
 7  TippingService           23  PartnerService          39  ManagementService
 8  OnlineStatusService      24  AdminService            40  EarningsStatsService
 9  NotificationService      25  EmailBotService         41  MediaStoriesService
10  ProfileService           26  InovioService           42  PollsService
11  IgnoreService            27  FraudService            43  PayoutService
12  AccountService           28  GeoLocationService      44  ContentDiscoveryService
13  ChatBotService           29  TwitchService           45  StreamingService
14  OrderService             30  ListsService            46  ChatRoomService
15  SubscriptionService      31  SettingsService         66  EmbedService
16  PaymentService           32  StoryService            67  TenorService
                                                         71  PermissionOverwriteService
1000  INTERNAL
```

Connection states: `CONNECTING=1, AUTHORIZING=2, CONNECTED=3, DISCONNECTED=4`

### Complete Event Type Schema (from main.js handleEvent methods)

### serviceId=1 — PostService

| Type | Key        | Callback              | Description                          |
| ---- | ---------- | --------------------- | ------------------------------------ |
| 1    | `post`     | _(inline handler)_    | **New post created** — injected into home/sub/list timelines |
| 2    | `like`     | `onLikeCreate`        | Post liked                           |
| 3    | `like`     | `onLikeRemove`        | Post unliked                         |
| 4    | `updates`  | `onPostUpdate`        | Post stats updated (likeCount, replyCount, tipAmount) |
| 5    | `post`     | _(inline handler)_    | Post deleted — removed from all timelines |
| 8    | `post`     | _(inline handler)_    | Post content updated — triggers re-fetch |
| 9    | `wallPost` | `onWallPostCreate`    | Post added to wall (+ queued if post not yet cached) |
| 10   | `wallPost` | `onWallPostDelete`    | Post removed from wall               |
| 101  | `wall`     | `onWallCreate`        | New wall created                     |
| 102  | `wall`     | `onWallDelete`        | Wall deleted                         |

**PostService type=1 is critical for the monitoring daemon** — new posts from followed creators arrive via WebSocket, not just polling. The inline handler adds the post to homeTimeline, homeSubscribeTimeline, and all matching listTimelines in real-time. Posts not on the default wall show a toast: "This Post won't appear in Home feeds."

### serviceId=2 — MediaService

| Type | Key     | Callback                    | Description               |
| ---- | ------- | --------------------------- | ------------------------- |
| 2    | `like`  | _(main handler)_            | Media liked               |
| 5    | `media` | `onMediaUpdate`             | Media updated (re-encoded/processed) |
| 7    | `order` | `onAccountMediaOrderCreate` | PPV media purchased       |
| 8    | `order` | _(main handler)_            | PPV bundle purchased      |
| 999  | —       | `onCacheClear`              | Cache invalidation signal |

A PPV purchase (type=7/8) is immediately followed by a wallet debit (svc=6 type=3, type=58000).

#### PPV Order Payload (type=7)

```json
{
  "type": 7,
  "order": {
    "orderId": "<order-snowflake>",
    "accountMediaId": "<media-snowflake>",
    "correlationAccountId": "<creator-accountId>",
    "accountId": "<buyer-accountId>",
    "type": 1
  }
}
```

- `accountMediaId` matches the `contentId` from any earlier `attachments[]` entry on a DM (`svc=5 type=1, attachment.contentType=1`) where the creator offered this media — so a DM-offered locked preview can be stitched to its eventual purchase without a REST roundtrip.
- `correlationAccountId` = the creator (seller). The daemon's `_handle_ppv_purchase` reads this to emit `RedownloadCreatorMedia(creator_id=...)`.
- `accountId` = the buyer (always the local user, since WS only delivers events the local account participates in).

#### PPV Correlation Chain (distinct from subscription)

Unlike subscriptions (where `subscription.historyId == wallet.transaction.correlationId` is a direct stitching key), the PPV chain does NOT publish the join key on the wire:

- `order.orderId` and `wallet.transaction.correlationId` are **different Snowflake IDs**, minted ~milliseconds apart.
- The actual join is via an intermediate payment/charge record not broadcast on the WS bus.
- For the daemon's "re-download this creator's media" reaction this doesn't matter; for any downstream code that needs to stitch a specific PPV order to its specific transaction, fetch the payment record via a REST endpoint.

### serviceId=3 — FollowerService

| Type | Key      | Callback         | Description                                    |
| ---- | -------- | ---------------- | ---------------------------------------------- |
| 2    | `follow` | _(main handler)_ | Follow — adds permission flag 2 to all content |
| 3    | `follow` | _(main handler)_ | Unfollow — removes permission flag 2           |

### serviceId=4 — GroupService

| Type | Key                            | Callback              | Description                    |
| ---- | ------------------------------ | --------------------- | ------------------------------ |
| 2    | `ackCommand`                   | _(main handler)_      | Message delivery/read receipt  |
| 3    | `like`                         | `onMessageLikeAdd`    | Message reaction added         |
| 4    | `like` / `userSettings`        | `onMessageLikeRemove` | Reaction removed / user settings changed |
| 6    | `groupUser`                    | `onGroupUserAdd`      | User added to group            |
| 7    | `groupUser`                    | `onGroupUserRemove`   | User removed from group        |
| 8    | `id`                           | `onGroupCreate`       | Group created                  |
| 9    | `groupUserSettingsChangeEvent` | _(inline handler)_    | Group user settings changed    |

`ackCommand.type` values:

- `1` = Delivered (message reached recipient's client)
- `2` = Read (recipient opened/viewed the message)

Fields: `groupId`, `messageIds[]`, `userId`, `userReadReceiptsEnabled`, `recipients[]`.

`messageIds[]` carries one or more IDs — a single ACK frame may batch
read receipts for multiple messages at once (observed: a single `type=2`
ack covering two earlier message IDs when the recipient opened the
thread after both arrived). Don't assume single-element arrays.

### serviceId=5 — MessageService

| Type | Key                   | Callback              | Description              |
| ---- | --------------------- | --------------------- | ------------------------ |
| 1    | `message`             | `onMessageCreate`     | New message received     |
| 2    | `messageAckEvent`     | _(inline handler)_    | Message ACK batch        |
| 3    | `like`                | `onMessageLikeAdd`    | Message reaction added   |
| 4    | `like`                | `onMessageLikeRemove` | Message reaction removed |
| 10   | `message`             | _(inline handler)_    | Message deleted (type=3 cascades by correlationId) |
| 22   | `typingAnnounceEvent` | `onTypingAnnounce`    | User is typing indicator |

#### New Message (type=1)

```json
{
  "type": 1,
  "message": {
    "type": 1,
    "content": "message text",
    "senderId": "<accountId>",
    "groupId": "<groupId>",
    "attachments": [
      { "contentType": 1, "contentId": "<id>", "messageId": "<id>", "pos": 0 }
    ],
    "interactions": [
      {
        "groupId": "...",
        "userId": "...",
        "readAt": 0,
        "deliveredAt": 0,
        "messageId": "..."
      }
    ],
    "inReplyTo": "<messageId>",
    "inReplyToRoot": "<messageId>",
    "inReplyToMessage": {
      /* full parent message object */
    },
    "id": "<messageId>",
    "createdAt": 1776225990.786,
    "embeds": []
  }
}
```

Note: `createdAt` is in seconds with decimal (not milliseconds like other events). The same envelope may mix representations — observed: the outer `message.createdAt` is float seconds (e.g., `1776225990.786`) while its nested `inReplyToMessage.createdAt` is int seconds (e.g., `1776222494`, no decimal). The `_parse_timestamp` validator handles both (sub-second precision is preserved into `datetime.microsecond`).

`inReplyTo` sentinel: empty string `""` means the message is standalone; a non-empty string is the parent message ID (and `inReplyToMessage` will be populated with the full parent message object as a nested dict, not just an ID). Both `inReplyTo` and `inReplyToRoot` follow this convention.

#### Message Reaction (type=3)

```json
{
  "type": 3,
  "like": {
    "accountId": "<accountId>",
    "messageId": "<messageId>",
    "type": <reaction_type>,
    "groupId": "<groupId>",
    "id": "<likeId>"
  }
}
```

Reaction type codes (from CSS asset definitions in main.js):

| `like.type` | Emoji | CSS class           |
| ----------- | ----- | ------------------- |
| 1           | ❤️    | `emoji.heart`       |
| 2           | 😂    | `emoji.cry-laugh`   |
| 3           | 😮    | `emoji.surprised`   |
| 4           | 😢    | `emoji.sad`         |
| 5           | 🔥    | `emoji.fire`        |
| 6           | 👍    | `emoji.thumbs-up`   |
| 7           | 👎    | `emoji.thumbs-down` |

#### Typing Indicator (type=22)

```json
{
  "type": 22,
  "typingAnnounceEvent": {
    "accountId": "<accountId>",
    "groupId": "<groupId>",
    "lastAnnounce": 1776291298537
  }
}
```

### serviceId=6 — WalletService

| Type | Key           | Callback                    | Description                                                      |
| ---- | ------------- | --------------------------- | ---------------------------------------------------------------- |
| 1    | `transaction` | _(parsed only)_             | Transaction created                                              |
| 2    | `wallet`      | _(main handler)_            | Balance update — uses `walletVersion` for optimistic concurrency |
| 3    | `transaction` | `onWalletTransactionUpdate` | Transaction update/completed                                     |
| 101  | —             | `onWalletsLoad`             | Wallets loaded signal                                            |

Wallet types: `1` = main wallet, `2` = earnings wallet.

`walletVersion` is checked: updates with lower version than cached are ignored.

`balance` vs `balance64` — the wallet update carries both:

- `balance` = net spendable (post-pending-debit).
- `balance64` = gross (pre-pending-debit, includes any in-flight debit).

When a purchase is in flight, `balance64 - balance` equals the amount of the pending debit and matches the paired `wallet.transaction.amount` exactly (verified empirically for a single-pending-transaction PPV: a $59.99 purchase produced `balance64 - balance = 59990` mills, equal to `transaction.amount`). Don't display `balance64` to the user as "your balance" — the user-facing number is `balance`.

Transaction type codes:

- `14001` = Wallet credit from external payment (money in)
- `58000` = Subscription / PPV purchase debit (money out). Same code for both subscription renewals AND single-item PPV media purchases; not subscription-specific.

### serviceId=7 — TippingService

| Type | Key       | Callback          | Description                                          |
| ---- | --------- | ----------------- | ---------------------------------------------------- |
| 1    | `tip`     | _(main handler)_  | Tip sent. Fields: `senderId`, `receiverId`, `amount` |
| 101  | `tipGoal` | `onTipGoalUpdate` | Tip goal progress updated                            |

### serviceId=8 — OnlineStatusService

| Type | Key      | Callback         | Description                                           |
| ---- | -------- | ---------------- | ----------------------------------------------------- |
| 1    | `status` | _(main handler)_ | Online status change. Fields: `accountId`, `statusId` |

### serviceId=9 — NotificationService

| Type | Key            | Callback                 | Description                 |
| ---- | -------------- | ------------------------ | --------------------------- |
| 1    | `notification` | `onNotificationCreate`   | Notification created        |
| 100  | —              | `onNotificationsLoaded`  | Notifications loaded signal |
| 101  | —              | `onUnacknowledgedUpdate` | Unread count changed        |
| 102  | —              | `onNotificationsUpdate`  | Notifications updated       |

Notification type codes (from notification filters in main.js):

| Code  | `serviceId * 1000 + N` | Description             |
| ----- | ---------------------- | ----------------------- |
| 1002  | Post(1) + 2            | Post liked              |
| 1003  | Post(1) + 3            | Post reply (alt)        |
| 1004  | Post(1) + 4            | Post reply              |
| 1005  | Post(1) + 5            | Post quoted             |
| 2002  | Media(2) + 2           | Media liked             |
| 2007  | Media(2) + 7           | Media purchased (PPV)   |
| 2008  | Media(2) + 8           | Media bundle purchased  |
| 3002  | Follower(3) + 2        | New follower            |
| 3003  | Follower(3) + 3        | Unfollowed              |
| 5003  | Message(5) + 3         | Message reaction        |
| 7001  | Tipping(7) + 1         | Tip received            |
| 15006 | Subscription(15) + 6   | New subscriber          |
| 15007 | Subscription(15) + 7   | Subscription expired    |
| 15011 | Subscription(15) + 11  | Promotion used          |
| 15016 | Subscription(15) + 16  | New subscriber (v2)     |
| 32007 | Story(32) + 7          | Locked text purchased   |
| 45012 | Streaming(45) + 12     | Stream ticket purchased |

#### `(9, 1)` Correlation Semantics

Every `(9, 1)` notification carries two correlation IDs that point at different things depending on the inner `notification.type`:

- **`correlationGroupId`** → the **persistent entity** affected (the "thing" the notification is about)
- **`correlationId`** → the **specific triggering record** (the "what just happened")

Empirically observed mappings (other inner types follow the same shape but specific references are unconfirmed):

| `notification.type` | `correlationGroupId` → | `correlationId` → | Paired direct event? |
|---|---|---|---|
| 5003 (Message Reaction) | `message.id` that was reacted to | `like.id` from the `(5, 3)` reaction | **Yes** — `(5, 3)` is also delivered to the local observer (group member) |
| 15011 (Promotion) | creator's `accountId` (the promoter) | `promo.id` inside `subscriptionTiers[].plans[].promos[]` (see SubscriptionService below) | **No** — `(15, 11)` is not delivered to the local observer; the notification is the only signal |

**Pairing depends on participation.** When the local observer is a *participant* in the underlying event (sender, buyer, group-member, etc.), the direct service event is fanned out to them alongside the notification. When the local observer is only a *target* of a creator-initiated action (promotion offer, etc.), only the notification arrives.

**Empirical note on the `15011` "Promotion used" table label above.** Observed in real traffic, `(9, 1)` type=15011 fires when a creator *activates or extends* a promotion offer, with `promo.uses=0` at notification time. The "used" label may be a Fansly-side misnomer or a stale revision of the protocol; the trigger semantics are closer to "promotion available."

**`notification.accountId`** is always the **recipient** of the notification feed (whose feed it lands in), not the actor of the triggering action. The actor lives on the direct event's payload when present, or has to be resolved from the persistent entity referenced by `correlationGroupId`.

**Dispatch policy:** the monitoring daemon noops all `(9, 1)` notifications. For paired inner types (5003, 2007, 2008, 3002, 15006, etc.), the direct event is authoritative. For unpaired inner types (15011 promotion, possibly 15007 expiration), the notification carries no actionable content for the downloader — the user would still have to accept the offer (which would fire a `(15, 5)` subscription event the daemon already handles).

### serviceId=15 — SubscriptionService

| Type | Key            | Callback                  | Description                       |
| ---- | -------------- | ------------------------- | --------------------------------- |
| 5    | `subscription` | `onSubscriptionUpdate`    | Subscription created or confirmed |
| 100  | `queryResult`  | `onSubscriptionsFetch`    | Subscriptions query result        |
| 101  | `giftCode`     | `onGiftCodeAdd`           | Gift code added                   |
| 102  | `subscription` | `onSubscriptionUpdateNew` | Subscription update (alternate)   |

Subscription flows through two events with the same `id`:

1. First event: `status: 2` (pending) — lean payload
2. Second event: `status: 3` (confirmed) — enriched with tier name, color, endsAt, etc.

#### Subscription Tier Entity Shape

The `subscriptionTiers` array on an `account` object (returned by REST timeline / account endpoints) is the underlying entity the SubscriptionService events reference. Shape:

```json
{
  "subscriptionTiers": [{
    "id": "<tier-snowflake>",
    "accountId": "<creator-accountId>",
    "name": "...",  "color": "#XXXXXX",  "pos": 0,  "price": <mills>,
    "subscriptionBenefits": ["..."],
    "includedTierIds": ["<lower-tier-snowflake>"],
    "plans": [{
      "id": "<plan-snowflake>",
      "status": 1,
      "billingCycle": <days>,
      "price": <mills>,
      "useAmounts": 0,
      "promos": [{
        "id": "<promo-snowflake>",
        "status": 1,
        "price": <mills, discounted>,
        "duration": <days>,
        "description": "...",
        "startsAt": <ms>,
        "endsAt": <ms>,
        "maxUses": <int>,
        "maxUsesBefore": <ms>,
        "newSubscribersOnly": 0,
        "uses": <int>
      }],
      "uses": <int>
    }]
  }]
}
```

**Hierarchy:** *tier* > *plans* (different billing cycles for the same tier) > *promos* (discounted variants of a plan).

- `(15, 5)` subscription events reference `subscriptionTier.id` and `plan.id`.
- `(9, 1)` type=15011 promotion notifications reference `promo.id` via `correlationId` (see `(9, 1)` Correlation Semantics above).
- `includedTierIds` describes tier hierarchy (a higher tier inherits lower tiers' benefits/content access); promo correlation does not traverse this hierarchy — each promo is bound to a single plan.

**Promo records are persistent slots.** The same `promo.id` is reused across successive promotional windows, with `startsAt` / `endsAt` / `price` / `description` updated each time the creator reactivates the offer. The Snowflake ID's age reflects slot-creation time (potentially years old), NOT current promotion activation. Trust `startsAt` / `endsAt` / `uses` for currentness.

### serviceId=16 — PaymentService

| Type | Key             | Callback                   | Description               |
| ---- | --------------- | -------------------------- | ------------------------- |
| 1    | `transaction`   | `onTransactionCreate`      | Payment initiated         |
| 2    | `transaction`   | `onTransactionUpdate`      | Payment confirmed/updated |
| 3    | `transaction`   | `onTransactionCreateError` | Payment error             |
| 10   | `wallet`        | `onPaymentMethodCreate`    | Payment method added      |
| 11   | `wallet`        | `onPaymentMethodUpdate`    | Payment method updated    |
| 20   | `payoutRequest` | `onPayoutRequestCreate`    | Payout request created    |
| 21   | `payoutRequest` | `onPayoutRequestUpdate`    | Payout request updated    |

Transaction type codes:

- `14000` = External card charge

### serviceId=8 — OnlineStatusService

| Type | Key      | Callback           | Description                                           |
| ---- | -------- | ------------------ | ----------------------------------------------------- |
| 1    | `status` | _(inline handler)_ | Online status change. Fields: `accountId`, `statusId` |

Updates `lastSeenAt` and `statusId` on cached account objects.

### serviceId=10 — ProfileService

| Type | Key           | Callback        | Description              |
| ---- | ------------- | --------------- | ------------------------ |
| 2    | `pinnedPost`  | `onPostPin`     | Post pinned              |
| 100  | `pinnedPost`  | `onPostPin`     | Post pinned (alternate)  |
| 101  | `pinnedPost`  | `onPostUnpin`   | Post unpinned            |
| 102  | `pinnedPosts` | `onPostsUpdate` | Pinned posts bulk update |

### serviceId=11 — IgnoreService

| Type | Key    | Callback           | Description                                                           |
| ---- | ------ | ------------------ | --------------------------------------------------------------------- |
| 1    | `data` | _(inline handler)_ | User ignored/blocked. Fields: `accountId`, `ignoredId`, `ignoreFlags` |

### serviceId=12 — AccountService

| Type | Key        | Callback                      | Description                                                   |
| ---- | ---------- | ----------------------------- | ------------------------------------------------------------- |
| 2    | `account`  | _(inline handler)_            | Account profile updated. Fields: `id`, `displayName`, `flags` |
| 100  | `accounts` | `onAccountsChange`            | Account data changed (event consumer)                         |
| 101  | `accounts` | `onAccountSuggestionsRefetch` | Suggestions need refresh                                      |
| 999  | —          | `onCacheClear`                | Cache invalidation                                            |

### serviceId=17 — CCBillService

| Type | Key           | Callback              | Description                |
| ---- | ------------- | --------------------- | -------------------------- |
| 2    | `transaction` | `onTransactionUpdate` | CCBill transaction updated |

### serviceId=26 — InovioService

| Type | Key       | Callback             | Description                |
| ---- | --------- | -------------------- | -------------------------- |
| 3    | `request` | `on3dsRequestCreate` | 3DS authentication request |

### serviceId=32 — StoryService

| Type | Key     | Callback             | Description        |
| ---- | ------- | -------------------- | ------------------ |
| 7    | `order` | `onStoryOrderCreate` | Story PPV purchase |

Note: Story bundle purchases (type=8) are handled in the main `onServiceEvent` handler, not the StoryService event consumer.

### serviceId=42 — PollsService

| Type | Key                | Callback            | Description                                     |
| ---- | ------------------ | ------------------- | ----------------------------------------------- |
| 10   | `pollVote`         | `onPollVote`        | Poll vote cast                                  |
| 20   | `pollSubscription` | `onPollSubscribe`   | Subscribed to poll updates (viewport enter)     |
| 21   | `pollSubscription` | `onPollUnsubscribe` | Unsubscribed from poll updates (viewport leave) |
| 50   | `polls`            | `onPollUpdate`      | Poll data/results updated                       |

### serviceId=44 — ContentDiscoveryService

Parsed but no specific action taken in frontend — events are consumed silently.

### serviceId=45 — StreamingService

| Type | Key      | Callback             | Description                         |
| ---- | -------- | -------------------- | ----------------------------------- |
| 10   | `stream` | `onStreamUpdate`     | Stream state changed (live/offline) |
| 11   | `stream` | `onPermissionUpdate` | Stream permission changed           |

### serviceId=46 — ChatRoomService (chatws only)

| Type | Key               | Callback            | Description                                    |
| ---- | ----------------- | ------------------- | ---------------------------------------------- |
| 4    | `chatRoom`        | _(inline handler)_  | Chat room state update                         |
| 10   | `chatRoomMessage` | `onChatRoomMessage` | New chat message                               |
| 20   | `chatRoomBan`     | _(inline handler)_  | User banned — remove messages                  |
| 30   | `chatRoomBan`     | _(inline handler)_  | Ban notification (system msg type=2)           |
| 50   | `chatRoomGoal`    | _(inline handler)_  | Tip goal created (status=1)                    |
| 51   | `chatRoomGoal`    | _(inline handler)_  | Tip goal updated/deleted                       |
| 53   | `subAlert`        | _(inline handler)_  | Subscription alert (system msg type=6)         |
| 54   | `settings`        | _(inline handler)_  | Chat room settings changed (system msg type=8) |

Chat room join flow: after auth (t=1 response), client sends `wrapRequest(46001, {chatRoomId})` to subscribe.

Chat message types (within `chatRoomMessage` or synthetic messages):

| msg type | Description                                                |
| -------- | ---------------------------------------------------------- |
| 0        | Regular text message                                       |
| 2        | Ban notification (system)                                  |
| 4        | Mod mode toggle (creator-only, `/mod` command, local only) |
| 5        | Lovense status (system, content="123")                     |
| 6        | Subscription alert (system, contains `subAlert`)           |
| 7        | Chat room status error (e.g., error code 25)               |
| 8        | Settings change (system, contains `chatRoomSettings`)      |

Tip-in-chat payload (attached to chat message send request):

```json
{
  "chatRoomId": "<id>",
  "content": "message text",
  "private": 0,
  "messageTip": {
    "receiverId": "<creatorAccountId>",
    "amount": 5000,
    "message": "message text",
    "customTargetId": "<chatRoomGoalId or null>"
  }
}
```

- `private: 1` hides the tip from other viewers
- `customTargetId` targets a specific chat room goal (user-selected or auto-highest)
- Tips between $1–$500 (`dollarsToBalance(1)` to `dollarsToBalance(500)`) trigger wallet top-up if insufficient balance

Username colors are validated with `/^#[0-9A-F]{6}$/i` — invalid colors are set to null.

### serviceId=1000 — INTERNAL

| Type | Key | Callback              | Description                                     |
| ---- | --- | --------------------- | ----------------------------------------------- |
| 100  | —   | `handleActivityEvent` | Client-side activity heartbeat (mouse/keyboard) |

Client-only — never sent by the server. Used to track user activity for online status.

### serviceId=42 — Polls

| Event type | Key                | Description                                     |
| ---------- | ------------------ | ----------------------------------------------- |
| 20         | `pollSubscription` | Subscribed to poll updates (viewport enter)     |
| 21         | `pollSubscription` | Unsubscribed from poll updates (viewport leave) |

These are NOT votes — they're viewport subscriptions for receiving real-time
poll result updates. They fire automatically when a poll scrolls into/out of view.

### serviceId=46 — Chat (chatws only)

| Event type | Key               | Description             |
| ---------- | ----------------- | ----------------------- |
| 10         | `chatRoomMessage` | Livestream chat message |

```json
{
  "type": 10,
  "chatRoomMessage": {
    "chatRoomId": "<roomId>",
    "senderId": "<accountId>",
    "content": "message text",
    "type": 0,
    "private": 0,
    "attachments": [],
    "accountFlags": 1,
    "metadata": "{\"senderIsCreator\": true, \"senderIsStaff\": false}",
    "chatRoomAccountId": "<accountId>",
    "id": "<messageId>",
    "createdAt": 1776273660119,
    "embeds": [],
    "usernameColor": "#ff0000",
    "username": "example_creator",
    "displayname": "Example Creator"
  }
}
```

Note: `metadata` is stringified JSON (quad-nesting at this level).
`usernameColor: "#ff0000"` indicates creator. `senderIsCreator`/`senderIsStaff` flags in metadata.

## Price Encoding

All prices are in **mills** (thousandths of a dollar). Divide by 1000 for USD.

| Raw value | USD    |
| --------- | ------ |
| `9990`    | $9.99  |
| `12000`   | $12.00 |
| `13990`   | $13.99 |
| `49990`   | $49.99 |

The extra decimal place (vs cents) is for tax calculation precision before rounding.

## Timestamp Formats

Timestamps are inconsistent across the protocol:

| Context                         | Format               | Example          |
| ------------------------------- | -------------------- | ---------------- |
| Session `lastUsed`, `createdAt` | Milliseconds         | `1776222201000`  |
| `lastPing`                      | Milliseconds         | `1776224384172`  |
| Media like `createdAt`          | Milliseconds         | `1776225221107`  |
| New message `createdAt`         | Seconds with decimal | `1776225990.786` |
| REST API post `createdAt`       | Seconds              | `1776272494`     |

The Pydantic `_parse_timestamp()` validator handles all formats: values > 1e10 are divided by 1000.

## Connection Headers

```
User-Agent: <browser UA>
Origin: https://fansly.com
Sec-Fetch-Dest: empty
Sec-Fetch-Mode: websocket
Sec-Fetch-Site: same-site
DNT: 1
Sec-GPC: 1
Cookie: f-s-c=<session>; f-d=<device_id>
```

## Anti-Detection Notes

- Real browser sessions maintain a persistent WebSocket connection
- Ping timing must match browser behavior (20-25s jittered, not fixed)
- Ping timeout detection uses 1.2x multiplier (from main.js)
- Unknown message types should be silently discarded (not logged at production level)
- SSL certificate verification is disabled (matching existing codebase pattern)

## Full Subscription Purchase Flow (observed)

```
t+0.0s  svc=16 type=1  Payment initiated     $12.00  status=7 (awaiting 3DS)
t+3.0s  svc=6  type=3  Wallet credited        $12.00  (null → user wallet, type=14001)
t+3.2s  svc=6  type=2  Wallet balance updated         (+12000, walletVersion++)
t+3.3s  svc=16 type=2  Payment confirmed              status=3
t+4.2s  svc=15 type=5  Subscription created   $12.00  status=2 (pending)
t+4.5s  svc=15 type=5  Subscription confirmed         status=3 (with tier name, endsAt)
t+4.8s  svc=6  type=3  Sub debit              $12.00  (wallet → null, type=58000)
t+4.8s  svc=6  type=2  Wallet balance updated         (-12000, walletVersion++)
t+5.7s  svc=5  type=1  Auto-welcome message           (creator's automated DM)
```

All events are correlated via `correlationId` ↔ `historyId` chains.

## Full PPV Media Purchase Flow (observed)

```
t-Xm  svc=5  type=1   New message (creator)        DM with attachment offering locked media
                                                   attachments[0].contentId = <media-id>
t+0s  svc=2  type=7   PPV order                    order.accountMediaId = <media-id>
                                                   order.correlationAccountId = <creator-id>
                                                   order.accountId = <buyer-id>
t+0.1s svc=6 type=2   Wallet balance update        balance64 - balance = purchase amount
                                                   walletVersion ticks
t+0.2s svc=6 type=3   Transaction record           type=58000, amount = mills value
                                                   correlationId points at an upstream payment
                                                   record NOT broadcast separately
```

The earlier DM offers the locked-preview media; the `attachments[0].contentId` equals the eventual `order.accountMediaId`, so a daemon can correlate "this DM contained this purchase" without an extra API call.

Daemon reaction policy: dispatch on the `svc=2 type=7` order (the terminal signal that the unlock occurred). The wallet update / transaction events are informational only — they fire reliably alongside the order but carry no additional access-state information the order doesn't already convey.

Distinct from the subscription flow's correlation: `order.orderId ≠ wallet.transaction.correlationId` (they're close in Snowflake-mint time but not equal). For PPV-specific receipt or audit-trail features, the join key has to come from a REST endpoint.

## Platform Infrastructure Notes

### Livestreaming — AWS IVS

Fansly uses **AWS Interactive Video Service (IVS)** for livestreaming:

- **Ingest**: Creator broadcasts via `IVSBroadcastClient` browser SDK (`BASIC_LANDSCAPE` config)
- **Playback**: HLS via `*.us-east-1.playback.live-video.net` (AWS account `862541535858`)
- **Chat**: NOT AWS IVS chat — Fansly runs their own `chatws.fansly.com` WebSocket (serviceId=46)
- **Stream state**: Managed via serviceId=45 `StreamingService` (type 10=state, type 11=permission)

### Lovense Integration

Livestreams support native **Lovense** device integration:

- Tips sent in chat (serviceId=7 `TippingService`) are forwarded to the Lovense browser extension
- Detection runs client-side on the creator's broadcast page
- Creator must keep the broadcast page open for tip forwarding to work
- The extension auto-detects; no manual pairing required from the chat/viewer side

### Analytics — Amplitude

The frontend includes the **Amplitude** analytics SDK for product telemetry (page views, feature usage, conversion funnels). Not functional code — purely behavioral tracking. Real browser sessions emit Amplitude events; the downloader does not, which is a theoretical anti-detection fingerprint gap.
