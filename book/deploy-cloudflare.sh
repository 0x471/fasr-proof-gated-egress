#!/usr/bin/env bash
# Deploy the book to Cloudflare Pages, behind Cloudflare Access (only listed emails can read it).
# Usage from the repo root:
#   CF_ACCESS_TEAM=<team> book/deploy-cloudflare.sh placeholder   # first: an empty page
#   (lock every hostname and the previews with Cloudflare Access, then audit them by hand: book/DEPLOY.md)
#   CF_ACCESS_TEAM=<team> book/deploy-cloudflare.sh book          # refuses unless all of them redirect to Access
# Needs: Node (npx), `npx wrangler@4.147.0 login` done once, and mdBook 0.5.4 as `mdbook` or $MDBOOK.
# The checks here are a smoke test, not an audit of the Access policies; see book/DEPLOY.md.
set -euo pipefail
cd "$(dirname "$0")/.."

WRANGLER="${WRANGLER:-npx --yes wrangler@4.147.0}"
PROJECT="${CF_PAGES_PROJECT:-proof-gated-egress}"
TEAM="${CF_ACCESS_TEAM:?set CF_ACCESS_TEAM to your Zero Trust team name (<team>.cloudflareaccess.com)}"
MDBOOK="${MDBOOK:-mdbook}"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
[[ "$PROJECT" =~ ^[a-z0-9-]+$ && "$TEAM" =~ ^[a-z0-9-]+$ ]] || { echo "bad project or team name" >&2; exit 2; }

# The project's domains as Cloudflare reports them, one per line; the project name must match exactly.
domains() {
  $WRANGLER pages project list --json | python3 -I -c '
import json, sys
rows = [r for r in json.load(sys.stdin) if r.get("Project Name") == sys.argv[1]]
if len(rows) != 1:
    sys.exit(1)
print("\n".join(d for d in rows[0].get("Project Domains", "").split(", ") if d))' "$PROJECT"
}

# Locked: an unauthenticated request is redirected to this team's Access login for this hostname.
locked() {
  local out code loc
  out="$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 15 "https://$1/")" || return 1
  code="${out%% *}"; loc="${out#* }"
  local login="https://$TEAM.cloudflareaccess.com/cdn-cgi/access/login/$1"
  [[ "$code" == 302 || "$code" == 303 ]] && [[ "$loc" == "$login" || "$loc" == "$login?"* || "$loc" == "$login/"* ]]
}

case "${1:-}" in
  placeholder)
    # wrangler 4.147 sends "pages project create" to the newer Workers flow, which fails for a
    # direct-upload project, so the project is created once in the dashboard (book/DEPLOY.md).
    domains >/dev/null || { echo "no Pages project named exactly $PROJECT: create it in the dashboard first (book/DEPLOY.md)" >&2; exit 1; }
    printf '<!doctype html><title>Private</title><p>Nothing here.</p>\n' > "$OUT/index.html"
    $WRANGLER pages deploy "$OUT" --project-name "$PROJECT" --branch main
    echo "Placeholder deployed. Now lock the production hostname and the previews with Access (book/DEPLOY.md)."
    ;;
  book)
    ALL="$(domains)" || { echo "no project named exactly $PROJECT" >&2; exit 1; }
    HOST="${CF_PAGES_HOST:-$PROJECT.pages.dev}"
    grep -qxF "$HOST" <<< "$ALL" || { echo "$HOST is not a domain of project $PROJECT" >&2; exit 1; }
    # Before anything is uploaded: every domain of the project, and a preview address that no
    # deployment uses (it must still hit the Access login, so previews are covered too).
    PAGES="$(grep -m1 '\.pages\.dev$' <<< "$ALL")" || { echo "project $PROJECT has no pages.dev domain" >&2; exit 1; }
    for h in $ALL "access-check-$RANDOM.$PAGES"; do
      if ! locked "$h"; then
        echo "https://$h does not redirect to $TEAM's Access login. Lock it first (book/DEPLOY.md); not deploying." >&2
        exit 1
      fi
    done
    MDBOOK_OUTPUT__HTML__SITE_URL=/ "$MDBOOK" build book -d "$OUT/site"
    $WRANGLER pages deploy "$OUT/site" --project-name "$PROJECT" --branch main | tee "$OUT/deploy.log"
    # Each upload also gets its own address (<hash>.<project>.pages.dev); it must be locked too.
    DEPLOYMENT="$(grep -oE 'https://[a-z0-9-]+\.[a-z0-9.-]+\.pages\.dev' "$OUT/deploy.log" | head -1 | sed 's|https://||' || true)"
    for h in "$HOST" ${DEPLOYMENT:+"$DEPLOYMENT"}; do
      if ! locked "$h"; then
        echo "WARNING: https://$h is not behind Access. Delete this deployment in the dashboard now." >&2
        exit 1
      fi
    done
    [[ -n "$DEPLOYMENT" ]] || { echo "WARNING: couldn't find this deployment's own address to check; check it by hand." >&2; exit 1; }
    echo "Deployed behind Access: https://$HOST (this upload: https://$DEPLOYMENT)"
    ;;
  *)
    echo "usage: CF_ACCESS_TEAM=<team> $0 placeholder|book" >&2
    exit 2
    ;;
esac
