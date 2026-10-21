#!/usr/bin/env bash
# Builds dist/mx-master-linux-<version>.tar.gz from the committed tree (HEAD).
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$here"
version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' mxmaster/__init__.py)
name="mx-master-linux-$version"
mkdir -p dist
git archive --format=tar.gz --prefix="$name/" -o "dist/$name.tar.gz" HEAD \
    -- . ':!make-release.sh' ':!.gitignore'
(cd dist && sha256sum "$name.tar.gz" > "$name.tar.gz.sha256")
echo "dist/$name.tar.gz"
