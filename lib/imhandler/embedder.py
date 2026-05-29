"""Embedding computation and quality metrics for the image dedup pipeline.

Public API:
    compute_quality_metrics(img) -> dict
    embed_images(root, conn, *, model, batch_size, weights_dir, tier_thresholds) -> (processed, skipped)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import convolve  # type: ignore[import-untyped]

from .cache import weights_dir as _default_weights_dir
from .scanner import scan


_LAPLACIAN_KERNEL = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
_CLIP_TEXT_MODEL_CACHE: dict[str, object] = {}
_CLIP_TEXT_MODEL_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Quality metrics
# ---------------------------------------------------------------------------

def compute_quality_metrics(
    img: Image.Image,
    *,
    lap_lo: float = 0.0005,
    lap_hi: float = 0.002,
    hf_lo: float = 0.65,
    block_hi: float = 2.0,
    sc_hi: float = 1.5,
) -> dict[str, float | int]:
    """Compute quality metrics for a PIL image.

    Returns a dict with keys:
        laplacian_score, hf_power_ratio, blocking_score,
        sharpness_consistency, quality_tier
    """
    gray = np.array(img.convert('L')).astype(np.float32)
    h, w = gray.shape

    # laplacian_score: Laplacian variance normalised by pixel count
    lap = convolve(gray, _LAPLACIAN_KERNEL)
    laplacian_score = float(lap.var()) / (h * w)

    # hf_power_ratio: high-frequency FFT power / total power (Y channel)
    ycbcr = np.array(img.convert('YCbCr'))
    y_chan = ycbcr[:, :, 0].astype(np.float32)
    F = np.fft.fftshift(np.fft.fft2(y_chan))
    power = np.abs(F) ** 2
    cy, cx = h // 2, w // 2
    rh, rw = max(h // 4, 1), max(w // 4, 1)
    lf_mask = np.zeros((h, w), dtype=bool)
    lf_mask[max(0, cy - rh):cy + rh, max(0, cx - rw):cx + rw] = True
    total_power = float(power.sum())
    hf_power_ratio = float(power[~lf_mask].sum()) / total_power if total_power > 0 else 0.0

    # blocking_score: variance at 8-pixel boundaries / within-block variance
    bnd_diffs: list[float] = []
    for y0 in range(8, h, 8):
        bnd_diffs.append(float(np.var(gray[y0, :] - gray[y0 - 1, :])))
    for x0 in range(8, w, 8):
        bnd_diffs.append(float(np.var(gray[:, x0] - gray[:, x0 - 1])))
    within_vars: list[float] = []
    for y0 in range(0, h - 8, 8):
        for x0 in range(0, w - 8, 8):
            within_vars.append(float(np.var(gray[y0:y0 + 8, x0:x0 + 8])))
    mean_bnd = float(np.mean(bnd_diffs)) if bnd_diffs else 0.0
    mean_within = float(np.mean(within_vars)) if within_vars else 1.0
    blocking_score = mean_bnd / mean_within if mean_within > 0 else 0.0

    # sharpness_consistency: coefficient of variation of per-tile Laplacian variance
    tile_h = max(h // 4, 16)
    tile_w = max(w // 4, 16)
    tile_vars: list[float] = []
    for y0 in range(0, h - tile_h, tile_h):
        for x0 in range(0, w - tile_w, tile_w):
            tile_vars.append(float(np.var(lap[y0:y0 + tile_h, x0:x0 + tile_w])))
    if len(tile_vars) > 1:
        mean_tv = float(np.mean(tile_vars))
        sharpness_consistency = float(np.std(tile_vars)) / (mean_tv + 1e-9)
    else:
        sharpness_consistency = 0.0

    # quality_tier: 0=clean, 1=degraded, 2=heavily degraded
    score = 0
    if laplacian_score < lap_lo:
        score += 2
    elif laplacian_score < lap_hi:
        score += 1
    if hf_power_ratio < hf_lo:
        score += 1
    if blocking_score > block_hi:
        score += 1
    if sharpness_consistency > sc_hi:
        score += 1
    quality_tier = min(score, 2)

    return {
        'laplacian_score': laplacian_score,
        'hf_power_ratio': hf_power_ratio,
        'blocking_score': blocking_score,
        'sharpness_consistency': sharpness_consistency,
        'quality_tier': quality_tier,
    }


# ---------------------------------------------------------------------------
# Token and configuration helpers
# ---------------------------------------------------------------------------

def _get_hf_token() -> str | None:
    """Get Hugging Face token with fallback chain.

    1. Try ~/etc/imhandler-keys.json with key "HF_TOKEN"
    2. Fall back to HF_TOKEN environment variable
    3. Return None if neither found
    """
    # Try JSON file first
    key_file = Path('~').expanduser() / 'etc' / 'imhandler-keys.json'
    if key_file.exists():
        try:
            with open(key_file) as f:
                data = json.load(f)
                token = data.get('HF_TOKEN')
                if token:
                    return token
        except (OSError, json.JSONDecodeError):
            pass

    # Fall back to environment variable
    return os.environ.get('HF_TOKEN')


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def load_clip_model(weights_dir: Path) -> tuple:
    """Load CLIP ViT-B/32. Returns (model, preprocess).

    Sets HF_HOME so weights land under weights_dir.
    Sets HF_TOKEN if available from config or environment.
    """
    import open_clip  # type: ignore[import-untyped]

    os.environ.setdefault('HF_HOME', str(weights_dir))
    os.environ.setdefault('HUGGINGFACE_HUB_CACHE', str(weights_dir / 'hub'))

    token = _get_hf_token()
    if token:
        os.environ['HF_TOKEN'] = token

    model, _, preprocess = open_clip.create_model_and_transforms(
        'ViT-B-32', pretrained='openai'
    )
    model.eval()
    return model, preprocess


def _load_clip_text_model(weights_dir: Path):
    """Load and cache the CLIP model for text queries."""
    cache_key = str(weights_dir.resolve())
    with _CLIP_TEXT_MODEL_LOCK:
        cached = _CLIP_TEXT_MODEL_CACHE.get(cache_key)
        if cached is not None:
            return cached
        model, _ = load_clip_model(weights_dir)
        _CLIP_TEXT_MODEL_CACHE[cache_key] = model
        return model


_SSCD_URL = (
    'https://dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_mixup.torchscript.pt'
)


def load_sscd_model(weights_dir: Path):
    """Load the SSCD TorchScript checkpoint, downloading it if absent.

    Uses the pre-built TorchScript export from Facebook's CDN rather than
    torch.hub, which avoids the fragile zip-extraction path in torch.hub.
    The model outputs 512-dim L2-normalised embeddings.
    """
    import torch  # type: ignore[import-untyped]
    import urllib.request

    weights_dir.mkdir(parents=True, exist_ok=True)
    model_path = weights_dir / 'sscd_disc_mixup.torchscript.pt'
    tmp_path = model_path.with_suffix('.pt.tmp')

    if not model_path.exists():
        tmp_path.unlink(missing_ok=True)
        last_pct: list[int] = [-1]

        def _reporthook(count: int, block_size: int, total_size: int) -> None:
            if total_size <= 0:
                return
            pct = min(100, count * block_size * 100 // total_size)
            if pct >= last_pct[0] + 5:
                last_pct[0] = pct
                print(f'Downloading SSCD weights… {pct}%', flush=True)

        print(f'Downloading SSCD weights → {model_path}', flush=True)
        urllib.request.urlretrieve(_SSCD_URL, tmp_path, reporthook=_reporthook)
        tmp_path.rename(model_path)
        print('Download complete.', flush=True)

    model = torch.jit.load(str(model_path), map_location='cpu')
    model.eval()
    return model


# ---------------------------------------------------------------------------
# Batch embedding helpers
# ---------------------------------------------------------------------------

def _embed_clip_batch(
    images: list[Image.Image], model, preprocess
) -> np.ndarray:
    import torch  # type: ignore[import-untyped]

    tensors = torch.cat([
        preprocess(img.convert('RGB')).unsqueeze(0) for img in images
    ])
    with torch.no_grad():
        features = model.encode_image(tensors)
        features = features / features.norm(dim=-1, keepdim=True)
    return features.cpu().numpy().astype(np.float32)


def _sscd_preprocess(img: Image.Image) -> 'torch.Tensor':  # type: ignore[name-defined]
    """Resize to 288×288 centre-crop and normalise (ImageNet stats)."""
    import torch  # type: ignore[import-untyped]

    img = img.convert('RGB')
    w, h = img.size
    scale = 288 / min(w, h)
    new_w = int(w * scale + 0.5)
    new_h = int(h * scale + 0.5)
    img = img.resize((new_w, new_h), Image.Resampling.BICUBIC)
    w, h = img.size
    left = (w - 288) // 2
    top = (h - 288) // 2
    img = img.crop((left, top, left + 288, top + 288))
    arr = np.array(img).astype(np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    arr = (arr - mean) / std
    return torch.from_numpy(arr.transpose(2, 0, 1))


def _embed_sscd_batch(images: list[Image.Image], model) -> np.ndarray:
    import torch  # type: ignore[import-untyped]

    tensors = torch.stack([_sscd_preprocess(img) for img in images])
    with torch.no_grad():
        features = model(tensors)
        features = features / features.norm(dim=-1, keepdim=True)
    return features.cpu().numpy().astype(np.float32)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def embed_images(
    root: Path | str,
    conn: sqlite3.Connection,
    *,
    model: str = 'both',
    batch_size: int = 8,
    weights_dir: Path | None = None,
    tier_thresholds: dict[str, float] | None = None,
    cancel=None,
    on_progress=None,
) -> tuple[int, int]:
    """Scan root, compute embeddings and quality metrics, upsert into conn.

    model: 'clip', 'sscd', or 'both'
    Returns (processed, skipped).
    Images are keyed by (path, mtime): unchanged files are skipped unless
    they are missing an embedding requested by this run.
    """
    if weights_dir is None:
        weights_dir = _default_weights_dir()
    weights_dir.mkdir(parents=True, exist_ok=True)

    album = scan(root)
    all_entries = album.all_images()
    if not all_entries:
        return 0, 0

    do_clip = model in ('clip', 'both')
    do_sscd = model in ('sscd', 'both')
    tier_kw: dict[str, float] = tier_thresholds or {}

    # Decide what needs doing per entry
    todo: list[tuple] = []  # (entry, need_clip, need_sscd)
    for entry in all_entries:
        row = conn.execute(
            'SELECT clip_embedding, sscd_embedding FROM Images WHERE path=? AND mtime=?',
            (str(entry.path), entry.mtime),
        ).fetchone()
        need_clip = do_clip and (row is None or row['clip_embedding'] is None)
        need_sscd = do_sscd and (row is None or row['sscd_embedding'] is None)
        if row is None or need_clip or need_sscd:
            todo.append((entry, need_clip, need_sscd))

    skipped = len(all_entries) - len(todo)
    if not todo:
        return 0, skipped

    # Load models lazily
    clip_model = clip_prep = sscd_model = None
    if any(nc for _, nc, _ in todo):
        print('Loading CLIP model…', flush=True)
        clip_model, clip_prep = load_clip_model(weights_dir)
    if any(ns for _, _, ns in todo):
        print('Loading SSCD model…', flush=True)
        sscd_model = load_sscd_model(weights_dir)

    processed = 0
    total = len(todo)
    last_pct: int = -1
    current_dir: str | None = None
    root_path = Path(root).resolve()

    current_label = ''

    dir_totals: dict[str, int] = {}
    for _entry, _, _ in todo:
        _key = str(_entry.path.parent)
        dir_totals[_key] = dir_totals.get(_key, 0) + 1
    dir_done: dict[str, int] = {}
    last_progress_time: float = 0.0

    for i in range(0, total, batch_size):
        if cancel is not None and cancel.is_set():
            print('Cancelled.', flush=True)
            break

        chunk: list[tuple] = todo[i:i + batch_size]

        # Directory change
        first_dir = str(chunk[0][0].path.parent)
        if first_dir != current_dir:
            current_dir = first_dir
            try:
                rel = str(chunk[0][0].path.parent.relative_to(root_path))
            except ValueError:
                rel = first_dir
            current_label = rel if rel != '.' else root_path.name
            print(current_label, flush=True)
            last_progress_time = 0.0

        now = time.monotonic()
        if now - last_progress_time >= 5.0:
            done_here = dir_done.get(current_dir, 0)  # type: ignore[arg-type]
            total_here = dir_totals.get(current_dir, 0)  # type: ignore[arg-type]
            print(f'  {done_here}/{total_here}', flush=True)
            last_progress_time = now

        # Progress at batch start (before inference, so it appears immediately)
        if on_progress is not None:
            pct = i * 100 // total
            if pct > last_pct:
                last_pct = pct
                on_progress(pct, current_label)

        # Load images, collecting failures
        loaded: list[tuple] = []  # (entry, img, need_clip, need_sscd)
        for entry, need_clip, need_sscd in chunk:
            try:
                img = Image.open(entry.path)
                img.load()
                loaded.append((entry, img, need_clip, need_sscd))
            except Exception as exc:
                print(f'warning: {entry.path}: {exc}', file=sys.stderr)

        if not loaded:
            continue

        imgs = [t[1] for t in loaded]

        # Quality metrics (per-image, fast)
        metrics_list: list[dict[str, float | int] | None] = []
        for entry, img, _, _ in loaded:
            try:
                metrics_list.append(compute_quality_metrics(img, **tier_kw))
            except Exception as exc:
                print(f'warning: metrics {entry.path}: {exc}', file=sys.stderr)
                metrics_list.append(None)

        # CLIP embeddings (batched)
        clip_arr: np.ndarray | None = None
        if clip_model is not None and any(t[2] for t in loaded):
            try:
                clip_arr = _embed_clip_batch(imgs, clip_model, clip_prep)
            except Exception as exc:
                print(f'warning: clip batch [{i}]: {exc}', file=sys.stderr)

        # SSCD embeddings (batched)
        sscd_arr: np.ndarray | None = None
        if sscd_model is not None and any(t[3] for t in loaded):
            try:
                sscd_arr = _embed_sscd_batch(imgs, sscd_model)
            except Exception as exc:
                print(f'warning: sscd batch [{i}]: {exc}', file=sys.stderr)

        # Upsert each image
        for k, (entry, img, need_clip, need_sscd) in enumerate(loaded):
            path_str = str(entry.path)
            metrics = metrics_list[k]
            img_w, img_h = img.size

            clip_blob = clip_arr[k].tobytes() if (clip_arr is not None and need_clip) else None
            sscd_blob = sscd_arr[k].tobytes() if (sscd_arr is not None and need_sscd) else None

            existing = conn.execute(
                'SELECT id FROM Images WHERE path=? AND mtime=?',
                (path_str, entry.mtime),
            ).fetchone()

            if existing is None:
                conn.execute(
                    '''INSERT OR IGNORE INTO Images
                       (path, mtime, width, height,
                        clip_embedding, sscd_embedding,
                        laplacian_score, hf_power_ratio, blocking_score,
                        sharpness_consistency, quality_tier)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (
                        path_str, entry.mtime, img_w, img_h,
                        clip_blob, sscd_blob,
                        metrics['laplacian_score'] if metrics else None,
                        metrics['hf_power_ratio'] if metrics else None,
                        metrics['blocking_score'] if metrics else None,
                        metrics['sharpness_consistency'] if metrics else None,
                        metrics['quality_tier'] if metrics else None,
                    ),
                )
            else:
                if clip_blob is not None:
                    conn.execute(
                        'UPDATE Images SET clip_embedding=? WHERE id=?',
                        (clip_blob, existing['id']),
                    )
                if sscd_blob is not None:
                    conn.execute(
                        'UPDATE Images SET sscd_embedding=? WHERE id=?',
                        (sscd_blob, existing['id']),
                    )

            dir_done[str(entry.path.parent)] = dir_done.get(str(entry.path.parent), 0) + 1
            processed += 1

        conn.commit()

        if on_progress is not None:
            pct = processed * 100 // total
            if pct > last_pct:
                last_pct = pct
                on_progress(pct, current_label)

    return processed, skipped


def find_similar(
    conn: sqlite3.Connection,
    path: 'Path | str',
    model: str,
    *,
    n: int = 8,
) -> tuple[object | None, list[dict]]:
    """Find the n most similar images in the same directory as path.

    Returns (target_row, neighbors) where:
      - target_row is the sqlite3.Row for path (None if no embedding exists)
      - neighbors is a list of dicts with keys: path, similarity, width, height
        ordered by descending similarity, excluding the target itself
    """
    path = Path(path)
    emb_col = f'{model}_embedding'
    target_row = conn.execute(
        f'SELECT {emb_col}, width, height FROM Images WHERE path = ?',
        (str(path),),
    ).fetchone()

    if target_row is None or target_row[emb_col] is None:
        return target_row, []

    target_emb = np.frombuffer(target_row[emb_col], dtype=np.float32)
    dir_str = str(path.parent)
    rows = conn.execute(
        f'SELECT path, {emb_col}, width, height FROM Images '
        f'WHERE path LIKE ? AND {emb_col} IS NOT NULL AND path != ?',
        (dir_str + '/%', str(path)),
    ).fetchall()
    rows = [r for r in rows if Path(r['path']).parent == path.parent]

    if not rows:
        return target_row, []

    paths = [r['path'] for r in rows]
    embs = np.stack([np.frombuffer(r[emb_col], dtype=np.float32) for r in rows])
    sims = embs @ target_emb
    top_n = min(n, len(paths))
    top_indices = np.argsort(sims)[::-1][:top_n]

    neighbors = [
        {
            'path': paths[i],
            'similarity': round(float(sims[i]), 3),
            'width': rows[i]['width'],
            'height': rows[i]['height'],
        }
        for i in top_indices
    ]
    return target_row, neighbors


def find_semantic(
    conn: sqlite3.Connection,
    query: str,
    *,
    scope: 'Path | str | None' = None,
    n: int = 24,
    weights_dir: Path | None = None,
) -> tuple[list[dict], int]:
    """Find the n CLIP-nearest images for a text query."""
    if weights_dir is None:
        weights_dir = _default_weights_dir()

    query = query.strip()
    if not query:
        return [], 0

    scope_path = Path(scope).resolve() if scope is not None else None
    rows = conn.execute(
        'SELECT i.path, i.clip_embedding, i.width, i.height'
        ' FROM Images i'
        ' JOIN ('
        '   SELECT path, MAX(mtime) AS max_mtime'
        '   FROM Images'
        '   WHERE clip_embedding IS NOT NULL'
        '   GROUP BY path'
        ' ) latest'
        '   ON latest.path = i.path AND latest.max_mtime = i.mtime'
        ' WHERE i.clip_embedding IS NOT NULL'
    ).fetchall()
    if scope_path is not None:
        rows = [row for row in rows if Path(row['path']).is_relative_to(scope_path)]
    if not rows:
        return [], 0

    import open_clip  # type: ignore[import-untyped]
    import torch  # type: ignore[import-untyped]

    model = _load_clip_text_model(weights_dir)
    tokens = open_clip.tokenize([query])
    with torch.no_grad():
        features = model.encode_text(tokens)
        features = features / features.norm(dim=-1, keepdim=True)
    query_emb = features[0].cpu().numpy().astype(np.float32)

    embs = np.stack([np.frombuffer(row['clip_embedding'], dtype=np.float32) for row in rows])
    sims = embs @ query_emb
    top_n = min(n, len(rows))
    top_indices = np.argsort(sims)[::-1][:top_n]

    results = [
        {
            'path': rows[i]['path'],
            'similarity': round(float(sims[i]), 3),
            'width': rows[i]['width'],
            'height': rows[i]['height'],
        }
        for i in top_indices
    ]
    return results, len(rows)
