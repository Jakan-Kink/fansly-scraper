class ImagePathsType:
    thumbnail: str
    preview: str
    image: str

    def __init__(self, thumbnail: str, preview: str, image: str) -> None:
        self.thumbnail = thumbnail
        self.preview = preview
        self.image = image
