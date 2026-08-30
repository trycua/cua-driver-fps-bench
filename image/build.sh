#!/usr/bin/env bash
# Build the Fleet benchmark image for linux/amd64 and push it to the private ECR
# repo that Fleet's pool-admission policy allows (cua-gymdriver-dev).
#
#   image/build.sh            # build + push, tag = cua-driver-bench-<utc date>-<CUA_REF[:8]>
#   PUSH=0 image/build.sh     # local build only (loads into the docker daemon)
#
# Prints the full image reference on the last line; export it as FPS_BENCH_FLEET_IMAGE.
set -euo pipefail
cd "$(dirname "$0")"

REGISTRY=${REGISTRY:-296062593712.dkr.ecr.us-west-2.amazonaws.com}
REPO=${REPO:-cua-gymdriver-dev}
CUA_REF=${CUA_REF:-$(grep -m1 '^ARG CUA_REF=' Dockerfile | cut -d= -f2)}
TAG=${TAG:-cua-driver-bench-$(date -u +%Y%m%d)-${CUA_REF:0:8}}
PUSH=${PUSH:-1}
PLATFORM=${PLATFORM:-linux/amd64}

if [[ "$PUSH" == "1" ]]; then
  IMAGE="$REGISTRY/$REPO:$TAG"
  aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin "$REGISTRY"
  docker buildx build --platform "$PLATFORM" --build-arg "CUA_REF=$CUA_REF" -t "$IMAGE" -f Dockerfile --push ..
else
  IMAGE=${LOCAL_IMAGE:-fps-bench-cua-driver:local}
  docker buildx build --platform "$PLATFORM" --build-arg "CUA_REF=$CUA_REF" -t "$IMAGE" -f Dockerfile --load ..
fi
echo "$IMAGE"
