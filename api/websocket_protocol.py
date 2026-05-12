"""Canonical Fansly WebSocket protocol constants.

Single source of truth for message types, service IDs, notification codes,
and per-service human labels. Consumed by ``api/websocket.py``,
 and ``daemon/handlers.py``.

Sourced from ``docs/reference/Fansly-WebSocket-Protocol.md`` and
``main.js EventService.ServiceIds``.
"""

from __future__ import annotations


WS_VERSION = 3
DEFAULT_URL = "wss://wsv3.fansly.com"
CHAT_URL = "wss://chatws.fansly.com"


# Top-level message types — main.js EventService.handleText
MSG_ERROR = 0
MSG_SESSION = 1
MSG_PING = 2
MSG_SERVICE_EVENT = 10000
MSG_BATCH = 10001
MSG_CHAT_ROOM = 46001


# Service IDs — names match main.js EventService.ServiceIds. Numbers are
# the canonical identifiers; SERVICE_NAMES below maps numbers → formal labels.
SVC_POST = 1
SVC_MEDIA = 2
SVC_FOLLOWER = 3
SVC_GROUP = 4
SVC_MESSAGE = 5
SVC_WALLET = 6
SVC_TIPPING = 7
SVC_ONLINE_STATUS = 8
SVC_NOTIFICATION = 9
SVC_PROFILE = 10
SVC_IGNORE = 11
SVC_ACCOUNT = 12
SVC_CHATBOT = 13
SVC_ORDER = 14
SVC_SUBSCRIPTION = 15
SVC_PAYMENT = 16
SVC_CCBILL = 17
SVC_AFFILIATE = 18
SVC_SAGA = 19
SVC_EMAIL = 20
SVC_REPORT = 21
SVC_TWOFA = 22
SVC_PARTNER = 23
SVC_ADMIN = 24
SVC_EMAILBOT = 25
SVC_INOVIO = 26
SVC_FRAUD = 27
SVC_GEOLOCATION = 28
SVC_TWITCH = 29
SVC_LISTS = 30
SVC_SETTINGS = 31
SVC_STORY = 32
SVC_WEBPUSH = 33
SVC_NOTES = 34
SVC_CCBILL_NEW = 35
SVC_TIMELINE_STATS = 36
SVC_PAYOP = 37
SVC_VAULT = 38
SVC_MANAGEMENT = 39
SVC_EARNINGS_STATS = 40
SVC_MEDIA_STORIES = 41
SVC_POLLS = 42
SVC_PAYOUT = 43
SVC_CONTENT_DISCOVERY = 44
SVC_STREAMING = 45
SVC_CHAT_ROOM = 46
SVC_EMBED = 66
SVC_TENOR = 67
SVC_PERMISSION_OVERWRITE = 71
SVC_INTERNAL = 1000


SERVICE_NAMES: dict[int, str] = {
    SVC_POST: "PostService",
    SVC_MEDIA: "MediaService",
    SVC_FOLLOWER: "FollowerService",
    SVC_GROUP: "GroupService",
    SVC_MESSAGE: "MessageService",
    SVC_WALLET: "WalletService",
    SVC_TIPPING: "TippingService",
    SVC_ONLINE_STATUS: "OnlineStatusService",
    SVC_NOTIFICATION: "NotificationService",
    SVC_PROFILE: "ProfileService",
    SVC_IGNORE: "IgnoreService",
    SVC_ACCOUNT: "AccountService",
    SVC_CHATBOT: "ChatBotService",
    SVC_ORDER: "OrderService",
    SVC_SUBSCRIPTION: "SubscriptionService",
    SVC_PAYMENT: "PaymentService",
    SVC_CCBILL: "CCBillService",
    SVC_AFFILIATE: "AffiliateService",
    SVC_SAGA: "SagaService",
    SVC_EMAIL: "EmailService",
    SVC_REPORT: "ReportService",
    SVC_TWOFA: "TwoFAService",
    SVC_PARTNER: "PartnerService",
    SVC_ADMIN: "AdminService",
    SVC_EMAILBOT: "EmailBotService",
    SVC_INOVIO: "InovioService",
    SVC_FRAUD: "FraudService",
    SVC_GEOLOCATION: "GeoLocationService",
    SVC_TWITCH: "TwitchService",
    SVC_LISTS: "ListsService",
    SVC_SETTINGS: "SettingsService",
    SVC_STORY: "StoryService",
    SVC_WEBPUSH: "WebPushService",
    SVC_NOTES: "NotesService",
    SVC_CCBILL_NEW: "CCBillServiceNew",
    SVC_TIMELINE_STATS: "TimelineStatsService",
    SVC_PAYOP: "PayopService",
    SVC_VAULT: "VaultService",
    SVC_MANAGEMENT: "ManagementService",
    SVC_EARNINGS_STATS: "EarningsStatsService",
    SVC_MEDIA_STORIES: "MediaStoriesService",
    SVC_POLLS: "PollsService",
    SVC_PAYOUT: "PayoutService",
    SVC_CONTENT_DISCOVERY: "ContentDiscoveryService",
    SVC_STREAMING: "StreamingService",
    SVC_CHAT_ROOM: "ChatRoomService",
    SVC_EMBED: "EmbedService",
    SVC_TENOR: "TenorService",
    SVC_PERMISSION_OVERWRITE: "PermissionOverwriteService",
    SVC_INTERNAL: "INTERNAL",
}


# Inner notification.type codes — serviceId * 1000 + N.
# Labels are heuristic and viewer-side semantics may differ from the
# label text; verify against live traffic before surfacing in user-facing UI.
NOTIFICATION_TYPES: dict[int, str] = {
    1002: "Post Like",
    1003: "Post Reply",
    1004: "Post Reply",
    1005: "Post Quote",
    2002: "Media Like",
    2007: "Media Purchase",
    2008: "Bundle Purchase",
    3002: "New Follower",
    3003: "Unfollowed",
    5003: "Message Reaction",
    7001: "Tip",
    15006: "New Subscriber",
    15007: "Sub Expired",
    15011: "Promotion",
    15016: "New Subscriber",
    32007: "Locked Text Purchase",
    45012: "Stream Ticket Purchase",
}


def service_name(service_id: int) -> str:
    """Return the EventService name for ``service_id``, or ``UnknownService``."""
    return SERVICE_NAMES.get(service_id, "UnknownService")


def format_event_label(service_id: int, event_type: int) -> str:
    """Return ``"<ServiceName> svc=<id> type=<type>"`` for log lines."""
    return f"{service_name(service_id)} svc={service_id} type={event_type}"
