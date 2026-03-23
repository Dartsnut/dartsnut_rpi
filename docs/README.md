# Hosting docs on GitHub Pages

Instructions for publishing the contents of `docs/` to GitHub Pages.

## Option 1: Deploy from branch (recommended)

Use GitHub’s built-in “Deploy from a branch” with the `docs/` folder as the source.

### 1. Add a Jekyll config (required)

GitHub Pages builds the site with Jekyll. Add this file so your Markdown is built correctly.

Create **`docs/_config.yml`** with:

```yaml
title: Dartsnut RPi Docs
description: PixelDart & PixelBoard Raspberry Pi deployment and API docs
markdown: kramdown
theme: jekyll-theme-minimal
```

Optional: to avoid Jekyll ignoring files that start with `_`, you can keep only `_config.yml` in `docs/` (no other `_*` files), or add:

```yaml
include: [".htaccess", "*.md", "*.html"]
```

### 2. Turn on GitHub Pages

1. Open the repo on GitHub: **https://github.com/Dartsnut/dartsnut_rpi**
2. Go to **Settings** → **Pages** (left sidebar).
3. Under **Build and deployment** → **Source**, choose **Deploy from a branch**.
4. Under **Branch**:
   - Branch: **main** (or the branch where `docs/` lives).
   - Folder: **/docs**.
5. Click **Save**.

After a minute or two, the site will be at:

- **https://dartsnut.github.io/dartsnut_rpi/** (if the repo is `Dartsnut/dartsnut_rpi`).

### 3. Links between docs

- From `index.md` to other docs use paths like:  
  `[Bluetooth API](bluetooth-api)` or `[WebSocket API](websocket-api)`.
- Jekyll will serve them as `bluetooth-api.html`, etc. Omitting `.html` is fine.

---

## Option 2: GitHub Actions (advanced)

If you later want a different generator (e.g. MkDocs, Docusaurus), you can build in Actions and deploy to `gh-pages`:

1. Create **`.github/workflows/pages.yml`** that:
   - Checks out the repo.
   - Installs the doc tool and builds the site from `docs/` (or a dedicated docs app).
   - Pushes the built output to the `gh-pages` branch (or uploads the artifact and uses `peaceiris/actions-gh-pages`).
2. In **Settings** → **Pages**, set **Source** to **Deploy from a branch**, branch **gh-pages**, folder **/ (root)**.

For plain Markdown like yours, Option 1 is usually enough.

---

## Summary

| Step | Action |
|------|--------|
| 1 | Add `docs/_config.yml` (see above). |
| 2 | Commit and push. |
| 3 | GitHub → Settings → Pages → Deploy from branch → branch **main**, folder **/docs** → Save. |
| 4 | Wait 1–2 minutes, then open **https://&lt;org-or-username&gt;.github.io/dartsnut_rpi/** |

If the repo is under the **Dartsnut** org, the URL is **https://dartsnut.github.io/dartsnut_rpi/**.
