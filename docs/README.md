# STITCH documentation

STITCH uses LLMs to configure builds and infer harness specifications for C and C++ libraries, then runs coverage-guided fuzzing with automatic crash triage.

The [documentation site](https://hgarrereyn.github.io/STITCH/) starts with a quickstart, followed by bug reproduction and coverage examples.

For instructions in Markdown:

- [Quickstart and examples](getting-started.md)
- [Command reference](cli-reference.md)
- [Architecture](architecture.md)

## Preview and edit the site

Open `docs/index.html` directly, or run this from the repository root:

```bash
python3 -m http.server 8000 --directory docs
```

Visit <http://localhost:8000>. The site is plain HTML, CSS, and JavaScript; no build step or network access is needed to read it. JavaScript only adds copy buttons and redirects old documentation bookmarks.

Edit `index.html` for content and `assets/site.css` for styling. Keep command examples in the Markdown guides consistent with the site. The existing GitHub Pages workflow publishes `docs/` when documentation changes are pushed to `main`.
