# Edit the docs

The site uses [Zensical](https://zensical.org/docs/). Markdown pages live in `docs/`,
and `zensical.toml` defines the navigation, theme, and Markdown extensions.

## Preview and build

Run from the repository root:

```sh
uv run --frozen --only-group docs zensical serve
```

Open <http://localhost:8000>. The preview reloads when you change a page.
The command installs the locked documentation dependencies automatically;
you do not need Docker or the binary build dependencies to edit the docs.

Build the same output as CI with:

```sh
uv run --frozen --only-group docs zensical build --clean --strict
```

The generated site is in `site/`. Strict mode fails on warnings, including broken
internal links and anchors.
To keep binary development dependencies installed too, use `--group docs`
instead of `--only-group docs`.

## Add or change a page

1. Edit the Markdown file under `docs/`.
2. If you add a page, add it to `project.nav` in `zensical.toml`.
3. Link to other pages with relative `.md` paths. Zensical rewrites them for the site.
4. Run the strict build and inspect the preview.

Use short, direct sentences and consistent terms. Keep procedures in guides and
constraints in references. Link to an existing explanation instead of repeating it.
Include information that helps readers complete the task or make a choice.
Keep explanations that connect the steps; brevity should not make the reader guess.

## GitHub Pages setup

The **Documentation** workflow checks PRs and deploys docs changes from `main`.
For the first deployment:

1. In the repository, open **Settings → Pages**.
2. Under **Build and deployment**, set **Source** to **GitHub Actions**.
3. Run **Documentation** on `main` after the setup is merged.
4. Check the workflow's `github-pages` environment URL.

The configured site address is <https://vodes.github.io/muxtools-binaries/>.
For a fork or custom domain, update `site_url`, `repo_url`, and `repo_name` in
`zensical.toml`, and the documentation link in the README.
