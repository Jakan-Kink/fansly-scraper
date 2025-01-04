from .stash_group import StashGroup


class StashGroupDescription:
    containing_group: StashGroup
    sub_group: StashGroup
    description: str

    def __init__(
        self, containing_group: StashGroup, sub_group: StashGroup, description: str = ""
    ) -> None:
        self.containing_group = containing_group
        self.sub_group = sub_group
        self.description = description
