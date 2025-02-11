"""GraphQL fragments for Stash queries.

These fragments match the ones defined in schema/fragments.graphql.
"""

# Configuration fragments
SCAN_METADATA_OPTIONS = """
    rescan
    scanGenerateCovers
    scanGeneratePreviews
    scanGenerateImagePreviews
    scanGenerateSprites
    scanGeneratePhashes
    scanGenerateThumbnails
    scanGenerateClipPreviews
"""

AUTO_TAG_METADATA_OPTIONS = """
    performers
    studios
    tags
"""

GENERATE_METADATA_OPTIONS = """
    covers
    sprites
    previews
    imagePreviews
    markers
    markerImagePreviews
    markerScreenshots
    transcodes
    phashes
    interactiveHeatmapsSpeeds
    imageThumbnails
    clipPreviews
"""

CONFIG_DEFAULTS_QUERY = f"""
query ConfigurationDefaults {{
    configuration {{
        defaults {{
            scan {{
                {SCAN_METADATA_OPTIONS}
            }}
            autoTag {{
                {AUTO_TAG_METADATA_OPTIONS}
            }}
            generate {{
                {GENERATE_METADATA_OPTIONS}
            }}
            deleteFile
            deleteGenerated
        }}
    }}
}}
"""

# Job fragments
JOB_FIELDS = """
    id
    status
    subTasks
    description
    progress
    startTime
    endTime
    addTime
    error
"""

FIND_JOB_QUERY = f"""
query FindJob($input: FindJobInput!) {{
    findJob(input: $input) {{
        {JOB_FIELDS}
    }}
}}
"""

# Metadata scan fragments
METADATA_SCAN_MUTATION = """
mutation MetadataScan($input: ScanMetadataInput!) {
    metadataScan(input: $input)
}
"""

# File fragments
FILE_FIELDS = """fragment FileFields on BaseFile {
    id
    path
    basename
    parent_folder_id
    zip_file_id
    mod_time
    size
    fingerprints {
        type
        value
    }
}"""

VIDEO_FILE_FIELDS = """fragment VideoFileFields on VideoFile {
    ...FileFields
    format
    width
    height
    duration
    video_codec
    audio_codec
    frame_rate
    bit_rate
}"""

IMAGE_FILE_FIELDS = """fragment ImageFileFields on ImageFile {
    ...FileFields
    width
    height
}"""

GALLERY_FILE_FIELDS = """fragment GalleryFileFields on GalleryFile {
    ...FileFields
}"""

# Scene fragments
SCENE_FIELDS = """
    id
    title
    code
    details
    director
    urls
    date
    rating100
    organized
    o_counter
    interactive
    interactive_speed
    captions {
        language_code
        caption_type
    }
    last_played_at
    resume_time
    play_duration
    play_count
    play_history
    o_history
    files {
        ...VideoFileFields
    }
    paths {
        screenshot
        preview
        stream
        webp
        vtt
        sprite
        funscript
        interactive_heatmap
        caption
    }
    studio {
        id
        name
        url
        image_path
        rating100
        favorite
        details
    }
    performers {
        id
        name
        gender
        url
        urls
        birthdate
        ethnicity
        country
        eye_color
        height_cm
        measurements
        fake_tits
        career_length
        tattoos
        piercings
        alias_list
        favorite
        image_path
        rating100
        details
        death_date
        hair_color
        weight
    }
    tags {
        id
        name
        description
        aliases
        image_path
    }
    stash_ids {
        endpoint
        stash_id
    }
    sceneStreams {
        url
        mime_type
        label
    }
"""

# Performer fragments
PERFORMER_FIELDS = """
    id
    name
    disambiguation
    urls
    gender
    birthdate
    ethnicity
    country
    eye_color
    height_cm
    measurements
    fake_tits
    penis_length
    circumcised
    career_length
    tattoos
    piercings
    alias_list
    favorite
    ignore_auto_tag
    image_path
    scene_count
    image_count
    gallery_count
    group_count
    performer_count
    o_counter
    rating100
    details
    death_date
    hair_color
    weight
    stash_ids {
        endpoint
        stash_id
    }
    tags {
        id
        name
        description
        aliases
        image_path
    }
    custom_fields
"""

# Studio fragments
STUDIO_FIELDS = """
    id
    name
    url
    image_path
    aliases
    ignore_auto_tag
    scene_count
    image_count
    gallery_count
    performer_count
    group_count
    rating100
    favorite
    details
    stash_ids {
        endpoint
        stash_id
    }
    tags {
        id
        name
        description
        aliases
        image_path
    }
    parent_studio {
        id
        name
        url
        image_path
    }
"""

# Tag fragments
TAG_FIELDS = """
    id
    name
    description
    aliases
    ignore_auto_tag
    favorite
    image_path
    scene_count
    scene_marker_count
    image_count
    gallery_count
    performer_count
    studio_count
    group_count
    parent_count
    child_count
    parents {
        id
        name
        description
        aliases
    }
    children {
        id
        name
        description
        aliases
    }
"""

# Scene query templates
SCENE_QUERY_FRAGMENTS = f"""
{FILE_FIELDS}
{VIDEO_FILE_FIELDS}
fragment SceneFragment on Scene {{
    {SCENE_FIELDS}
}}
"""

FIND_SCENE_QUERY = f"""
{SCENE_QUERY_FRAGMENTS}
query FindScene($id: ID!) {{
    findScene(id: $id) {{
        ...SceneFragment
    }}
}}
"""

FIND_SCENES_QUERY = f"""
{SCENE_QUERY_FRAGMENTS}
query FindScenes($filter: FindFilterType, $scene_filter: SceneFilterType) {{
    findScenes(filter: $filter, scene_filter: $scene_filter) {{
        count
        duration
        filesize
        scenes {{
            ...SceneFragment
        }}
    }}
}}
"""

CREATE_SCENE_MUTATION = f"""
{SCENE_QUERY_FRAGMENTS}
mutation CreateScene($input: SceneCreateInput!) {{
    sceneCreate(input: $input) {{
        ...SceneFragment
    }}
}}
"""

UPDATE_SCENE_MUTATION = f"""
{SCENE_QUERY_FRAGMENTS}
mutation UpdateScene($input: SceneUpdateInput!) {{
    sceneUpdate(input: $input) {{
        ...SceneFragment
    }}
}}
"""

FIND_DUPLICATE_SCENES_QUERY = f"""
{SCENE_QUERY_FRAGMENTS}
query FindDuplicateScenes($distance: Int, $duration_diff: Float) {{
    findDuplicateScenes(distance: $distance, duration_diff: $duration_diff) {{
        ...SceneFragment
    }}
}}
"""

PARSE_SCENE_FILENAMES_QUERY = """
query ParseSceneFilenames($filter: FindFilterType, $config: SceneParserInput!) {
    parseSceneFilenames(filter: $filter, config: $config)
}
"""

SCENE_WALL_QUERY = f"""
{SCENE_QUERY_FRAGMENTS}
query SceneWall($q: String) {{
    sceneWall(q: $q) {{
        ...SceneFragment
    }}
}}
"""

BULK_SCENE_UPDATE_MUTATION = f"""
{SCENE_QUERY_FRAGMENTS}
mutation BulkSceneUpdate($input: BulkSceneUpdateInput!) {{
    bulkSceneUpdate(input: $input) {{
        ...SceneFragment
    }}
}}
"""

SCENES_UPDATE_MUTATION = f"""
{SCENE_QUERY_FRAGMENTS}
mutation ScenesUpdate($input: [SceneUpdateInput!]!) {{
    scenesUpdate(input: $input) {{
        ...SceneFragment
    }}
}}
"""

SCENE_GENERATE_SCREENSHOT_MUTATION = """
mutation SceneGenerateScreenshot($id: ID!, $at: Float) {
    sceneGenerateScreenshot(id: $id, at: $at)
}
"""

# Performer query templates
FIND_PERFORMER_QUERY = f"""
query FindPerformer($id: ID!) {{
    findPerformer(id: $id) {{
        {PERFORMER_FIELDS}
    }}
}}
"""

FIND_PERFORMERS_QUERY = f"""
query FindPerformers($filter: FindFilterType, $performer_filter: PerformerFilterType) {{
    findPerformers(filter: $filter, performer_filter: $performer_filter) {{
        count
        performers {{
            {PERFORMER_FIELDS}
        }}
    }}
}}
"""

CREATE_PERFORMER_MUTATION = f"""
mutation CreatePerformer($input: PerformerCreateInput!) {{
    performerCreate(input: $input) {{
        {PERFORMER_FIELDS}
    }}
}}
"""

UPDATE_PERFORMER_MUTATION = f"""
mutation UpdatePerformer($input: PerformerUpdateInput!) {{
    performerUpdate(input: $input) {{
        {PERFORMER_FIELDS}
    }}
}}
"""

# Studio query templates
FIND_STUDIO_QUERY = f"""
query FindStudio($id: ID!) {{
    findStudio(id: $id) {{
        {STUDIO_FIELDS}
    }}
}}
"""

FIND_STUDIOS_QUERY = f"""
query FindStudios($filter: FindFilterType, $studio_filter: StudioFilterType) {{
    findStudios(filter: $filter, studio_filter: $studio_filter) {{
        count
        studios {{
            {STUDIO_FIELDS}
        }}
    }}
}}
"""

CREATE_STUDIO_MUTATION = f"""
mutation CreateStudio($input: StudioCreateInput!) {{
    studioCreate(input: $input) {{
        {STUDIO_FIELDS}
    }}
}}
"""

UPDATE_STUDIO_MUTATION = f"""
mutation UpdateStudio($input: StudioUpdateInput!) {{
    studioUpdate(input: $input) {{
        {STUDIO_FIELDS}
    }}
}}
"""

# Tag query templates
FIND_TAG_QUERY = f"""
query FindTag($id: ID!) {{
    findTag(id: $id) {{
        {TAG_FIELDS}
    }}
}}
"""

FIND_TAGS_QUERY = f"""
query FindTags($filter: FindFilterType, $tag_filter: TagFilterType) {{
    findTags(filter: $filter, tag_filter: $tag_filter) {{
        count
        tags {{
            {TAG_FIELDS}
        }}
    }}
}}
"""

CREATE_TAG_MUTATION = f"""
mutation CreateTag($input: TagCreateInput!) {{
    tagCreate(input: $input) {{
        {TAG_FIELDS}
    }}
}}
"""

UPDATE_TAG_MUTATION = f"""
mutation UpdateTag($input: TagUpdateInput!) {{
    tagUpdate(input: $input) {{
        {TAG_FIELDS}
    }}
}}
"""

# Gallery fragments
GALLERY_FIELDS = """
    id
    title
    code
    date
    urls
    details
    photographer
    rating100
    organized
    image_count
    studio {
        id
        name
        url
        image_path
        rating100
        favorite
        details
    }
    scenes {
        id
        title
        code
        paths {
            screenshot
            preview
        }
    }
    performers {
        id
        name
        gender
        url
        urls
        image_path
        favorite
        rating100
    }
    tags {
        id
        name
        description
        aliases
        image_path
    }
    files {
        ...GalleryFileFields
    }
"""

# Gallery query templates
GALLERY_QUERY_FRAGMENTS = f"""
{FILE_FIELDS}
{GALLERY_FILE_FIELDS}
fragment GalleryFragment on Gallery {{
    {GALLERY_FIELDS}
}}
"""

FIND_GALLERY_QUERY = f"""
{GALLERY_QUERY_FRAGMENTS}
query FindGallery($id: ID!) {{
    findGallery(id: $id) {{
        ...GalleryFragment
    }}
}}
"""

FIND_GALLERIES_QUERY = f"""
{GALLERY_QUERY_FRAGMENTS}
query FindGalleries($filter: FindFilterType, $gallery_filter: GalleryFilterType) {{
    findGalleries(filter: $filter, gallery_filter: $gallery_filter) {{
        count
        galleries {{
            ...GalleryFragment
        }}
    }}
}}
"""

CREATE_GALLERY_MUTATION = f"""
{GALLERY_QUERY_FRAGMENTS}
mutation CreateGallery($input: GalleryCreateInput!) {{
    galleryCreate(input: $input) {{
        ...GalleryFragment
    }}
}}
"""

UPDATE_GALLERY_MUTATION = f"""
{GALLERY_QUERY_FRAGMENTS}
mutation UpdateGallery($input: GalleryUpdateInput!) {{
    galleryUpdate(input: $input) {{
        ...GalleryFragment
    }}
}}
"""

GALLERIES_UPDATE_MUTATION = f"""
{GALLERY_QUERY_FRAGMENTS}
mutation GalleriesUpdate($input: [GalleryUpdateInput!]!) {{
    galleriesUpdate(input: $input) {{
        ...GalleryFragment
    }}
}}
"""

GALLERY_DESTROY_MUTATION = """
mutation GalleryDestroy($input: GalleryDestroyInput!) {
    galleryDestroy(input: $input)
}
"""

REMOVE_GALLERY_IMAGES_MUTATION = """
mutation RemoveGalleryImages($input: GalleryRemoveInput!) {
    removeGalleryImages(input: $input)
}
"""

SET_GALLERY_COVER_MUTATION = """
mutation SetGalleryCover($input: GallerySetCoverInput!) {
    setGalleryCover(input: $input)
}
"""

RESET_GALLERY_COVER_MUTATION = """
mutation ResetGalleryCover($input: GalleryResetCoverInput!) {
    resetGalleryCover(input: $input)
}
"""

GALLERY_CHAPTER_CREATE_MUTATION = f"""
{GALLERY_QUERY_FRAGMENTS}
mutation GalleryChapterCreate($input: GalleryChapterCreateInput!) {{
    galleryChapterCreate(input: $input) {{
        id
        title
        image_index
        gallery {{
            ...GalleryFragment
        }}
    }}
}}
"""

GALLERY_CHAPTER_UPDATE_MUTATION = f"""
{GALLERY_QUERY_FRAGMENTS}
mutation GalleryChapterUpdate($input: GalleryChapterUpdateInput!) {{
    galleryChapterUpdate(input: $input) {{
        id
        title
        image_index
        gallery {{
            ...GalleryFragment
        }}
    }}
}}
"""

GALLERY_CHAPTER_DESTROY_MUTATION = """
mutation GalleryChapterDestroy($id: ID!) {
    galleryChapterDestroy(id: $id)
}
"""

GALLERY_ADD_IMAGES_MUTATION = """
mutation AddGalleryImages($input: GalleryAddInput!) {
    addGalleryImages(input: $input)
}
"""

# Image fragments
IMAGE_FIELDS = """
    id
    title
    code
    rating100
    organized
    o_counter
    date
    urls
    details
    photographer
    studio {
        id
        name
        url
        image_path
        rating100
        favorite
        details
    }
    performers {
        id
        name
        gender
        url
        urls
        image_path
        favorite
        rating100
    }
    tags {
        id
        name
        description
        aliases
        image_path
    }
    galleries {
        id
        title
    }
    files {
        ...ImageFileFields
    }
    paths {
        thumbnail
        preview
        image
    }
"""

# Image query templates
IMAGE_QUERY_FRAGMENTS = f"""
{FILE_FIELDS}
{IMAGE_FILE_FIELDS}
fragment ImageFragment on Image {{
    {IMAGE_FIELDS}
}}
"""

FIND_IMAGE_QUERY = f"""
{IMAGE_QUERY_FRAGMENTS}
query FindImage($id: ID!) {{
    findImage(id: $id) {{
        ...ImageFragment
    }}
}}
"""

FIND_IMAGES_QUERY = f"""
{IMAGE_QUERY_FRAGMENTS}
query FindImages($filter: FindFilterType, $image_filter: ImageFilterType) {{
    findImages(filter: $filter, image_filter: $image_filter) {{
        count
        megapixels
        filesize
        images {{
            ...ImageFragment
        }}
    }}
}}
"""

CREATE_IMAGE_MUTATION = f"""
{IMAGE_QUERY_FRAGMENTS}
mutation CreateImage($input: ImageCreateInput!) {{
    imageCreate(input: $input) {{
        ...ImageFragment
    }}
}}
"""

UPDATE_IMAGE_MUTATION = f"""
{IMAGE_QUERY_FRAGMENTS}
mutation UpdateImage($input: ImageUpdateInput!) {{
    imageUpdate(input: $input) {{
        ...ImageFragment
    }}
}}
"""

# Marker fragments
MARKER_FIELDS = """
    id
    title
    seconds
    scene {
        id
        title
        paths {
            screenshot
            preview
            stream
        }
    }
    primary_tag {
        id
        name
        description
        aliases
        image_path
    }
    tags {
        id
        name
        description
        aliases
        image_path
    }
    stream
    preview
    screenshot
"""

# Marker query templates
FIND_MARKER_QUERY = f"""
query FindMarker($id: ID!) {{
    findSceneMarker(id: $id) {{
        {MARKER_FIELDS}
    }}
}}
"""

FIND_MARKERS_QUERY = f"""
query FindMarkers($filter: FindFilterType, $marker_filter: SceneMarkerFilterType) {{
    findSceneMarkers(filter: $filter, scene_marker_filter: $marker_filter) {{
        count
        scene_markers {{
            {MARKER_FIELDS}
        }}
    }}
}}
"""

CREATE_MARKER_MUTATION = f"""
mutation CreateMarker($input: SceneMarkerCreateInput!) {{
    sceneMarkerCreate(input: $input) {{
        {MARKER_FIELDS}
    }}
}}
"""

UPDATE_MARKER_MUTATION = f"""
mutation UpdateMarker($input: SceneMarkerUpdateInput!) {{
    sceneMarkerUpdate(input: $input) {{
        {MARKER_FIELDS}
    }}
}}
"""

# Scene marker tag query
SCENE_MARKER_TAG_QUERY = f"""
{TAG_FIELDS}
{MARKER_FIELDS}
query FindSceneMarkerTags($scene_id: ID!) {{
    sceneMarkerTags(scene_id: $scene_id) {{
        tag {{
            {TAG_FIELDS}
        }}
        scene_markers {{
            {MARKER_FIELDS}
        }}
    }}
}}
"""

# Tag mutations
TAGS_MERGE_MUTATION = f"""
{TAG_FIELDS}
mutation TagsMerge($input: TagsMergeInput!) {{
    tagsMerge(input: $input) {{
        {TAG_FIELDS}
    }}
}}
"""

BULK_TAG_UPDATE_MUTATION = f"""
{TAG_FIELDS}
mutation BulkTagUpdate($input: BulkTagUpdateInput!) {{
    bulkTagUpdate(input: $input) {{
        {TAG_FIELDS}
    }}
}}
"""

# Metadata mutations
METADATA_GENERATE_MUTATION = """
mutation MetadataGenerate($input: GenerateMetadataInput!) {
    metadataGenerate(input: $input)
}
"""
