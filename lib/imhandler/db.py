import sqlite3
from pathlib import Path
from typing import Iterable

from .cache import db_path


_DDL = [
    """
    CREATE TABLE IF NOT EXISTS Images (
        id                   INTEGER PRIMARY KEY,
        path                 TEXT NOT NULL,
        mtime                REAL NOT NULL,
        width                INTEGER,
        height               INTEGER,
        clip_embedding       BLOB,
        sscd_embedding       BLOB,
        laplacian_score      REAL,
        hf_power_ratio       REAL,
        blocking_score       REAL,
        sharpness_consistency REAL,
        quality_tier         INTEGER,
        UNIQUE(path, mtime)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS Clusters (
        id              INTEGER PRIMARY KEY,
        threshold_used  REAL NOT NULL,
        model_used      TEXT NOT NULL,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ClusterMembership (
        cluster_id    INTEGER NOT NULL REFERENCES Clusters(id),
        image_id      INTEGER NOT NULL REFERENCES Images(id),
        quality_rank  INTEGER,
        PRIMARY KEY (cluster_id, image_id)
    )
    """,
]


def open_db(path: Path | None = None) -> sqlite3.Connection:
    if path is None:
        path = db_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    except Exception as exc:
        raise type(exc)(f'{exc}: {path}') from exc
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    for ddl in _DDL:
        conn.execute(ddl)
    conn.commit()
    return conn


def get_clusters(conn: sqlite3.Connection, *, model: str | None = None,
                 threshold: float | None = None) -> list:
    """Return Clusters rows, optionally filtered by model and/or threshold."""
    q = 'SELECT id, threshold_used, model_used, created_at FROM Clusters'
    conditions: list[str] = []
    params: list = []
    if model is not None:
        conditions.append('model_used = ?')
        params.append(model)
    if threshold is not None:
        conditions.append('threshold_used = ?')
        params.append(threshold)
    if conditions:
        q += ' WHERE ' + ' AND '.join(conditions)
    q += ' ORDER BY created_at DESC, id'
    return conn.execute(q, params).fetchall()


def get_cluster_member_rows(conn: sqlite3.Connection, *, model: str | None = None,
                            threshold: float | None = None) -> list:
    """Return flat join of Clusters+ClusterMembership+Images, ordered by cluster id and rank."""
    q = '''
        SELECT c.id AS cluster_id, c.threshold_used, c.model_used, c.created_at,
               cm.quality_rank, i.path, i.width, i.height,
               i.laplacian_score, i.hf_power_ratio, i.blocking_score,
               i.sharpness_consistency, i.quality_tier
        FROM Clusters c
        JOIN ClusterMembership cm ON cm.cluster_id = c.id
        JOIN Images i ON i.id = cm.image_id
    '''
    conditions: list[str] = []
    params: list = []
    if model is not None:
        conditions.append('c.model_used = ?')
        params.append(model)
    if threshold is not None:
        conditions.append('c.threshold_used = ?')
        params.append(threshold)
    if conditions:
        q += ' WHERE ' + ' AND '.join(conditions)
    q += ' ORDER BY c.created_at DESC, c.id, cm.quality_rank'
    return conn.execute(q, params).fetchall()


def get_cluster_members(conn: sqlite3.Connection, cluster_id: int) -> list:
    """Return ClusterMembership+Images rows for one cluster, ordered by quality_rank."""
    return conn.execute(
        '''
        SELECT i.id AS image_id, i.path, i.width, i.height,
               i.laplacian_score, i.hf_power_ratio, i.blocking_score,
               i.sharpness_consistency, i.quality_tier,
               cm.quality_rank
        FROM ClusterMembership cm
        JOIN Images i ON i.id = cm.image_id
        WHERE cm.cluster_id = ?
        ORDER BY cm.quality_rank
        ''',
        (cluster_id,),
    ).fetchall()


def cleanup_missing_members(conn: sqlite3.Connection,
                            cluster_id: int) -> tuple[list[int], int]:
    """Delete DB entries for files in the cluster that no longer exist on disk.

    Returns (missing_ids, remaining_count).
    If remaining_count <= 1, the caller should delete the cluster itself.
    """
    rows = get_cluster_members(conn, cluster_id)
    missing_ids = [row['image_id'] for row in rows if not Path(row['path']).exists()]
    if missing_ids:
        ph = ','.join('?' * len(missing_ids))
        conn.execute(f'DELETE FROM ClusterMembership WHERE image_id IN ({ph})', missing_ids)
        conn.execute(f'DELETE FROM Images WHERE id IN ({ph})', missing_ids)
        conn.commit()
    return missing_ids, len(rows) - len(missing_ids)


def get_embedded_paths(conn: sqlite3.Connection,
                       paths: Iterable[str]) -> set[str]:
    """Return the subset of paths that have at least one embedding in the DB."""
    path_list = list(paths)
    if not path_list:
        return set()
    ph = ','.join('?' * len(path_list))
    rows = conn.execute(
        f'SELECT path FROM Images WHERE path IN ({ph})'
        f' AND (clip_embedding IS NOT NULL OR sscd_embedding IS NOT NULL)',
        path_list,
    ).fetchall()
    return {r['path'] for r in rows}
