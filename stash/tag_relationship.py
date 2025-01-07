from .types import StashTagProtocol, StashTagRelationshipProtocol


class StashTagRelationship(StashTagRelationshipProtocol):
    def __init__(
        self, parent_tag: StashTagProtocol, child_tag: StashTagProtocol
    ) -> None:
        StashTagRelationshipProtocol.__init__(self)
        self.parent_tag = parent_tag
        self.child_tag = child_tag
