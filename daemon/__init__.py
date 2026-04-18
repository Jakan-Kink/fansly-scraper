"""Monitoring daemon package.

Provides the components for the post-batch monitoring loop:
  - ActivitySimulator: three-tier state machine for anti-detection polling cadence
  - WorkItem hierarchy: typed work items produced by WebSocket event handlers
  - dispatch_ws_event: translate decoded ServiceEvent dicts into WorkItems
  - should_process_creator: timeline-based creator skip filter
  - mark_creator_processed: record lastCheckedAt into MonitorState after a run
"""

from daemon.filters import should_process_creator
from daemon.handlers import (
    CheckCreatorAccess,
    DownloadMessagesForGroup,
    DownloadStoriesOnly,
    DownloadTimelineOnly,
    FullCreatorDownload,
    RedownloadCreatorMedia,
    WorkItem,
    dispatch_ws_event,
)
from daemon.runner import run_daemon
from daemon.simulator import ActivitySimulator
from daemon.state import mark_creator_processed


__all__ = [
    "ActivitySimulator",
    "CheckCreatorAccess",
    "DownloadMessagesForGroup",
    "DownloadStoriesOnly",
    "DownloadTimelineOnly",
    "FullCreatorDownload",
    "RedownloadCreatorMedia",
    "WorkItem",
    "dispatch_ws_event",
    "mark_creator_processed",
    "run_daemon",
    "should_process_creator",
]
