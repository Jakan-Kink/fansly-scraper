# Fansly API Response Analysis

## Core Response Structure

All API responses follow this basic structure:

```json
{
  "success": true,
  "response": {
    // Endpoint-specific data
  }
}
```

## Post/Timeline Response

Posts appear in timeline and wall responses:

```json
{
  "id": "723516315247063042",
  "accountId": "618592888766341120",
  "content": "Post content text",
  "fypFlags": 0,
  "inReplyTo": null,
  "inReplyToRoot": null,
  "createdAt": 1733994091, // Unix timestamp
  "expiresAt": null,
  "attachments": [
    {
      "pos": 0,
      "contentType": 1, // 1 = image/video
      "contentId": "723516314605334530"
    }
  ],
  "likeCount": 0,
  "idString": "00723516315247063042",
  "timelineReadPermissionFlags": [],
  "accountTimelineReadPermissionFlags": {
    "flags": 0,
    "metadata": "{}"
  },
  "mediaLikeCount": 0,
  "totalTipAmount": 0,
  "attachmentTipAmount": 0,
  "accountMentions": []
}
```

## Media Item Response

Media items contain the actual media content and variants:

```json
{
  "id": "737318930569244672",
  "type": 2, // 2 = video
  "status": 1,
  "accountId": "374708676486770688",
  "mimetype": "video/mp4",
  "flags": 298,
  "location": "/374708676486770688/737318930569244672.mp4",
  "width": 720,
  "height": 1280,
  "metadata": {
    "originalHeight": 3840,
    "originalWidth": 2160,
    "duration": 182.4,
    "resolutionMode": 2,
    "frameRate": 60
  },
  "updatedAt": 1737285141,
  "createdAt": 1737284891,
  "variants": [
    {
      "id": "737318930569244673",
      "type": 1, // 1 = thumbnail
      "status": 1,
      "mimetype": "image/jpeg",
      "flags": 0,
      "location": "/374708676486770688/737318930569244673.jpeg",
      "width": 480,
      "height": 854,
      "metadata": { "resolutionMode": 2 },
      "updatedAt": 1737284981,
      "locations": []
    },
    {
      "id": "737318935682097155",
      "type": 302, // 302 = HLS stream
      "status": 1,
      "mimetype": "application/vnd.apple.mpegurl",
      "flags": 0,
      "location": null,
      "width": 2160,
      "height": 3840,
      "metadata": {
        "originalHeight": 3840,
        "originalWidth": 2160,
        "duration": 182.4,
        "resolutionMode": 2,
        "frameRate": 60,
        "variants": [
          { "w": 2160, "h": 3840 },
          { "w": 1080, "h": 1920 },
          { "w": 720, "h": 1280 },
          { "w": 480, "h": 854 },
          { "w": 360, "h": 640 }
        ]
      },
      "updatedAt": 1737285728,
      "locations": []
    }
  ]
}
```

## Message Response

Messages from conversations:

```json
{
  "id": "722765276893945858",
  "type": 1,
  "dataVersion": 1,
  "content": "Message content text",
  "groupId": "722765276734566402",
  "senderId": "286142689622110208",
  "correlationId": "722765276709396490",
  "inReplyTo": null,
  "inReplyToRoot": null,
  "createdAt": 1733815029,
  "attachments": [],
  "embeds": [],
  "interactions": [
    {
      "userId": "720167541418237953",
      "readAt": 1733815037211,
      "deliveredAt": 1733815033720
    }
  ],
  "likes": [],
  "totalTipAmount": 0
}
```

## Important Notes

### Media Types

- Image/Video Content: `contentType: 1`
- Video: `type: 2`
- Thumbnail: `type: 1`
- HLS Stream: `type: 302`
- DASH Stream: `type: 303`

### Timestamps

All timestamps are Unix timestamps:

- Regular timestamps: seconds since epoch
- Detailed timestamps: milliseconds since epoch

### Media Variants

Videos typically include:

1. Original file
2. Thumbnail images
3. HLS stream with multiple resolutions
4. DASH stream with multiple resolutions

### Permissions

Media access is controlled by:

- `permissionFlags`
- `timelineReadPermissionFlags`
- `accountTimelineReadPermissionFlags`

### URLs and Locations

Media locations can be:

1. Relative paths: `/accountId/mediaId.ext`
2. Full CDN URLs with auth tokens
3. Streaming manifest URLs (m3u8/mpd)

### Metadata

- Most objects include a metadata field
- Can be JSON string or object
- Contains additional type-specific information
