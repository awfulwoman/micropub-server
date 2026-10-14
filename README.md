# micropub-server

A small Python service that lets IndieWeb clients publish to a static site.
It speaks [Micropub](https://micropub.spec.indieweb.org) and
[IndieAuth](https://indieauth.spec.indieweb.org), turns each post into a
Markdown file with front matter, and commits it to the site's Git repo. The
site's own CI builds and deploys it.

It is a Python re-implementation of the parts of
[Indiekit](https://github.com/getindiekit/indiekit) that awfulwoman.com uses.
Indiekit is the reference for behaviour, not for code.

## Status

Specification only. No code yet.

## Specs

Start with [`docs/specs/00-overview.md`](docs/specs/00-overview.md).

| Spec | Covers |
| --- | --- |
| [00 Overview](docs/specs/00-overview.md) | Goals, scope, architecture, build phases |
| [01 Standards](docs/specs/01-standards.md) | Which open standards, and how much of each |
| [02 IndieAuth](docs/specs/02-indieauth.md) | Sign-in, codes, tokens, introspection, metadata |
| [03 Micropub](docs/specs/03-micropub.md) | Create, update, delete, undelete, queries |
| [04 Media endpoint](docs/specs/04-media-endpoint.md) | Uploads, image processing, media queries |
| [05 Post model](docs/specs/05-post-model.md) | JF2, normalisation, post type discovery, slugs, paths |
| [06 Post templates](docs/specs/06-post-templates.md) | Rendering Hugo front matter, awfulwoman.com mappings |
| [07 Content stores](docs/specs/07-content-stores.md) | The store interface and the GitHub store |
| [08 Syndication](docs/specs/08-syndication.md) | Mastodon and Bluesky cross-posting |
| [09 Configuration](docs/specs/09-configuration.md) | YAML config and environment variables |
| [10 Data model](docs/specs/10-data-model.md) | SQLite schema |
| [11 Deployment](docs/specs/11-deployment.md) | Container, health, infra composition, cut-over |
| [12 Indiekit divergences](docs/specs/12-indiekit-divergences.md) | Indiekit bugs and choices we do not copy |
| [13 Web UI](docs/specs/13-web-ui.md) | Writing and managing posts in the browser, share bookmarklet |
