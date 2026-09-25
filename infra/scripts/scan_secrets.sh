#!/bin/sh
# Secret-scan built artifacts (PLAN.md section 16.2: "Secret scanner on repository and built
# artifacts"). The repository itself is scanned by gitleaks in CI; this covers what ships.
#
#   infra/scripts/scan_secrets.sh ARTIFACT...
#
# ARTIFACT may be:
#   *.deb                  unpacked (data and control members) and scanned
#   *.tar.zst / *.tar      an offline bundle: its manifest (SHA256SUMS), SBOMs, and the .deb
#                          inside are scanned; images.tar is scanned per image by the
#                          image-security CI job instead
#   *.tar (docker archive) pass --image to scan an image archive's filesystem
#   a directory            scanned as is (e.g. apps/web/dist)
#
# Findings fail the script (exit 1). Known test fixtures are allowlisted in
# infra/scripts/trivy-secret.yaml, each with a reason. Uses `trivy` from PATH, or the
# aquasec/trivy image when it is not installed.
set -eu
ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
CONFIG="$ROOT_DIR/infra/scripts/trivy-secret.yaml"
TRIVY_IMAGE="${TRIVY_IMAGE:-aquasec/trivy:0.66.0}"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

trivy_run() { # trivy_run SUBCOMMAND TARGET [ARGS...] ; TARGET is a host path
    sub="$1"; parent=$(cd "$(dirname "$2")" && pwd); name=$(basename "$2"); shift 2
    if command -v trivy >/dev/null 2>&1; then
        trivy "$sub" --quiet --scanners secret --secret-config "$CONFIG" --exit-code 1 \
            "$@" "$parent/$name"
    else
        docker run --rm -v "$parent:/scan:ro" -v "$CONFIG:/trivy-secret.yaml:ro" \
            "$TRIVY_IMAGE" "$sub" --quiet --scanners secret --secret-config /trivy-secret.yaml \
            --exit-code 1 "$@" "/scan/$name"
    fi
}

scan_dir() {
    echo "== secret scan: $1"
    trivy_run fs "$1" --skip-dirs node_modules
}

scan_deb() {
    dir="$WORK/deb-$(basename "$1")"
    mkdir -p "$dir/control"
    dpkg-deb -x "$1" "$dir"
    dpkg-deb -e "$1" "$dir/control"
    scan_dir "$dir"
}

IMAGE=no
status=0
for artifact in "$@"; do
    case "$artifact" in
        --image) IMAGE=yes; continue ;;
    esac
    if [ -d "$artifact" ]; then
        scan_dir "$artifact" || status=1
    elif [ "$IMAGE" = yes ]; then
        echo "== secret scan (image archive): $artifact"
        trivy_run image "$artifact" --input || status=1
    else
        case "$artifact" in
            *.deb) scan_deb "$artifact" || status=1 ;;
            *.tar.zst|*.tar)
                dir="$WORK/bundle-$(basename "$artifact")"
                mkdir -p "$dir"
                # Everything but the image archive and model weights: manifest, SBOMs, .deb.
                tar -xf "$artifact" -C "$dir" --exclude='./images.tar' --exclude='./models' \
                    --exclude='./embedding-models'
                test -f "$dir/SHA256SUMS" || { echo "no SHA256SUMS in $artifact"; status=1; }
                for deb in "$dir"/*.deb; do
                    [ -e "$deb" ] && { scan_deb "$deb" || status=1; rm -f "$deb"; }
                done
                scan_dir "$dir" || status=1
                ;;
            *) echo "unsupported artifact: $artifact" >&2; status=1 ;;
        esac
    fi
done
[ "$status" -eq 0 ] && echo "No secrets found."
exit "$status"
