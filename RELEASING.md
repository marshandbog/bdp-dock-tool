# Building & releasing the Windows installer

The tool is a Python/Tkinter app. The Windows `.exe` and installer are built **in CI on
a Windows runner** — you do not need a Windows machine. Two GitHub Actions do the work:

| Workflow | Trigger | Result |
|---|---|---|
| `.github/workflows/release.yml` | push a `v*` tag (or run manually) | builds `.exe` (PyInstaller) → installer (Inno Setup) → attaches `BDP-Tool-Setup.exe` to a GitHub Release |
| `.github/workflows/pages.yml` | push to `main` touching `site/` (or run manually) | deploys the download page to GitHub Pages |

## First-time setup

1. **Create the GitHub repo and push** (this folder is already a git repo):
   ```sh
   gh repo create <owner>/bdp-dock-tool --private --source=. --remote=origin --push
   ```
   (or make it `--public`; use any repo name — the workflows read the slug automatically.)

2. **Enable Pages:** repo → *Settings → Pages → Build and deployment → Source: GitHub Actions*.

3. **Point isit5oclock.org at the download page** (pick one):
   - **GitHub Pages:** add a `site/CNAME` file containing your download host
     (e.g. `downloads.isit5oclock.org`) and a matching DNS CNAME. Don't repoint the apex
     domain if your main site already lives there — use a subdomain.
   - **Netlify / Vercel / Cloudflare Pages:** ignore `pages.yml`; publish the `site/`
     directory from your existing deploy. The page is static and portable.

## Cutting a release

```sh
git tag v1.0.0
git push origin v1.0.0
```

CI builds and publishes the installer. The download page's button always points at
`…/releases/latest/download/BDP-Tool-Setup.exe`, so it picks up the new version with no edit.

To bump the version, tag a new `vX.Y.Z` — the tag string is passed into the installer as
its version automatically.

## Building locally on Windows (optional)

```bat
pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm packaging\bdp_tool.spec
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DMyAppVersion=1.0.0 packaging\installer.iss
:: installer lands in packaging\Output\BDP-Tool-Setup.exe
```

## Known caveat: code signing

The installer is **unsigned**, so Windows SmartScreen shows an "unknown publisher"
warning (users click *More info → Run anyway*). To remove it, obtain a code-signing
certificate (OV/EV) and add a signing step after the Inno Setup build — happy to wire
that in when you have a cert.
