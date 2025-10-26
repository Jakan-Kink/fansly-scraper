variable "PRIV_BUILD_REPO" {
    type = string
    default = "moby-dangling"
}

group "default" {
    targets = ["fansly"]
}
target "fansly" {
    matrix = {
        size = ["base", "full"]
        python_version = ["3.12"]
    }
    args = {
        PYTHON_VERSION = python_version,
        SIZE = size
    }
    name = replace("fansly-${size}-${python_version}", ".", "-")
    platforms = ["linux/amd64", "linux/arm64"]
    dockerfile = "./Dockerfile"
    context = "."
    tags = [
        "${PRIV_BUILD_REPO}:${size}",
        "${PRIV_BUILD_REPO}:${size}-latest",
        "${size == "base" ? "${PRIV_BUILD_REPO}:latest" : ""}"
    ]
}
