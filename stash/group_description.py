from .types import StashGroupDescriptionProtocol, StashGroupProtocol


class StashGroupDescription(StashGroupDescriptionProtocol):
    def __init__(
        self,
        containing_group: StashGroupProtocol,
        sub_group: StashGroupProtocol,
        description: str = "",
    ) -> None:
        self.containing_group = containing_group
        self.sub_group = sub_group
        self.description = description

    @classmethod
    def from_dict(cls, data: dict) -> "StashGroupDescription":
        """Create a StashGroupDescription instance from a dictionary.

        Args:
            data: Dictionary containing group description data

        Returns:
            A new StashGroupDescription instance
        """
        from .stash_group import StashGroup

        return cls(
            containing_group=StashGroup.from_dict(data.get("containing_group", {})),
            sub_group=StashGroup.from_dict(data.get("sub_group", {})),
            description=data.get("description", ""),
        )
