---
status: current
---

# Fansly Frontend Route Table

Extracted from `main.js` Angular route configuration.

Guard legend: `si` = authenticated user, `os` = general auth, `eM` = creator/admin, `TR` = creator-specific

## Public Routes (no guard)

| Route                                               | Description                                        |
| --------------------------------------------------- | -------------------------------------------------- |
| `/`                                                 | Landing page (redirects to /home if authenticated) |
| `/age`                                              | Age verification gate                              |
| `/r/:refcode`                                       | Referral redirect                                  |
| `/promo/:refcode`                                   | Promo code with referral                           |
| `/promo`                                            | General promo page                                 |
| `/email/unsubscribe/:token`                         | Email unsubscribe                                  |
| `/passwordreset/:token`                             | Password reset                                     |
| `/emailverify/:token`                               | Email verification                                 |
| `/search`                                           | Search                                             |
| `/explore`                                          | Explore/discover content                           |
| `/explore/discover`                                 | Discover tab (default)                             |
| `/explore/accounts`                                 | Explore accounts                                   |
| `/explore/new`                                      | New content                                        |
| `/explore/new/:mediaOfferId`                        | Specific media offer                               |
| `/explore/foryou`                                   | For You feed                                       |
| `/explore/foryou/settings`                          | For You preferences                                |
| `/explore/foryou/:tag`                              | For You by tag                                     |
| `/explore/tag/:tag`                                 | Tag browse                                         |
| `/fyp`                                              | For You Page (standalone)                          |
| `/notificationalerts/unsubscribe/:unsubscribeToken` | Notification alert unsubscribe                     |
| `/subscriptions/giftcode/:code`                     | Gift code redemption                               |
| `/application`                                      | Creator application                                |
| `/application/form`                                 | Creator application form                           |
| `/chatroom/:chatRoomId`                             | Standalone chat room                               |
| `/thirdpartyconnect/login/:token`                   | Third-party OAuth login                            |
| `/thirdpartyconnect/connect/:token`                 | Third-party connect                                |
| `/thirdpartyconnect/error/:code`                    | Third-party error                                  |
| `/managementsession/claim/:token`                   | Agency management session claim                    |
| `/leaderboard`                                      | Leaderboard                                        |
| `/redirect/external`                                | External URL redirect proxy                        |
| `/founders`                                         | Founders page                                      |
| `/support`                                          | Support page                                       |
| `/tos`                                              | Terms of Service                                   |
| `/terms`                                            | Terms (alias)                                      |
| `/privacy`                                          | Privacy policy                                     |
| `/contact`                                          | Contact page                                       |
| `/usc2257`                                          | USC 2257 compliance                                |
| `/dmca`                                             | DMCA policy                                        |
| `/cookies`                                          | Cookie policy                                      |
| `/imprint`                                          | Legal imprint                                      |

## Authenticated User Routes (guard: `si`)

| Route                       | Description                     |
| --------------------------- | ------------------------------- |
| `/home`                     | Home timeline (preloaded)       |
| `/home/subscribed`          | Home — subscribed creators only |
| `/home/followed`            | Home — followed creators only   |
| `/home/list/:listId`        | Home — filtered by list         |
| `/affiliates`               | Affiliate program               |
| `/notifications`            | Notifications (preloaded)       |
| `/messages`                 | Messages inbox                  |
| `/messages/:groupId`        | Specific conversation           |
| `/statistics/:mediaOfferId` | Media offer statistics          |
| `/subscriptions`            | Active subscriptions            |
| `/collection`               | Personal collection             |
| `/lists`                    | Creator lists                   |
| `/lists/:listId`            | Specific list                   |
| `/bookmarks`                | Bookmarks                       |
| `/bookmarks/:albumType`     | Bookmarks by album type         |
| `/settings`                 | Settings parent                 |
| `/settings/account`         | Account settings                |
| `/settings/profile`         | Profile settings                |
| `/settings/notifications`   | Notification settings           |
| `/settings/display`         | Display/theme settings          |
| `/settings/payments`        | Payment settings                |
| `/settings/explore`         | Explore preferences             |
| `/settings/privacy`         | Privacy settings                |
| `/settings/sessions`        | Active sessions                 |
| `/settings/connections`     | Connected accounts              |
| `/settings/about`           | About                           |

## Auth-Required Content Routes (guard: `os`)

| Route                              | Description                    |
| ---------------------------------- | ------------------------------ |
| `/post/:postId`                    | Single post view (preloaded)   |
| `/post/:postId/:interactionSource` | Post with interaction tracking |
| `/post/:postId/attachments/:index` | Post attachment viewer         |
| `/live/:username`                  | Watch livestream               |
| `/collection/:username`            | Creator's collection           |
| `/collection/:username/:albumId`   | Specific collection album      |
| `/:username`                       | Creator profile (catch-all)    |
| `/:username/posts`                 | Creator posts (default tab)    |
| `/:username/media`                 | Creator media tab              |
| `/:username/media/pictures`        | Creator pictures only          |
| `/:username/media/wall/:wallId`    | Creator media by wall          |
| `/:username/followers`             | Creator's followers            |
| `/:username/following`             | Creator's following            |
| `/:username/wall/:wallId`          | Creator wall view              |
| `/:username/posts/wall/:wallId`    | Creator posts by wall          |
| `/:username/posts/album/:albumId`  | Creator posts by album         |
| `/:username/:internalId`           | Creator internal page          |

## Creator Dashboard Routes (guards: `si` + `TR`)

| Route                           | Description              |
| ------------------------------- | ------------------------ |
| `/scheduled`                    | Scheduled posts          |
| `/creator`                      | Creator dashboard parent |
| `/creator/subscribers`          | Subscriber management    |
| `/creator/streaming`            | Streaming setup          |
| `/creator/uploads`              | Upload management        |
| `/creator/lists`                | Creator's lists          |
| `/creator/lists/:listId`        | Specific list management |
| `/creator/plans`                | Subscription plans       |
| `/creator/chat`                 | Chat management          |
| `/creator/topsupporters`        | Top supporters           |
| `/creator/profilestats`         | Profile statistics       |
| `/creator/trackinglinks`        | Tracking links           |
| `/creator/earnings`             | Earnings parent          |
| `/creator/earnings/wallet`      | Wallet/balance (default) |
| `/creator/earnings/statistics`  | Earnings statistics      |
| `/creator/vault`                | Content vault            |
| `/creator/management`           | Agency management        |
| `/creator/payout/documentation` | Payout documentation     |

## Admin/Internal Routes (guard: `eM`)

| Route                                     | Description                     |
| ----------------------------------------- | ------------------------------- |
| `/settings/design-reference`              | Design system component library |
| `/settings/design-reference-integrations` | Design system integrations      |
| `/live/broadcast`                         | Livestream broadcast page       |

## Other

| Route                   | Description                             |
| ----------------------- | --------------------------------------- |
| `/payments/application` | Payment application (no explicit guard) |
| `/**`                   | Wildcard catch-all                      |
