# Image Comparison — Theory

Background for the dedup pipeline: how neural embeddings work, why cosine
similarity is used, how clustering operates, and what the quality metrics
measure. For tuning guidance see `imhandler-selection-tuning.md`; for API
and command options see `imhandler-specs.md` and `imhandler-imh-man.md`.

---

## The problem

Finding duplicate or near-duplicate images in a large collection is hard to
do with metadata alone. File names and timestamps are unreliable; file size
changes with re-encoding; even a hash is useless once a single pixel
changes. A useful deduplication system must work from pixel content, and it
must tolerate the kinds of transformations that appear in real collections:
crops, colour adjustments, JPEG recompression at different quality settings,
minor reframing, and resizing.

The pipeline's answer is to map each image to a point in a high-dimensional
metric space where nearby points correspond to visually similar images,
regardless of the specific transformations applied. These points are called
*embeddings*.

---

## Neural image embeddings

### What an embedding is

An image embedding is the output of a neural network that has been trained so
that images of similar content produce output vectors that point in roughly
the same direction. The network is a function f: Image → ℝ^d where d is the
embedding dimension (512 for both models used here). After L2-normalisation —
dividing each vector by its Euclidean magnitude — every embedding lies on the
unit hypersphere S^(d-1).

Working on the unit hypersphere has a convenient property: the cosine of the
angle between two vectors equals their dot product (since |u| = |v| = 1,
u·v = cos θ). Cosine similarity, ranging from –1 to 1, then directly
measures the angle between embeddings — a single intuitive quantity that
captures how similar two images are, regardless of any scale differences in
the raw network output.

The two models in use are CLIP ViT-B/32 and SSCD disc_mixup. They are
complementary: CLIP is sensitive to semantic content; SSCD is sensitive to
visual structure and pixel-level similarity.

---

## CLIP ViT-B/32

### Architecture

CLIP (Radford et al., 2021, OpenAI) pairs a vision encoder with a text
encoder and trains them jointly. The vision encoder used here is ViT-B/32 —
a Vision Transformer (Dosovitskiy et al., 2020) with base-scale capacity and
32×32 pixel patches.

A 224×224 input is divided into a 7×7 grid of non-overlapping 32×32 patches.
Each patch is linearly projected to a 768-dimensional vector, producing 49
patch tokens. A special `[CLS]` token is prepended; the full 50-token sequence
is processed by 12 layers of multi-head self-attention (12 heads, 768 hidden
dim). The `[CLS]` token's final-layer output is projected to a 512-d embedding
and L2-normalised.

### Training objective

CLIP was trained on approximately 400 million (image, text) pairs collected
from the internet. The objective is contrastive: given a batch of N pairs,
the model maximises the cosine similarity of matched (image, text) pairs while
minimising it for the N² − N unmatched pairs. This InfoNCE loss treats each
matched pair as the positive example and all other images and texts in the
batch as negatives.

### What it captures

Because the model learns to match images to their captions, the embedding
encodes semantic content: the subject, scene type, activity, and notable
objects. Images of the same person in different lighting, the same beach from
different angles, or the same dish on different plates will cluster together.

What it does not capture: CLIP is largely insensitive to low-level pixel
differences — JPEG quality, colour grading, moderate crops, and resizing.
Two images that look different but describe the same scene will score high
similarity; two images that are pixel-near-identical but show different
subjects will score low. This makes it the better choice for finding
thematic duplicates across a large collection.

---

## SSCD disc_mixup

### Architecture

SSCD (Pizzi et al., 2022, Meta/Facebook Research) uses a ResNet-50 backbone
with Generalised Mean (GeM) pooling replacing the standard average pool. A
fully-connected head projects to a 512-d space, L2-normalised. The disc_mixup
variant was trained on the DISC (DISCovery of Copies) benchmark dataset with
Mixup regularisation, which interpolates between training examples and their
labels to improve generalisation.

### Training objective

SSCD is self-supervised: no text labels. Training generates positive pairs by
applying aggressive augmentations to the same source image:

- Random crops retaining as little as 50% of the area
- Horizontal flip
- Colour jitter (brightness, contrast, saturation, hue)
- Random grayscale conversion
- Gaussian blur
- JPEG recompression at quality 30–95

Each image and its augmented versions are treated as positive pairs (they
should be similar); all other images in the batch are negatives. The model is
trained to produce near-identical embeddings for all augmented versions of the
same source, and well-separated embeddings for different sources.

### What it captures

Because the model sees heavily cropped, colour-shifted, and JPEG-degraded
versions of the same image as positives, it learns to produce stable embeddings
under those transformations. A cropped copy, a colour-graded version, or a
re-saved JPEG of the same original will map close together in embedding space.

What it does not capture: SSCD pays less attention to semantic content at the
level CLIP operates on. Two images of the same subject taken from different
angles, with different framing and lighting, may score low SSCD similarity
even though CLIP would correctly identify them as related.

### Choosing between them

The two models are complementary. CLIP clusters tend to be broader and more
semantically driven; SSCD clusters tend to be tighter and more copy-specific.
Running both and inspecting reports independently gives the fullest picture.
For a collection that primarily contains edits, crops, and re-saves of the
same originals, SSCD will surface more actionable clusters. For a collection
where the concern is multiple shots of the same subject, CLIP is more useful.

---

## Cosine similarity

For two L2-normalised vectors u, v ∈ ℝ^512:

    cosine_similarity(u, v) = u · v = cos θ

where θ is the angle between them on the unit hypersphere. The range is
[−1, 1]. For image embeddings from both CLIP and SSCD, values below zero are
uncommon; in practice the working range is [0.5, 1.0] for interesting pairs.

Some intuition for threshold values:

| Threshold | Angle between embeddings | Typical meaning |
|-----------|--------------------------|-----------------|
| 0.75 | ~41° | Same broad subject; loose match |
| 0.85 | ~32° | Similar images; good default starting point |
| 0.90 | ~26° | Clearly related; copies or near-duplicates |
| 0.95 | ~18° | Near-identical; tight copies |
| 0.99 | ~8° | Essentially the same image |

These are approximate and model-dependent. A threshold of 0.85 with CLIP
corresponds to a looser visual relationship than 0.85 with SSCD, because SSCD
was trained to be a more discriminating copy detector.

Cosine similarity is preferred over Euclidean distance for embeddings because
it is scale-invariant: only the direction of the vector matters, not its
magnitude. This is directly enforced by L2-normalisation, and it means the
threshold has a stable geometric interpretation across different images and
collection sizes.

---

## Clustering by connected components

### Graph construction

Given n embeddings, compute the n×n pairwise cosine similarity matrix
S = E · Eᵀ (a single matrix multiply for L2-normalised embeddings E). Apply
the threshold to produce a boolean adjacency matrix A where A[i,j] = 1 iff
S[i,j] ≥ threshold. This defines an undirected graph where each node is an
image and each edge means "these two images are similar enough to be linked."

### Connected components

A connected component is a maximal set of nodes where every node can be
reached from every other node by traversing edges. Extracting components is
O(n + e) (BFS or DFS), where e is the number of edges — fast even for large
graphs. Components of size 1 (singletons — images with no similar peer above
the threshold) are discarded, since they represent images that are unique in
the collection.

### Transitivity

Connected components implement *single-linkage* semantics: if image A is
similar to B, and B is similar to C, then A, B, and C are in the same cluster
even if A and C do not exceed the threshold themselves. This transitivity is
usually desirable — it correctly groups chains of sequential edits — but at
low thresholds it can cause *chaining*: loosely-related images get pulled into
the same cluster because each is similar to some intermediate.

Chaining is the main reason to raise the threshold when clusters appear
over-broad. It is not a flaw in the algorithm — it is an inherent property of
single-linkage clustering — and it is why SSCD (with its tighter embeddings)
is less prone to it than CLIP at the same threshold.

### Why not k-means or DBSCAN?

*k-means* requires specifying k, the number of clusters, in advance. The
number of duplicate groups in a photo collection is unknown and highly
variable; k-means is not appropriate.

*DBSCAN* avoids specifying k but adds a density parameter (minPts) on top of
the distance parameter. For sparse duplicate clusters — where most images
have at most one or two near-duplicates — this extra parameter adds complexity
without benefit.

*Agglomerative clustering* with single linkage produces a full dendrogram,
allowing the threshold to be varied post-hoc without recomputing distances.
This is worth considering for very large collections, but its O(n² log n)
complexity is higher than the O(n²) matrix multiply + O(n) component
extraction used here.

---

## Quality metrics

The four quality metrics are computed from pixel data using classical signal
processing — no neural model, no GPU required. They serve one purpose:
ranking images *within* a cluster to identify which is the best copy.

### Laplacian variance

The Laplacian operator ∇² measures the second spatial derivative of intensity.
Its discrete approximation for a 2D image I is convolution with the kernel:

    [ 0  1  0 ]
    [ 1 -4  1 ]
    [ 0  1  0 ]

This kernel produces a large response at edges and fine texture (regions of
rapid intensity change) and near-zero response in smooth regions. In a blurry
or out-of-focus image, high-frequency spatial structure is attenuated, so the
Laplacian response is uniformly small.

The *variance* of the Laplacian response image measures how much of this
high-frequency structure is present overall. It is normalised by pixel count
so that images of different resolutions are comparable:

    laplacian_score = Var(∇²I) / (width × height)

High values indicate sharp, detailed images. Low values indicate blurry or
out-of-focus images.

**Content dependence**: the metric is inherently sensitive to image content.
A sharp portrait against a smooth, blurred background will score lower than
an in-focus landscape with leaf texture, not because it is less sharp, but
because there is genuinely less high-frequency content. Fog, smooth skies,
and plain backgrounds all reduce the score. This makes `laplacian_score`
most reliable when comparing images of similar subject matter — which is
exactly the intended use case (ranking within a cluster of similar images).

### HF power ratio

The 2D discrete Fourier transform (DFT) of the luminance channel decomposes
an image into its spatial frequency components. The power spectrum
P(u, v) = |F(u, v)|² gives the energy at each spatial frequency (u, v).

For natural photographic images, the power spectrum follows an approximate
*1/f²* law: P ∝ 1/(u² + v²). This is a statistical regularity of natural
scenes, related to their approximate scale invariance. Most energy concentrates
at low frequencies (the central region of the spectrum), and power falls off
smoothly toward high frequencies.

The HF power ratio measures the fraction of total power that lies *outside*
the central quarter of the frequency spectrum:

    hf_power_ratio = P(outside central quarter) / P(total)

where the "central quarter" is the box |u| ≤ W/4, |v| ≤ H/4.

A natively-captured image has a smooth power spectrum with a gradual falloff
at high frequencies, giving a moderate HF ratio. An image that has been
upscaled from a lower resolution has its high-frequency content supplied by
the interpolation kernel: bilinear or bicubic upscaling produces smoothly
interpolated values that contain no information above the original Nyquist
frequency. The power spectrum shows an abrupt energy drop-off at that
frequency, producing a lower HF ratio than a camera-captured image of the
same subject at the same output resolution.

This makes `hf_power_ratio` most useful as a detector of upscaled or heavily
low-pass-filtered images — images that look sharp at a glance but are
actually lower-resolution than their pixel dimensions suggest.

**Limitation**: the metric cannot distinguish "soft because the subject is
genuinely smooth" from "soft because the image was interpolated." It is a
supporting signal, not a standalone quality measure.

### Blocking score

JPEG compression divides the image into non-overlapping 8×8 pixel blocks and
applies the Discrete Cosine Transform (DCT) to each block independently.
Quantisation — the step where quality loss occurs — rounds each DCT
coefficient to the nearest multiple of a quality-dependent step size. At low
quality settings the step size is large, meaning coefficients are rounded
coarsely and the reconstructed values at block boundaries from adjacent blocks
may be significantly mismatched. This produces the characteristic grid of
visible lines at 8-pixel intervals known as *blocking artifacts*.

The blocking score is the ratio of mean boundary-difference variance to mean
within-block intensity variance:

    For each row/column at an 8-pixel boundary:
        collect Var(pixel[boundary] − pixel[boundary−1])   → boundary variances
    For each 8×8 block:
        collect Var(pixel intensities within block)         → within variances

    blocking_score = mean(boundary variances) / mean(within variances)

A clean image has similar magnitudes for both terms, so the score is near
1.0. A heavily blocked image has large, erratic differences along block
seams, elevating the boundary term and pushing the score above 1.0.

This is the *most content-independent* of the four metrics. The 8-pixel grid
pattern is an artifact of the codec, not the image content. A clean image of
any subject at any resolution should score near 1.0.

### Sharpness consistency

The `laplacian_score` measures overall sharpness, but does not distinguish
between "uniformly blurry everywhere" and "sharp in one area, blurry in
another." `sharpness_consistency` detects the latter.

The image is divided into non-overlapping tiles sized ¼ × image height by
¼ × image width (minimum 16×16 pixels). The Laplacian variance is computed
within each tile independently, yielding a distribution of per-tile sharpness
values. The *coefficient of variation*
(CV = σ/μ) of this distribution measures how spatially uniform the sharpness
is:

    sharpness_consistency = std(per-tile Laplacian variance) / mean(per-tile Laplacian variance)

High values (large CV) indicate that some parts of the image are much sharper
than others — for example, partial motion blur on a moving subject, or uneven
JPEG recompression applied only to part of the image. Low values indicate
uniform sharpness across the frame.

**Artistic depth of field**: intentional shallow depth-of-field photography
(portraits with bokeh, macro work) produces high sharpness consistency scores
even though the out-of-focus areas are deliberate. The metric reliably detects
*unintended* spatial degradation; it should be given low weight in collections
with significant portrait or macro work.

---

## quality_tier design

The four metrics are on different scales and have different sensitivities to
content. Rather than combining them into a single continuous score (which
would require careful weighting and would be hard to interpret), they are
combined into a three-level tier: **0 = clean**, **1 = degraded**,
**2 = heavily degraded**.

Each metric contributes an integer partial score toward the tier:

| Condition | Contribution |
|-----------|--------------|
| `laplacian_score < lap_lo` | +2 |
| `laplacian_score < lap_hi` | +1 |
| `hf_power_ratio < hf_lo` | +1 |
| `blocking_score > block_hi` | +1 |
| `sharpness_consistency > sc_hi` | +1 |

The total is clamped to 2. The two `laplacian_score` checks are independent:
a very low laplacian can contribute +2 directly; a moderately low one
contributes +1. The cap prevents any single metric from forcing a clean image
to be classified as heavily degraded.

Within a cluster, members are ranked by `(quality_tier ASC,
laplacian_score DESC)`. The tier provides coarse triage; the laplacian score
breaks ties within a tier, favouring sharper images.

The tier thresholds have no universal optimal values — they depend on the
subject matter and the camera. See `imhandler-selection-tuning.md` for
guidance on calibrating them for a specific collection.
