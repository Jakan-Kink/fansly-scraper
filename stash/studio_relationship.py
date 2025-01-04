from .stash_studio import StashStudio


class StashStudioRelationship:
    parent_studio: StashStudio
    child_studio: StashStudio

    def __init__(self, parent_studio: StashStudio, child_studio: StashStudio) -> None:
        self.parent_studio = parent_studio
        self.child_studio = child_studio
