# imh — Implementation Notes

Internal behaviour of the `imh` command. See `imhandler-imh-man.md` for
usage and `imhandler-specs.md` for the library API.

---

## Entry point

`bin/imh` is a standalone Python script. It parses the
top-level `-q` / `--qat` flag, then dispatches on the subcommand name to
a handler function. Each handler constructs an `AppConfig`, calls
`appconfig.init()`, and invokes the appropriate library functions.

Config is loaded from `etc/imhandler.conf` in the grove repo. The `-q` flag
switches the variant key from `hty7` to `qat`, selecting the
`[qat.imhandler.core]` section instead of `[hty7.imhandler.core]`.

---

## Scanner — Leaf/Interior Distinction

`scanner.scan()` classifies each directory as either an interior node or a leaf
node. A directory that contains subdirectories is interior; its images are
ignored. A directory with no subdirectories is a leaf; its images are included.
This means only the deepest level of each branch contributes images to the album
tree.

---

## imh list

Calls `scanner.scan(root)` and either walks the resulting `Album` tree for
`--tree` output or calls `filter_and_sort(album.all_images(), ...)` for flat
output.

`--tree` is rendered by a recursive function that prints each album with its
`image_count()` in parentheses, indented by depth. `--glob` and `--sort` are
not applied in tree mode.

---

## imh thumb

Thumbnail storage path:

```
cache_dir/thumbs/<xx>/<sha256>-<size>.jpg
```

`<sha256>` is the hex SHA-256 of the absolute image path string (UTF-8
encoded). `<xx>` is the first two hex characters of the digest, used as a
bucket directory to avoid large flat directories.

Cache invalidation: a thumbnail is considered stale if its `mtime` is older
than the source image's `mtime`. Stale thumbnails are regenerated in place;
the destination file is overwritten atomically (write to a temp file in the
same directory, then `os.replace`).

Errors are collected, reported to stderr, and written to
`cache_dir/logs/thumb-errors-<ISO-timestamp>.log` (one path per line). The
log file is only created if at least one error occurred. Exit status is 1 if
the error list is non-empty.

---

## imh purge

Walks `cache_dir/thumbs/` and, for each `.jpg` file found, extracts the
`<sha256>` from the filename, then checks whether any current image in `DIR`
hashes to that SHA-256. Thumbnails with no matching source are deleted.

Separately, calls `db.cleanup_missing_members()` for every cluster in the
database to remove records for images that no longer exist on disk.

---

## imh embed

**Skipping**: an image is skipped if the database already contains a row with
matching `(path, mtime)` that has all requested embeddings (`clip_embedding`
and/or `sscd_embedding`) non-null. A partial row (e.g., only CLIP present
when `--model both` was requested) triggers re-processing of the missing
embedding only.

**Batching**: images are grouped into batches of `--batch-size`. Each batch
is processed by the neural model, then committed to the database in a single
transaction. This makes Ctrl-C interruption safe: the next run resumes from
the first unprocessed image.

**Model weights**:
- CLIP ViT-B/32: downloaded via `open_clip` / HuggingFace Hub (~605 MB),
  cached at `cache_dir/weights/clip-vit-b-32/`.
- SSCD disc_mixup: TorchScript checkpoint from
  `dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_mixup.torchscript.pt`
  (~90 MB), cached at `cache_dir/weights/sscd_disc_mixup.torchscript.pt`.

**Authentication**: Hugging Face downloads check for an `HF_TOKEN` using the
following fallback chain: `~/etc/imhandler-keys.json` (key `"HF_TOKEN"`),
then the `HF_TOKEN` environment variable. If neither is found, downloads
proceed without authentication.

**Quality tier scoring**: see `imhandler-theory.md` for the scoring formula
and what each metric measures.

---

## imh cluster

1. Load all L2-normalised embeddings for the requested model from the
   database into a numpy matrix `E` of shape `(n, 512)`.
2. Compute the full pairwise cosine similarity matrix: `S = E @ E.T`.
3. Threshold to a boolean adjacency matrix: `A = S >= threshold`.
4. Extract connected components using `scipy.sparse.csgraph.connected_components`.
5. Discard components of size 1 (singletons).
6. Rank members within each component: sort by `(quality_tier ASC,
   laplacian_score DESC)` to produce `quality_rank` values (0 = best).
7. Delete any existing `Clusters` and `ClusterMembership` rows with the same
   `(model_used, threshold_used)`, then insert the new results.

The matrix multiply in step 2 is O(n²) in both time and memory. For
collections larger than ~100 k images this will be slow and memory-intensive;
tiled or approximate NN approaches are not yet implemented.

---

## imh report

Calls `db.get_clusters(conn, model=model, threshold=threshold)`, then for
each cluster calls `db.get_cluster_members(conn, cluster_id)` and formats
the output line by line. The `*` marker is placed on the member with
`quality_rank == 0`. Width/height come from the `Images` table row stored
at embed time.

If `-o FILE` is given, output is written to that file; otherwise to stdout.
