#!/bin/sh
# Build the offline demo bundle (PLAN.md section 14.2):
#   crewquarters-offline_<version>_<arch>.tar.zst = .deb + pinned OCI image archive +
#   SHA256SUMS (+ SBOMs when syft is installed) (+ an optional validated model directory).
# usage: infra/debian/build-offline-bundle.sh VERSION ARCH [--with-vllm] [--model DIR]
# The platform image crewquarters/platform:VERSION must exist locally for ARCH
# (docker buildx build --platform linux/ARCH --load ...).
set -eu
VERSION="${1:?version}"; ARCH="${2:?arch}"; shift 2
ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
WITH_VLLM=no; MODEL_DIR=""
while [ $# -gt 0 ]; do
    case "$1" in
        --with-vllm) WITH_VLLM=yes ;;
        --model) MODEL_DIR="$2"; shift ;;
    esac
    shift
done
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
DEB=$("$ROOT_DIR/infra/debian/build-deb.sh" "$VERSION" "$ARCH")
cp "$DEB" "$WORK/"
PG=$(grep -o 'pgvector/pgvector@sha256:[a-f0-9]*' "$ROOT_DIR/infra/compose/compose.appliance.yaml")
IMAGES="crewquarters/platform:$VERSION $PG"
for profile in "$ROOT_DIR"/catalog/models/dgx/*.json; do
    img=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['launch']['image'])" "$profile")
    [ "$WITH_VLLM" = yes ] && IMAGES="$IMAGES $img"
done
for img in $IMAGES; do
    docker image inspect "$img" >/dev/null 2>&1 || docker pull --platform "linux/$ARCH" "$img"
done
# shellcheck disable=SC2086
docker save -o "$WORK/images.tar" $(echo $IMAGES | tr ' ' '\n' | sort -u)
if command -v syft >/dev/null 2>&1; then
    for img in $IMAGES; do
        syft -q "$img" -o spdx-json > "$WORK/sbom-$(echo "$img" | tr '/:@' '___').spdx.json"
    done
fi
if [ -n "$MODEL_DIR" ]; then
    mkdir -p "$WORK/models"; cp -r "$MODEL_DIR" "$WORK/models/"
fi
(cd "$WORK" && find . -type f ! -name SHA256SUMS -printf '%P\n' | sort | xargs sha256sum > SHA256SUMS)
OUT="$ROOT_DIR/dist/crewquarters-offline_${VERSION}_${ARCH}.tar.zst"
tar --zstd -cf "$OUT" -C "$WORK" .
echo "$OUT"
