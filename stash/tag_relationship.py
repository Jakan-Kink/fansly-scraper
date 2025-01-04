from .stash_tag import StashTag


class StashTagRelationship:
    parent_tag: StashTag
    child_tag: StashTag

    def __init__(self, parent_tag: StashTag, child_tag: StashTag) -> None:
        self.parent_tag = parent_tag
        self.child_tag = child_tag
