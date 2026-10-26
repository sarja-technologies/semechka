#!/usr/bin/env bash
# Run ON the Spark (deployment evidence; see docs/ENTERPRISE.md).  usage: mkcopy.sh <version>. Fresh disposable copy of CMSIT AI's newest backup, program installed, no service.
set -euo pipefail
V=$1; B=$(ls -dt ~/semechka-deploy/backups/roasted-cmsit-pre-* | head -1); R=~/semechka-deploy/rehearsal
rm -rf $R; mkdir -p $R/data; chmod 700 $R $R/data; cp -a $B/. $R/data/
cd ~/semechka-deploy/releases && rm -rf semechka-$V && tar xzf semechka-$V.tar.gz
( cd semechka-$V && ./install.sh --no-service --name rehearsal --prefix $R/app --data $R/data --port 18787 >$R/install.log 2>&1 ) && echo "copy of $(basename $B) ready, program $V installed" || { tail -20 $R/install.log; exit 1; }
