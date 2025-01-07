from .types import StashStudioProtocol, StashStudioRelationshipProtocol


class StashStudioRelationship(StashStudioRelationshipProtocol):
    def __init__(
        self, parent_studio: StashStudioProtocol, child_studio: StashStudioProtocol
    ) -> None:
        StashStudioRelationshipProtocol.__init__(self)
        self.parent_studio = parent_studio
        self.child_studio = child_studio
