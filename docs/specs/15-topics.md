# 15 Topics

A second taxonomy for awfulwoman.com, beside tags.

- **Tags** stay free-form. They are what the author types, and they go out as
  hashtags when a post is syndicated to the Fediverse and Bluesky
  ([08](08-syndication.md)).
- **Topics** are a small, fixed set of themes for browsing the site. They are
  never syndicated.

The name is "topics", not "categories": in Micropub `category` already means
tags ([05](05-post-model.md)).

## The topic map

The site owns the list of topics, as it owns its content shape
([06](06-post-templates.md#the-site-owns-the-shape)). It lives in the site
repo as **`data/topics.yaml`**, so Hugo layouts can read the titles too:

```yaml
# awfulwoman.com/data/topics.yaml
web:
  title: Web development
  tags: [web, html, css, javascript, ...]
lgbtq:
  title: LGBTQ
  tags: [trans, transphobia, pride, ...]
```

- The key is the topic's slug and its URL: `/topics/<key>/`. Keys are
  lowercase.
- `title` is what the site shows.
- `tags` are the tags that imply this topic. Matching ignores case. A tag
  maps to one topic at most.

The server reads the file from the content store at run time, in the same
way as `data/micropub.yaml`. If the file is missing or broken, the server
logs a warning and writes no topics. It never refuses a post because of it.

## Rules

- A post has **0 to 3 topics**, in front matter as `topics: [web, accessibility]`.
- Topics can apply to every post type.
- A topic must be a key in `data/topics.yaml`. The server drops unknown
  topics and reports them in the web UI.

## How a post gets its topics

### From tags (the default)

When a post is created, the server looks up each tag in the map and writes
the topics it finds. If more than 3 match, it keeps the 3 with the most
matching tags. On a tie, the order in `data/topics.yaml` decides.

### Set by hand

A client can send the topics itself with the custom property `mp-topic`
(one value or a list). Then the server does not use the map.

- More than 3 values: `400 invalid_request`.
- The web UI ([13](13-web-ui.md)) shows the topics from the map as ticked
  checkboxes, and the author can change them before posting.

### On update

- An update that changes `category` and does not send `mp-topic`: the
  server works out the topics again from the new tags.
- An update that sends `mp-topic`: the server writes those topics.
- An update that does neither: the topics do not change.

`mp-topic` is not stored as a property. It becomes `topics` in front matter
and is returned as `topic` by `q=source`.

## Backfill of old posts

A one-off script, `scripts/backfill_topics.py`, in the same way as
`scripts/backfill_targets.py`. It works on a local checkout of the site and
makes one commit per step.

1. **Tagged posts (about 175).** Topics from the map, as above. No review.
2. **Untagged notes and articles (about 330).** An LLM (Ollama, through the
   gateway) reads each post and suggests 0 to 3 topics from the map. The
   script writes the suggestions to a review file:

   ```yaml
   content/notes/2021/02/10/asyjrrb5/index.md:
     title: "..."
     suggested: [lgbtq, politics]
   ```

   The author edits the file in batches. Then `--apply <file>` writes the
   approved topics.
3. Other untagged posts (bookmarks, likes, reposts, quotes, checkins) get no
   topics from the backfill.

The script never changes a post that already has `topics`.

## Integrations

| Repo | Change |
| --- | --- |
| awfulwoman.com | Add `topic: topics` to `taxonomies` in `hugo.yaml` |
| awfulwoman.com | `data/topics.yaml` (drafted) |
| awfulwoman.com | Topic list at `/topics/` and term pages, with titles from `data/topics.yaml` |
| awfulwoman.com | Show a post's topics on the page; add "Topics" to the nav |
| awfulwoman.com | Delete the `test` tag |
| micropub-server | Read `data/topics.yaml`; `mp-topic`; topics in the web UI |
| micropub-server | `scripts/backfill_topics.py` |
| agents | `awfulwoman-create-draft`: accept topics and pass them as `mp-topic` |

Hugo gives each topic an RSS feed at `/topics/<key>/index.xml` with no extra
work.
