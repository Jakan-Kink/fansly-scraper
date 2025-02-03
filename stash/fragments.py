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
FILE_FIELDS = """
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
    created_at
    updated_at
"""

VIDEO_FILE_FIELDS = f"""
    {FILE_FIELDS}
    format
    width
    height
    duration
    video_codec
    audio_codec
    frame_rate
    bit_rate
"""

IMAGE_FILE_FIELDS = f"""
    {FILE_FIELDS}
    width
    height
"""

GALLERY_FILE_FIELDS = FILE_FIELDS

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
    created_at
    updated_at
    last_played_at
    resume_time
    play_duration
    play_count
    play_history
    o_history
    files {
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
        format
        width
        height
        duration
        video_codec
        audio_codec
        frame_rate
        bit_rate
        created_at
        updated_at
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
        created_at
        updated_at
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
        created_at
        updated_at
    }
    tags {
        id
        name
        description
        aliases
        created_at
        updated_at
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
    created_at
    updated_at
    stash_ids {
        endpoint
        stash_id
    }
    tags {
        id
        name
        description
        aliases
        created_at
        updated_at
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
    created_at
    updated_at
    stash_ids {
        endpoint
        stash_id
    }
    tags {
        id
        name
        description
        aliases
        created_at
        updated_at
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
    created_at
    updated_at
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
FIND_SCENE_QUERY = f"""
query FindScene($id: ID!) {{
    findScene(id: $id) {{
        {SCENE_FIELDS}
    }}
}}
"""

FIND_SCENES_QUERY = f"""
query FindScenes($filter: FindFilterType, $scene_filter: SceneFilterType) {{
    findScenes(filter: $filter, scene_filter: $scene_filter) {{
        count
        duration
        filesize
        scenes {{
            {SCENE_FIELDS}
        }}
    }}
}}
"""

CREATE_SCENE_MUTATION = f"""
mutation CreateScene($input: SceneCreateInput!) {{
    sceneCreate(input: $input) {{
        {SCENE_FIELDS}
    }}
}}
"""

UPDATE_SCENE_MUTATION = f"""
mutation UpdateScene($input: SceneUpdateInput!) {{
    sceneUpdate(input: $input) {{
        {SCENE_FIELDS}
    }}
}}
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
    created_at
    updated_at
    studio {
        id
        name
        url
        image_path
        rating100
        favorite
        details
        created_at
        updated_at
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
FIND_GALLERY_QUERY = f"""
query FindGallery($id: ID!) {{
    findGallery(id: $id) {{
        {GALLERY_FIELDS}
    }}
}}
"""

FIND_GALLERIES_QUERY = f"""
query FindGalleries($filter: FindFilterType, $gallery_filter: GalleryFilterType) {{
    findGalleries(filter: $filter, gallery_filter: $gallery_filter) {{
        count
        galleries {{
            {GALLERY_FIELDS}
        }}
    }}
}}
"""

CREATE_GALLERY_MUTATION = f"""
mutation CreateGallery($input: GalleryCreateInput!) {{
    galleryCreate(input: $input) {{
        {GALLERY_FIELDS}
    }}
}}
"""

UPDATE_GALLERY_MUTATION = f"""
mutation UpdateGallery($input: GalleryUpdateInput!) {{
    galleryUpdate(input: $input) {{
        {GALLERY_FIELDS}
    }}
}}
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
    created_at
    updated_at
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
FIND_IMAGE_QUERY = f"""
query FindImage($id: ID!) {{
    findImage(id: $id) {{
        {IMAGE_FIELDS}
    }}
}}
"""

FIND_IMAGES_QUERY = f"""
query FindImages($filter: FindFilterType, $image_filter: ImageFilterType) {{
    findImages(filter: $filter, image_filter: $image_filter) {{
        count
        megapixels
        filesize
        images {{
            {IMAGE_FIELDS}
        }}
    }}
}}
"""

CREATE_IMAGE_MUTATION = f"""
mutation CreateImage($input: ImageCreateInput!) {{
    imageCreate(input: $input) {{
        {IMAGE_FIELDS}
    }}
}}
"""

UPDATE_IMAGE_MUTATION = f"""
mutation UpdateImage($input: ImageUpdateInput!) {{
    imageUpdate(input: $input) {{
        {IMAGE_FIELDS}
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
    created_at
    updated_at
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
