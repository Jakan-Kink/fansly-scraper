from .stash_group import StashGroup


class StashGroupDescription:
    def __init__(
        self, containing_group: StashGroup, sub_group: StashGroup, description: str = ""
    ):
        self.containing_group = containing_group
        self.sub_group = sub_group
        self.description = description
