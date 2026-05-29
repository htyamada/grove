# Image Handler Web UI

The web UI is provided by `imhandler.djview`, which supplies the shared
Django views, URL patterns, templates, and app config. It plugs into an
existing Django project; there is no standalone server command.

See `imhandler-django-impl.md` for integration instructions and
`imhandler-specs.md` for the full API.

---

## Pages

### Index

Root page with links to the four sections: Browse, Similarity, Semantic,
Compare.

### Browse

The gallery. Navigation is breadcrumb-based: the current album path is shown
as a trail of links from the collection root down to the current level.

**Interior albums** (directories that contain sub-albums) show a list of
child albums, each with its total image count. Click an album to descend into
it. A parent link navigates up one level.

**Leaf albums** (directories that contain images directly) show a thumbnail
grid. Each thumbnail links to the full-size image. A filename label appears
below each thumbnail.

Sort the grid with `?sort=name` (default), `?sort=mtime`, or `?sort=size`.
Sort links are provided in the page header.

If `cache_dir` is not configured, a warning is shown and thumbnails cannot
be generated.

### Similarity

The gallery with embedding awareness. Identical layout and navigation to
Browse, with two additions on leaf album pages:

- Images that have stored embeddings show a visual indicator. Each such
  image has a **Similar** link that opens the Similar view for that image.
- An **Embed** button appears at the top of the page. Clicking it starts
  the embedding process for the current album and streams progress line by
  line via Server-Sent Events. At the virtual top-level album in a multi-root
  setup, Embed runs once for each configured real root. A **Cancel** button
  stops an in-progress embed; completed batches are already saved and will be
  skipped on the next run.

### Compare

Loads all clusters for a given model and similarity threshold — re-clustering
on each page load. Adjust the model (`clip` / `sscd`) and threshold via links
or the URL query string (`?model=clip&threshold=0.85`). Clusters with more
than 100 members are listed separately at the bottom.

Each cluster entry shows a thumbnail strip of its members. Click a cluster to
open the contact sheet.

### Contact sheet (cluster detail)

Shows all members of a single cluster as a grid, ordered by quality rank
(best first). Each cell shows the thumbnail, filename, pixel dimensions, and
quality tier. Clicking the thumbnail opens the full-size image in a new tab.

Missing images are cleaned up automatically when the page is loaded: if only
one member remains after cleanup, the cluster is deleted and the page
redirects back to Compare.

A **Mark** button beneath each image toggles it into the deletion list.

### Similar

Shows the most similar images in the same directory as a given image,
ranked by cosine similarity. Switch between CLIP and SSCD results with the
model links. From any thumbnail in Browse or Similarity view, use the
**Similar** link to reach this page.

### Semantic search

The top-level **Semantic** page runs a CLIP text-to-image search across all
embedded images in the library. Choose how many matches to show with the
`results` field. The page returns the first `N` matches as clickable
thumbnails. Selecting a result opens the full-size image directly.

---

## Deletion workflow

Images to delete are collected across sessions in a deletion list:

1. Navigate to a cluster contact sheet or the Similar page.
2. Click **Mark** on images you want to remove. The count in the header
   updates immediately (no page reload).
3. When ready, click **Download delete.sh**. This downloads a shell script
   that removes every marked file:

   ```sh
   #!/bin/sh
   rm -- '/home/yamada/Photos/img001.jpg'
   rm -- '/home/yamada/Photos/img001b.jpg'
   ```

   The deletion list is cleared after the download.

4. Review the script, then run it:

   ```
   sh delete.sh
   ```

   The script uses `rm --` so filenames starting with `-` are handled
   safely. Paths with single quotes are escaped with `'\''`.

No deletion is performed by the web UI itself.

---

## Embedding from the web UI

On any Similarity view page, click **Embed** to run `imh embed` for the
current album directly from the browser. At the virtual top-level album in a
multi-root setup, **Embed** runs once for each configured real root. Progress
is streamed line by line. Click **Cancel** to stop; already-completed batches
are saved and will be skipped on the next run.

This is equivalent to running `imh embed <album-path>` from the command line,
or one `imh embed` run per configured root for the multi-root virtual top
level.
