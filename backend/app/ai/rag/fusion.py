"""Rank fusion helpers shared by the local and Supabase retrievers."""

from collections.abc import Sequence


def weighted_rrf(
    rank_lists: Sequence[Sequence[str]],
    weights: Sequence[float],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse ranked ID lists with weighted Reciprocal Rank Fusion.

    ``weights`` lets dense and keyword candidates contribute different amounts.
    A zero weight disables that ranking without changing the other one.
    """
    if len(rank_lists) != len(weights):
        raise ValueError("rank_lists and weights must have the same length")

    scores: dict[str, float] = {}
    for ranking, weight in zip(rank_lists, weights):
        if weight <= 0:
            continue
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (k + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


def dense_anchored_rrf(
    dense_rank: Sequence[str],
    keyword_rank: Sequence[str],
    *,
    dense_weight: float = 1.0,
    keyword_weight: float = 0.05,
    k: int = 60,
    dense_anchor: int = 2,
) -> list[tuple[str, float]]:
    """Fuse dense and keyword ranks while protecting the best dense results.

    Pure RRF lets a strong BM25 match displace the semantically best dense
    result, which lowers MRR even when recall improves. Keeping the first
    ``dense_anchor`` dense results at the front preserves their order; the
    weighted RRF score then ranks the remaining candidates (including keyword
    only hits), so keyword can still rescue a relevant chunk that dense missed.
    """
    fused = weighted_rrf(
        [dense_rank, keyword_rank],
        [dense_weight, keyword_weight],
        k=k,
    )
    if dense_anchor <= 0:
        return fused

    anchored = list(dict.fromkeys(dense_rank[:dense_anchor]))
    if not anchored:
        return fused

    anchored_set = set(anchored)
    ordered = anchored + [
        chunk_id for chunk_id, _ in fused if chunk_id not in anchored_set
    ]
    # RRF scores describe order, not calibrated relevance. Recompute them for
    # the final order so callers can still sort or display by score.
    scale = max(dense_weight, keyword_weight, 1.0)
    return [
        (chunk_id, scale / (k + rank))
        for rank, chunk_id in enumerate(ordered, start=1)
    ]
