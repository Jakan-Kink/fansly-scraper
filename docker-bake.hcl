group "default" {
    targets = ["fansly"]
}
target "fansly" {
    matrix = {
        size = ["base", "full"]
    }
    name = "fansly-${size}"
    platforms = ["linux/amd64", "linux/arm64"]
    dockerfile = "./Dockerfile"
    context = "."
    tags = [
        "${PRIV_BUILD_REPO}:${size}",
        "${PRIV_BUILD_REPO}:${size}-latest",
        "${size == "base" ? "${PRIV_BUILD_REPO}:latest" : ""}"
    ]
}
