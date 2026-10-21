# Deploying the book privately

GitHub Pages can't restrict who reads a site on a personal account: private Pages need GitHub Enterprise Cloud, and a site built from a private repository is otherwise public. So the book is hosted on **Cloudflare Pages** behind **Cloudflare Access**: readers sign in with an email address you've allowed, using a one-time code sent to that address. Cloudflare's Zero Trust free plan covers this for fewer than 50 users.

**Important:** the "Enable access policy" switch in Pages only protects *preview* deployments, not the main `<project>.pages.dev` address. Step 3 below locks the main address too. Until it's done, anything deployed there is public, which is why the first deploy is an empty placeholder.

## 1. One-time setup

1. Create a free Cloudflare account and open **Zero Trust** once (it asks for a team name). Choose the **Zero Trust Free** plan: Cloudflare asks for a card and a billing address even for the free plan, and the account owner enters them.
2. In this repo, log wrangler in (opens a browser): `npx --yes wrangler@4.147.0 login`.
3. Have mdBook 0.5.4 available as `mdbook`, or set `MDBOOK=/path/to/mdbook`.

## 2. Deploy the placeholder

```sh
CF_ACCESS_TEAM=<your team name> book/deploy-cloudflare.sh placeholder
```

`<your team name>` is the Zero Trust team name from step 1 (your login page is `<team>.cloudflareaccess.com`).

First create the project once in the dashboard: **Workers & Pages** > **Create** > **Pages** > **Drag and drop your files**, name it `proof-gated-egress`, and select **Create project** (don't upload anything there). wrangler 4.147 can't create it: `pages project create` is redirected to the newer Workers flow and fails. Then the command above deploys a page that says "Nothing here." If Cloudflare gives the project a different address (for example with a suffix because the name is taken), set `CF_PAGES_HOST=<that address>` for the next steps.

## 3. Lock both addresses with Access

Following Cloudflare's instructions ("Enable Access on your *.pages.dev domain"):

1. Cloudflare dashboard > **Workers & Pages** > the project > **Settings** > **Preview access** > **Restrict previews**. This creates an Access application for `*.<project>.pages.dev`.
2. Zero Trust > **Access controls** > **Applications** > that application. Under **Public hostnames**, clear the **Subdomain** field (it holds `*`) and **Save**. The application now protects `<project>.pages.dev` and the previews are open, so do the next step straight away.
3. Back in the project > **Settings** > **Preview access** > **Restrict previews** again: a second application now protects `*.<project>.pages.dev`.
4. Zero Trust > **Access controls** > **Policies** > **Add a policy**: Include > **Emails** > the readers' addresses, Action **Allow**, a name such as "Book readers".
5. In each of the two applications: add that policy, remove the default "Allow Members" policy (its sources are all authenticated users), **Save**.
6. Zero Trust > **Integrations** > **Identity providers** must list a **One-time PIN** provider (it is the default when none is added).

Then test: open `https://<project>.pages.dev` in a private window. You should get Cloudflare's sign-in page, not "Nothing here." Try an email that isn't on the list: it must not get in.

**Audit before uploading the book, not just before sharing the link.** An address nobody has been sent is still public if it isn't locked. In Zero Trust > Access controls > Applications:

1. There are two applications, one for `<project>.pages.dev` and one for `*.<project>.pages.dev` (previews and per-upload addresses). Old upload addresses stay reachable after newer uploads, so the wildcard one must stay.
2. Each has exactly one Allow policy listing only the readers' emails, login by one-time PIN, and no Bypass or path exceptions.
3. No other application covers a path or subdomain of these addresses: a more specific application overrides the general one without inheriting its policy.
4. Every custom domain of the project has its own application, checked the same way.
5. In a private window: a signed-out visitor gets the sign-in page; an email that isn't listed gets no code; a listed email gets in.

## 4. Deploy the book

```sh
CF_ACCESS_TEAM=<your team name> book/deploy-cloudflare.sh book
```

Before uploading anything, the script finds the project whose name matches exactly, refuses unless the production address is one of that project's domains, and checks that every domain of the project, plus a preview address no deployment uses, redirects a signed-out visitor to **your team's** Access login. After uploading it checks the production address and the upload's own address (`<hash>.<project>.pages.dev`) again, and tells you to delete the deployment if either is open. Run it again whenever the book changes.

**This is a smoke test, not an audit.** It sees URLs from outside; it can't read the Access policies, so the manual audit in step 3 comes first.

If the script refuses although the private window shows the sign-in page, Cloudflare's login address may look different from what it expects: run `curl -sI https://<project>.pages.dev/` and check the `location` header before changing anything.

## 5. Deploy from CI on every push to main

`.github/workflows/book.yml` runs the same script (`deploy` job) on pushes to `main` that touch the book, so the same checks apply: nothing is uploaded unless every address of the project already redirects to the Access login. One-time setup:

1. Cloudflare dashboard > **Manage account** > **Account API tokens** > **Create Token** > **Start from scratch**: one permission, **Developer Platform** > **Pages** > **Edit** (not the two "Custom Pages" entries), for this account only, with an expiry (ours: 90 days, until 19 Jan 2027). An account token belongs to the account, not to one person's login. Copy the token once; it isn't shown again (a lost one is replaced with **Roll**).
2. GitHub: the repository's **Settings** > **Environments** > `cloudflare` (only `main` may deploy to it) holds two secrets: `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`. Set the token from your own terminal: `gh secret set CLOUDFLARE_API_TOKEN --env cloudflare` (it prompts for the value, so the token never appears in a command line).
3. Turn the job on: `gh variable set CLOUDFLARE_DEPLOY --body true` (or the repository's **Settings** > **Secrets and variables** > **Actions** > **Variables**). Until then the job is skipped; set it to anything else to pause deploys.

**Limits:** anyone with write access to the repository can change the workflow and so use the token; the token can edit every Pages project in the account (Cloudflare can't scope it to one project). Protect `main` or keep collaborators read-only if that matters. Rotate the token by creating a new one and replacing the secret.

## Notes

- **Adding a reader:** add their email to both Access policies. **Removing** one doesn't end a session already open: they keep access until it expires (the session duration set on the Access application), so revoke their sessions in Zero Trust too if it matters.
- **A custom domain** (optional) needs its own Access application. Cloudflare can't add a custom domain that already has an Access policy, so the domain is briefly public between adding it and locking it: deploy the placeholder again first, add the domain, create its Access application (Zero Trust > Access controls > Applications > Self-hosted), check it in a private window, then deploy the book.
- **What Cloudflare sees:** the book's content is stored on Cloudflare. Check that this is fine under the residency's confidentiality terms and with your mentor.
- Sources: [GitHub: changing the visibility of your Pages site](https://docs.github.com/en/enterprise-cloud@latest/pages/getting-started-with-github-pages/changing-the-visibility-of-your-github-pages-site); [Cloudflare Pages: preview deployments](https://developers.cloudflare.com/pages/configuration/preview-deployments/); [Cloudflare Pages: known issues](https://developers.cloudflare.com/pages/platform/known-issues/#enable-access-on-your-pagesdev-domain); [Cloudflare Zero Trust plans](https://www.cloudflare.com/plans/zero-trust-services/).
