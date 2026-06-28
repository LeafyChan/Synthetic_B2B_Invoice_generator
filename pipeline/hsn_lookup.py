"""
pipeline/hsn_lookup.py
======================
Runtime TF-IDF semantic search over the official HSN/SAC master.

Usage
-----
    from pipeline.hsn_lookup import HSNIndex

    idx = HSNIndex()                        # loads once, cached per process
    goods   = idx.search_goods("stainless steel ball valve", top_k=5)
    services = idx.search_services("annual maintenance contract", top_k=3)

Each result is a dict:
    {
        "code":         "84818090",
        "description":  "Nuclear reactors … BALL VALVES",
        "gst_rate":     18,
        "unit":         "PCS",          # goods only
        "match_score":  0.412,
    }

Design notes
------------
- Index is loaded lazily on first use and cached as a module-level singleton.
- All search is cosine similarity in sparse TF-IDF space — no model download,
  no network, no GPU required.  Query takes <5ms on a commodity laptop CPU.
- For the Ollama-assisted path (bootstrap.py), this module provides the real
  candidate codes that are passed to the LLM for selection — the LLM never
  invents HSN digits, it only picks from a pre-filtered real list.
- For the fallback path (no Ollama), this module drives catalog generation
  directly by querying each product description against the master.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger("hsn_lookup")

HERE = Path(__file__).parent
DATA_DIR = HERE / "hsn_data"

# ── Module-level singleton (one load per process) ─────────────────────────────
_instance: Optional["HSNIndex"] = None


def get_index() -> "HSNIndex":
    global _instance
    if _instance is None:
        _instance = HSNIndex()
    return _instance


class HSNIndex:
    """
    Lazy-loading TF-IDF search index over the official HSN + SAC master.

    If the index files don't exist (first run before build_hsn_index.py),
    raises FileNotFoundError with a clear message telling the user what to do.
    """

    def __init__(self) -> None:
        self._hsn_vec   = None
        self._hsn_mat   = None
        self._hsn_meta: list[dict] = []
        self._sac_vec   = None
        self._sac_mat   = None
        self._sac_meta: list[dict] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            import joblib
        except ImportError:
            raise ImportError(
                "joblib is required for HSN lookup. "
                "Run: pip install scikit-learn joblib"
            )

        def _load(prefix: str):
            vec_path  = DATA_DIR / f"{prefix}_vectorizer.joblib"
            mat_path  = DATA_DIR / f"{prefix}_matrix.joblib"
            meta_path = DATA_DIR / f"{prefix}_meta.json"
            if not vec_path.exists():
                raise FileNotFoundError(
                    f"HSN index not built yet. Run:\n"
                    f"  python pipeline/build_hsn_index.py --xlsx /path/to/HSN_SAC.xlsx\n"
                    f"Expected: {vec_path}"
                )
            vec  = joblib.load(vec_path)
            mat  = joblib.load(mat_path)
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            return vec, mat, meta

        log.debug("Loading HSN/SAC TF-IDF index …")
        self._hsn_vec, self._hsn_mat, self._hsn_meta = _load("hsn")
        self._sac_vec, self._sac_mat, self._sac_meta = _load("sac")
        self._loaded = True
        log.debug(
            f"HSN index ready: {len(self._hsn_meta)} goods codes, "
            f"{len(self._sac_meta)} service codes"
        )

    # ── Core search ───────────────────────────────────────────────────────────

    def _search(
        self,
        query: str,
        vectorizer,
        matrix,
        meta: list[dict],
        top_k: int,
        gst_filter: Optional[int] = None,
    ) -> list[dict]:
        """
        Transform query → TF-IDF vector, compute cosine similarity against
        the pre-built matrix, return top_k matches with scores.
        """
        q_vec = vectorizer.transform([query.lower()])
        # Cosine similarity: matrix rows are already L2-normalised by sklearn
        scores = (matrix @ q_vec.T).toarray().ravel()

        if gst_filter is not None:
            mask = np.array([r["gst_rate"] == gst_filter for r in meta])
            scores = scores * mask

        top_idx = np.argsort(scores)[::-1][:top_k]
        results = []
        for i in top_idx:
            if scores[i] <= 0:
                break
            rec = dict(meta[i])
            rec["match_score"] = round(float(scores[i]), 4)
            results.append(rec)
        return results

    def search_goods(
        self,
        query: str,
        top_k: int = 5,
        gst_filter: Optional[int] = None,
    ) -> list[dict]:
        """
        Find the top_k HSN codes most relevant to `query`.

        Parameters
        ----------
        query       : free-text product description
        top_k       : number of results to return
        gst_filter  : if set, restrict to codes with this exact GST rate
        """
        self._ensure_loaded()
        return self._search(
            query, self._hsn_vec, self._hsn_mat, self._hsn_meta,
            top_k, gst_filter
        )

    def search_services(
        self,
        query: str,
        top_k: int = 3,
    ) -> list[dict]:
        """Find the top_k SAC codes most relevant to `query`."""
        self._ensure_loaded()
        return self._search(
            query, self._sac_vec, self._sac_mat, self._sac_meta,
            top_k
        )

    def lookup_code(self, code: str) -> Optional[dict]:
        """Exact lookup by HSN or SAC code string. Returns None if not found."""
        self._ensure_loaded()
        is_sac = str(code).startswith("99") or len(str(code)) == 6
        pool = self._sac_meta if is_sac else self._hsn_meta
        for rec in pool:
            if rec["code"] == str(code).strip():
                return dict(rec)
        return None

    def random_goods(self, rng, n: int = 1) -> list[dict]:
        """Return n random goods records (seeded RNG for reproducibility)."""
        self._ensure_loaded()
        indices = rng.sample(range(len(self._hsn_meta)), min(n, len(self._hsn_meta)))
        return [dict(self._hsn_meta[i]) for i in indices]

    def random_services(self, rng, n: int = 1) -> list[dict]:
        """Return n random service records."""
        self._ensure_loaded()
        indices = rng.sample(range(len(self._sac_meta)), min(n, len(self._sac_meta)))
        return [dict(self._sac_meta[i]) for i in indices]

    @property
    def goods_count(self) -> int:
        self._ensure_loaded()
        return len(self._hsn_meta)

    @property
    def service_count(self) -> int:
        self._ensure_loaded()
        return len(self._sac_meta)