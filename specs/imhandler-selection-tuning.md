# Embeddings, Clustering, and Parameter Tuning

Practical guidance for running the dedup pipeline and adjusting parameters
when the defaults produce poor results. For the theory behind embeddings,
cosine similarity, and the quality metrics see `imhandler-theory.md`.

---

## Pipeline overview

`imh embed` runs each image through neural models and stores a 512-d vector
(embedding) per image in the database. This is the slow step; a GPU helps
but is not required. Re-running only processes images added since the last
run.

`imh cluster` reads the stored embeddings, groups images by similarity, and
writes clusters to the database. This takes seconds regardless of collection
size, so it can be re-run freely to explore different threshold values.

`imh report` reads the clusters and prints them. The best image in each
cluster is ranked first (marked `*`); the others are candidates for deletion.

---

## Which model to use

Run `--model both` on the first pass and keep both embeddings in the database.
Then cluster and report each independently:

```
imh embed ~/Photos --model both
imh cluster --model clip  && imh report --model clip  > clip-report.txt
imh cluster --model sscd  && imh report --model sscd  > sscd-report.txt
```

**Use CLIP results when**: the concern is multiple shots of the same subject,
event, or scene — different framings, different lighting, different moments.

**Use SSCD results when**: the concern is edited copies — crops, colour
adjustments, resized versions, re-saved JPEGs of the same original.

SSCD clusters are tighter and more specific; CLIP clusters are broader. If
you only run one model, CLIP is the better default for a general collection.

---

## Clustering threshold

The threshold controls how similar two images must be before they are linked
into the same cluster. Higher means stricter; lower means more permissive.

| Symptom | Action |
|---------|--------|
| Clusters contain images that are clearly different photos | Raise threshold by 0.03–0.05 |
| Known duplicates are not being grouped | Lower threshold by 0.03–0.05 |
| Large clusters pulling in distantly related images | Raise threshold; this is threshold-chaining |

Re-running `imh cluster` is instant and does not change the embeddings.
Different threshold values coexist in the database — run at several to
compare:

```
imh cluster --threshold 0.80 && imh report --threshold 0.80 > r80.txt
imh cluster --threshold 0.85 && imh report --threshold 0.85 > r85.txt
imh cluster --threshold 0.90 && imh report --threshold 0.90 > r90.txt
```

The right threshold is the highest one at which you stop losing real
duplicates. Working up from 0.80 in steps of 0.05 is a practical starting
procedure.

The default (0.85) is a reasonable starting point for CLIP on a natural
photograph collection. SSCD embeddings are more discriminating, so you may
need a slightly lower threshold (0.80–0.82) to catch copies with significant
edits.

---

## Quality metrics and tier thresholds

Quality metrics rank images *within* a cluster — they do not affect whether
two images are grouped together. Getting the ranking wrong means the wrong
image is pre-selected for deletion, but clusters themselves are unaffected.
This makes the thresholds lower-stakes than the clustering threshold.

The thresholds are passed to `imh embed`:

```
imh embed ~/Photos \
  --lap-lo 0.0005 \
  --lap-hi 0.002  \
  --hf-lo  0.65   \
  --block-hi 2.0  \
  --sc-hi  1.5
```

### Reading the report

The `lap=` value in `imh report` output is `laplacian_score`. Use it to
calibrate the thresholds:

1. Run `imh report` and find a cluster where the wrong image is ranked best.
2. Read off the `lap=` values for the sharp and blurry members.
3. If the blurry member has a higher `lap=` than the sharp one, the
   distinction must come from the other metrics (blocking, hf, consistency).
4. Adjust thresholds to make the blurry/degraded image score a higher tier
   than the sharp one.

### Adjusting individual thresholds

**`--lap-hi` / `--lap-lo`**: the most impactful thresholds. Run `imh report`
and note the `lap=` values for images you know are blurry. Set `--lap-hi`
just above the blurriest acceptable image; set `--lap-lo` to half of that.

If `laplacian_score` is systematically misleading for your collection (e.g.,
it penalises clean portraits against smooth backgrounds), lower these
thresholds so fewer images are flagged, or rely on the other three metrics.

**`--hf-lo`**: useful mainly for detecting upscaled images (artificially
sharpened or AI-upscaled at a resolution higher than the original capture).
If your collection contains no upscaled images, set `--hf-lo 0.0` to disable
this metric's contribution.

**`--block-hi`**: this metric is content-independent and reliable. The default
(2.0) flags images with significant JPEG blocking. If no images in your
collection are from low-quality JPEG sources, raise it to 5.0 to suppress
noise.

**`--sc-hi`**: penalises images where sharpness varies spatially. Set
`--sc-hi 100` to disable it entirely for collections with a lot of portrait
or macro work (intentional bokeh scores high here and should not be penalised).

### Disabling a metric

Set any threshold to an extreme value to prevent it from contributing:

```
--hf-lo 0.0        # disable HF ratio check
--block-hi 999.0   # disable blocking check
--sc-hi 100.0      # disable sharpness consistency check
```
