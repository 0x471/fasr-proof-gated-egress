# Deploying the book privately

GitHub Pages can't restrict who reads a site on a personal account: private Pages need GitHub Enterprise Cloud, and a site built from a private repository is otherwise public. So the book is hosted on **Cloudflare Pages** behind **Cloudflare Access**: readers sign in with an email address you've allowed, using a one-time code sent to that address. Cloudflare's Zero Trust free plan covers this for fewer than 50 users.

**Important:** the "Enable access policy" switch in Pages only protects *preview* deployments, not the main `<project>.pages.dev` address. Step 3 below locks the main address too. Until it's done, anything deployed there is public, which is why the first deploy is an empty placeholder.

## 1. One-time setup

1. Create a free Cloudflare account and open **Zero Trust** once (it asks for a team name; the free plan is enough).
2. In this repo, log wrangler in (opens a browser): `npx --yes wrangler@4.147.0 login`.
3. Have mdBook 0.5.4 available as `mdbook`, or set `MDBOOK=/path/to/mdbook`.

## 2. Deploy the placeholder

```sh
CF_ACCESS_TEAM=<your team name> book/deploy-cloudflare.sh placeholder
```

`<your team name>` is the Zero Trust team name from step 1 (your login page is `<team>.cloudflareaccess.com`).

This creates the Pages project `proof-gated-egress` and deploys a page that says "Nothing here." If Cloudflare gives the project a different address (for example with a suffix because the name is taken), set `CF_PAGES_HOST=<that address>` for the next steps.

## 3. Lock both addresses with Access

Following Cloudflare's instructions ("Enable Access on your *.pages.dev domain"):

1. Cloudflare dashboard > **Workers & Pages** > the project > **Settings** > **Enable access policy**.
2. Select **Manage** on the Access policy it created (for preview deployments).
3. Under **Access** > **Applications**, select the project, then **Configure**.
4. Under **Public hostname**, in the **Subdomain** field, delete the wildcard (`*`) and **Save**. (If it complains, change the application name.)
5. Back in the project > **Settings** > **General**, select **Enable access policy** again, which re-protects the previews.
6. Check there are now **two** Access applications: one for `<project>.pages.dev`, one for the previews.
7. In each, set the policy to **Allow** only these emails: yours, Yogi's and Jakub's. Login method: **One-time PIN**.

Then test: open `https://<project>.pages.dev` in a private window. You should get Cloudflare's sign-in page, not "Nothing here." Try an email that isn't on the list: it must not get in.

## 4. Deploy the book

```sh
CF_ACCESS_TEAM=<your team name> book/deploy-cloudflare.sh book
```

The script refuses to upload the book unless the production address is one Cloudflare reports for the project and redirects a signed-out visitor to **your team's** Access login for that address. After uploading it checks the production address and the upload's own address (`<hash>.<project>.pages.dev`) again, and tells you to delete the deployment if either is open. Run it again whenever the book changes.

**This is a smoke test, not an audit.** It sees one URL from outside; it can't read the Access policies. Before sending the link, check by hand in Zero Trust > Access controls > Applications:

1. There are two applications, one for `<project>.pages.dev` and one for `*.<project>.pages.dev` (previews and per-upload addresses). Old upload addresses stay reachable after newer uploads, so the wildcard one must stay.
2. Each has exactly one Allow policy listing only the readers' emails, login by one-time PIN, and no Bypass or path exceptions.
3. In a private window: a signed-out visitor gets the sign-in page; an email that isn't listed gets no code; a listed email gets in.

If the script refuses although the private window shows the sign-in page, Cloudflare's login address may look different from what it expects: run `curl -sI https://<project>.pages.dev/` and check the `location` header before changing anything.

## Notes

- **Adding a reader:** add their email to both Access policies. **Removing** one doesn't end a session already open: they keep access until it expires (the session duration set on the Access application), so revoke their sessions in Zero Trust too if it matters.
- **A custom domain** (optional) needs its own Access application. Cloudflare can't add a custom domain that already has an Access policy, so the domain is briefly public between adding it and locking it: deploy the placeholder again first, add the domain, create its Access application (Zero Trust > Access controls > Applications > Self-hosted), check it in a private window, then deploy the book.
- **What Cloudflare sees:** the book's content is stored on Cloudflare. Check that this is fine under the residency's confidentiality terms and with your mentor.
- Sources: [GitHub: changing the visibility of your Pages site](https://docs.github.com/en/enterprise-cloud@latest/pages/getting-started-with-github-pages/changing-the-visibility-of-your-github-pages-site); [Cloudflare Pages: preview deployments](https://developers.cloudflare.com/pages/configuration/preview-deployments/); [Cloudflare Pages: known issues](https://developers.cloudflare.com/pages/platform/known-issues/#enable-access-on-your-pagesdev-domain); [Cloudflare Zero Trust plans](https://www.cloudflare.com/plans/zero-trust-services/).
