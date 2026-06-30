"""
pipeline/database.py
====================
SQLite Master Ground-Truth Log

Schema
------
documents           — one row per PO or Invoice image
    id              INTEGER PRIMARY KEY
    doc_type        TEXT    ('purchase_order' | 'tax_invoice')
    doc_index       INTEGER
    filename        TEXT    (now includes tier subfolder, e.g. 'degraded/po_000003.png')
    tier            TEXT    ('clean' | 'degraded' | 'heavy')
    po_number       TEXT    (FK between pairs)
    invoice_number  TEXT    (NULL for POs)
    json_payload    TEXT    (full structured JSON)
    buyer_name      TEXT
    seller_name     TEXT
    buyer_gstin     TEXT
    seller_gstin    TEXT
    is_interstate   INTEGER (NULL for POs; 0/1 for invoices)
    subtotal        REAL
    total_tax       REAL    (NULL for POs)
    grand_total     REAL    (NULL for POs)
    num_line_items  INTEGER
    layout_variant  INTEGER
    degradation_profile TEXT
    created_at      TEXT

document_pairs      — cross-reference PO ↔ Invoice
    pair_id         INTEGER PRIMARY KEY
    doc_index       INTEGER
    po_doc_id       INTEGER (FK → documents.id)
    inv_doc_id      INTEGER (FK → documents.id)
    po_number       TEXT
    invoice_number  TEXT
    tier            TEXT
    grand_total     REAL
    terminology_po_buyer  TEXT
    terminology_inv_seller TEXT
"""

import json
import logging
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("database")

DDL = """
CREATE TABLE IF NOT EXISTS documents (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type            TEXT    NOT NULL,
    doc_index           INTEGER NOT NULL,
    filename            TEXT    NOT NULL,
    tier                TEXT,
    po_number           TEXT    NOT NULL,
    invoice_number      TEXT,
    json_payload        TEXT    NOT NULL,
    buyer_name          TEXT,
    seller_name         TEXT,
    buyer_gstin         TEXT,
    seller_gstin        TEXT,
    is_interstate       INTEGER,
    subtotal            REAL,
    total_tax           REAL,
    grand_total         REAL,
    num_line_items      INTEGER,
    layout_variant      INTEGER,
    degradation_profile TEXT,
    has_discrepancy     INTEGER DEFAULT 0,
    discrepancy_kind    TEXT,
    created_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS document_pairs (
    pair_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_index           INTEGER NOT NULL,
    po_doc_id           INTEGER NOT NULL,
    inv_doc_id          INTEGER NOT NULL,
    po_number           TEXT    NOT NULL,
    invoice_number      TEXT    NOT NULL,
    tier                TEXT,
    grand_total         REAL    NOT NULL,
    terminology_po_buyer  TEXT,
    terminology_inv_seller TEXT,
    created_at          TEXT    NOT NULL,
    FOREIGN KEY (po_doc_id)  REFERENCES documents(id),
    FOREIGN KEY (inv_doc_id) REFERENCES documents(id)
);

CREATE INDEX IF NOT EXISTS idx_doc_po_number  ON documents(po_number);
CREATE INDEX IF NOT EXISTS idx_doc_doc_index  ON documents(doc_index);
CREATE INDEX IF NOT EXISTS idx_doc_tier       ON documents(tier);
CREATE INDEX IF NOT EXISTS idx_pairs_po_num   ON document_pairs(po_number);
CREATE INDEX IF NOT EXISTS idx_pairs_tier     ON document_pairs(tier);
"""


def init_database(db_path: Path) -> None:
    """Create the SQLite database and tables if they don't exist."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(DDL)
        conn.commit()
        log.info(f"Database initialised: {db_path}")
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_document_pair(
    db_path: Path,
    po_data: dict,
    inv_data: dict,
    po_filename: str,
    inv_filename: str,
    degradation_profile: str,
    tier: str = None,
) -> tuple[int, int]:
    """
    Insert both documents and a cross-reference pair record.

    Parameters
    ----------
    tier : "clean" | "degraded" | "heavy" | None
        The realism tier this document pair was rendered at. Optional for
        backward compatibility — older callers that don't pass tier will
        store NULL.

    Returns (po_doc_id, inv_doc_id).
    """
    db_path = Path(db_path)
    now = _now()

    po_row = (
        "purchase_order",
        po_data["doc_index"],
        po_filename,
        tier,
        po_data["po_number"],
        None,                               # invoice_number
        json.dumps(po_data, ensure_ascii=False),
        po_data.get("buyer_name"),
        po_data.get("seller_name"),
        po_data.get("buyer_gstin"),
        po_data.get("seller_gstin"),
        None,                               # is_interstate (N/A for PO)
        po_data.get("subtotal"),
        None,                               # total_tax
        None,                               # grand_total
        len(po_data.get("line_items", [])),
        po_data.get("layout_variant"),
        degradation_profile,
        now,
    )

    inv_row = (
        "tax_invoice",
        inv_data["doc_index"],
        inv_filename,
        tier,
        inv_data["po_number"],
        inv_data.get("invoice_number"),
        json.dumps(inv_data, ensure_ascii=False),
        inv_data.get("buyer_name"),
        inv_data.get("seller_name"),
        inv_data.get("buyer_gstin"),
        inv_data.get("seller_gstin"),
        1 if inv_data.get("is_interstate") else 0,
        inv_data.get("subtotal"),
        inv_data.get("total_tax"),
        inv_data.get("grand_total"),
        len(inv_data.get("line_items", [])),
        inv_data.get("layout_variant"),
        degradation_profile,
        now,
    )

    INSERT_DOC = """
        INSERT INTO documents
        (doc_type, doc_index, filename, tier, po_number, invoice_number,
         json_payload, buyer_name, seller_name, buyer_gstin, seller_gstin,
         is_interstate, subtotal, total_tax, grand_total,
         num_line_items, layout_variant, degradation_profile, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """

    INSERT_PAIR = """
        INSERT INTO document_pairs
        (doc_index, po_doc_id, inv_doc_id, po_number, invoice_number,
         tier, grand_total, terminology_po_buyer, terminology_inv_seller, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        cur = conn.cursor()
        cur.execute(INSERT_DOC, po_row)
        po_id = cur.lastrowid

        cur.execute(INSERT_DOC, inv_row)
        inv_id = cur.lastrowid

        pair_row = (
            po_data["doc_index"],
            po_id,
            inv_id,
            po_data["po_number"],
            inv_data.get("invoice_number", ""),
            tier,
            inv_data.get("grand_total", 0.0),
            po_data.get("buyer_term"),
            inv_data.get("seller_term"),
            now,
        )
        cur.execute(INSERT_PAIR, pair_row)
        conn.commit()
        return po_id, inv_id
    except Exception as e:
        conn.rollback()
        log.error(f"DB insert failed for index {po_data['doc_index']}: {e}")
        raise
    finally:
        conn.close()


def get_pair_count(db_path: Path) -> int:
    """Return number of document pairs already logged (for resume support)."""
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            cur = conn.execute("SELECT COUNT(*) FROM document_pairs")
            return cur.fetchone()[0]
        finally:
            conn.close()
    except Exception as e:
        # Was a bare `except Exception: return 0` — indistinguishable from
        # "DB genuinely has 0 rows". Logging means a real failure (locked
        # file, permissions, corruption) is visible instead of silently
        # masquerading as an empty database.
        log.warning(f"get_pair_count failed for {db_path}: {e} — returning 0")
        return 0


def get_tier_counts(db_path: Path) -> dict:
    """Return {tier: count} breakdown of logged document pairs (for verification)."""
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            cur = conn.execute(
                "SELECT tier, COUNT(*) FROM document_pairs GROUP BY tier"
            )
            return {row[0]: row[1] for row in cur.fetchall()}
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"get_tier_counts failed for {db_path}: {e} — returning {{}}")
        return {}


def get_max_doc_index(db_path: Path) -> int:
    """
    Return the highest doc_index already logged, or -1 if the DB is empty/
    missing. Use this to compute the correct --start-index for an append
    run: start_index = get_max_doc_index(db_path) + 1.

    Without this, --append only skips wiping the output folder — it does
    NOT skip past existing doc_index values, so re-running with the same
    index range silently overwrites existing files.

    NOTE: a real failure here (locked DB, permissions, corruption) used to
    be silently treated the same as "DB is empty", returning -1 — which
    main.py would then treat as "start fresh at 0", silently overwriting
    doc_index 0 even though the existing DB was fine and just temporarily
    unreadable. Now logged loudly so a real I/O error doesn't masquerade
    as an empty database on an --append run.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return -1
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            cur = conn.execute("SELECT MAX(doc_index) FROM document_pairs")
            result = cur.fetchone()[0]
            return -1 if result is None else int(result)
        finally:
            conn.close()
    except Exception as e:
        log.error(
            f"get_max_doc_index FAILED for {db_path}: {e} — returning -1, which "
            f"main.py's --append logic will treat as 'no existing documents' and "
            f"start at doc_index 0. If the DB actually has data, this WILL "
            f"overwrite it. Investigate before re-running --append."
        )
        return -1