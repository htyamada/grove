"""Clustering pass for the image dedup pipeline.

Public API:
    cluster_images(conn, *, threshold, model) -> int
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

import numpy as np
from scipy.sparse import csr_matrix  # type: ignore[import-untyped]
from scipy.sparse.csgraph import connected_components  # type: ignore[import-untyped]


def _load_embeddings(
    conn: sqlite3.Connection, model: str
) -> tuple[list[int], np.ndarray]:
    """Return (image_ids, embedding_matrix) for rows that have the given embedding."""
    col = f'{model}_embedding'
    rows = conn.execute(
        f'SELECT id, {col} FROM Images WHERE {col} IS NOT NULL'
    ).fetchall()
    if not rows:
        return [], np.empty((0, 0), dtype=np.float32)
    ids = [r['id'] for r in rows]
    embs = np.stack([np.frombuffer(r[col], dtype=np.float32) for r in rows])
    return ids, embs


def _delete_existing(
    conn: sqlite3.Connection, threshold: float, model: str
) -> None:
    """Remove all clusters (and their memberships) with matching threshold+model."""
    old_ids = [
        r['id']
        for r in conn.execute(
            'SELECT id FROM Clusters WHERE threshold_used=? AND model_used=?',
            (threshold, model),
        ).fetchall()
    ]
    if old_ids:
        ph = ','.join('?' * len(old_ids))
        conn.execute(f'DELETE FROM ClusterMembership WHERE cluster_id IN ({ph})', old_ids)
        conn.execute(f'DELETE FROM Clusters WHERE id IN ({ph})', old_ids)


def cluster_images(
    conn: sqlite3.Connection,
    *,
    threshold: float = 0.85,
    model: str = 'clip',
) -> int:
    """Compute pairwise cosine similarity, extract connected components,
    rank members by quality, and write results to the database.

    Any existing clusters with the same threshold and model are replaced.
    Singleton components (no duplicates) are discarded.

    Returns the number of clusters written.
    """
    ids, embs = _load_embeddings(conn, model)
    if len(ids) < 2:
        _delete_existing(conn, threshold, model)
        conn.commit()
        return 0

    # Embeddings are L2-normalised: dot product == cosine similarity
    sim: np.ndarray = embs @ embs.T  # (N, N) float32

    adj = (sim >= threshold).astype(np.uint8)
    np.fill_diagonal(adj, 0)

    _, labels = connected_components(
        csr_matrix(adj), directed=False, return_labels=True
    )

    groups: dict[int, list[int]] = defaultdict(list)
    for idx, label in enumerate(labels):
        groups[label].append(idx)

    clusters = [g for g in groups.values() if len(g) > 1]

    _delete_existing(conn, threshold, model)

    if not clusters:
        conn.commit()
        return 0

    # Cache quality data for ranking (tier asc, sharpness desc → best first)
    id_to_quality: dict[int, tuple[int, float]] = {}
    for r in conn.execute(
        'SELECT id, quality_tier, laplacian_score FROM Images'
    ).fetchall():
        tier = r['quality_tier'] if r['quality_tier'] is not None else 2
        lap = r['laplacian_score'] if r['laplacian_score'] is not None else 0.0
        id_to_quality[r['id']] = (tier, lap)

    cur = conn.cursor()
    for group in clusters:
        cur.execute(
            'INSERT INTO Clusters (threshold_used, model_used) VALUES (?,?)',
            (threshold, model),
        )
        cluster_id = cur.lastrowid

        image_ids = [ids[i] for i in group]
        ranked = sorted(
            image_ids,
            key=lambda img_id: (
                id_to_quality.get(img_id, (2, 0.0))[0],      # tier asc
                -id_to_quality.get(img_id, (2, 0.0))[1],     # laplacian desc
            ),
        )
        for rank, img_id in enumerate(ranked):
            cur.execute(
                'INSERT INTO ClusterMembership (cluster_id, image_id, quality_rank)'
                ' VALUES (?,?,?)',
                (cluster_id, img_id, rank),
            )

    conn.commit()
    return len(clusters)
