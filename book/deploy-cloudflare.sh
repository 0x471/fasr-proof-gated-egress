#!/usr/bin/env bash
# Deploy the book to Cloudflare Pages, behind Cloudflare Access (only listed emails can read it).
# Usage from the repo root:
#   book/deploy-cloudflare.sh placeholder   # first: create the project with an empty page
#   (lock both hostnames with Cloudflare Access, see book/DEPLOY.md, then check in a private window)
#   book/deploy-cloudflare.sh book          # refuses unless the production hostname redirects to Access
# Needs: Node (npx), `npx wrangler@4.147.0 login` done once, and mdBook 0.5.4 as `mdbook` or $MDBOOK.
set -euo pipefail
cd "$(dirname "$0")/.."

WRANGLER="npx --yes wrangler@4.147.0"
PROJECT="${CF_PAGES_PROJECT:-proof-gated-egress}"
HOST="${CF_PAGES_HOST:-$PROJECT.pages.dev}"
MDBOOK="${MDBOOK:-mdbook}"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

locked() {
  # Locked means the page itself doesn't load for an unauthenticated request: Access answers
  # with a redirect to the team's login page (or 401/403), never with the content (200).
  local out code loc
  out="$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 15 "https://$HOST/")" || return 1
  code="${out%% *}"; loc="${out#* }"
  [[ "$code" != 200 ]] && { [[ "$loc" == *".cloudflareaccess.com/"* ]] || [[ "$code" == 401 || "$code" == 403 ]]; }
}

case "${1:-}" in
  placeholder)
    $WRANGLER pages project list | grep -qw "$PROJECT" || $WRANGLER pages project create "$PROJECT" --production-branch main
    printf '<!doctype html><title>Private</title><p>Nothing here.</p>\n' > "$OUT/index.html"
    $WRANGLER pages deploy "$OUT" --project-name "$PROJECT" --branch main
    echo "Placeholder deployed. Now lock https://$HOST and its previews with Access (book/DEPLOY.md)."
    ;;
  book)
    if ! locked; then
      echo "https://$HOST does not redirect to Cloudflare Access. Lock it first (book/DEPLOY.md); not deploying." >&2
      exit 1
    fi
    MDBOOK_OUTPUT__HTML__SITE_URL=/ "$MDBOOK" build book -d "$OUT"
    $WRANGLER pages deploy "$OUT" --project-name "$PROJECT" --branch main
    locked && echo "Deployed behind Access: https://$HOST" || { echo "WARNING: https://$HOST is not behind Access after deploy" >&2; exit 1; }
    ;;
  *)
    echo "usage: $0 placeholder|book" >&2
    exit 2
    ;;
esac
