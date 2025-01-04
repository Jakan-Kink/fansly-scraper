from .stash_tag import StashTag


class StashTagRelationship:
    def __init__(self, parent_tag: StashTag, child_tag: StashTag):
        self.parent_tag = parent_tag
        self.child_tag = child_tag
