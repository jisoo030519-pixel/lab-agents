#!/bin/sh
# GitHub Pages 에 올린다. 저장소를 만든 다음 한 번만 실행하면 된다.
#   sh publish-site.sh https://github.com/<계정>/lab-agents.git
set -e
[ -z "$1" ] && { echo "사용법: sh publish-site.sh <저장소 주소>"; exit 1; }
cd "$(dirname "$0")"
git remote remove origin 2>/dev/null || true
git remote add origin "$1"
git branch -M main
git push -u origin main
echo ""
echo "올렸습니다. 이제 브라우저에서:"
echo "  저장소 → Settings → Pages → Source: main / \"/docs\" → Save"
echo ""
echo "1~2분 뒤 주소가 열립니다:"
echo "$1" | sed -E 's#https://github.com/([^/]+)/([^/]+)\.git#  https://\1.github.io/\2/#'
