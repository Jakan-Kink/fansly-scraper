from .stash_studio import StashStudio


class StashStudioRelationship:
    def __init__(self, parent_studio: StashStudio, child_studio: StashStudio):
        self.parent_studio = parent_studio
        self.child_studio = child_studio
