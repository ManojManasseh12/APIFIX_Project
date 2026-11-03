from app.ai.rag.fusion import dense_anchored_rrf, weighted_rrf


def ids(pairs):
    return [chunk_id for chunk_id, _ in pairs]


def test_weighted_rrf_prefers_higher_weighted_ranks():
    fused = weighted_rrf([["dense-a", "dense-b"], ["keyword-a"]], [1.0, 0.05])

    assert ids(fused) == ["dense-a", "dense-b", "keyword-a"]


def test_weighted_rrf_with_zero_keyword_weight_is_pure_dense():
    fused = weighted_rrf([["a", "b"], ["b", "a"]], [1.0, 0.0])

    assert ids(fused) == ["a", "b"]


def test_dense_anchor_keeps_best_dense_order():
    fused = dense_anchored_rrf(
        ["dense-a", "dense-b", "dense-c"],
        ["dense-c", "dense-a"],
        dense_anchor=2,
        keyword_weight=0.5,
    )

    assert ids(fused)[:2] == ["dense-a", "dense-b"]


def test_keyword_can_rescue_a_chunk_just_below_the_dense_cutoff():
    dense = [f"dense-{index}" for index in range(6)]
    fused = dense_anchored_rrf(
        dense,
        ["dense-5", "dense-0"],
        dense_anchor=2,
        keyword_weight=0.05,
    )

    assert "dense-5" in ids(fused)[:5]


def test_scores_follow_the_final_order():
    fused = dense_anchored_rrf(
        ["dense-a", "dense-b", "dense-c"],
        ["dense-c", "dense-a"],
        dense_anchor=2,
        keyword_weight=0.5,
    )

    scores = [score for _, score in fused]
    assert scores == sorted(scores, reverse=True)


def test_zero_anchor_matches_weighted_rrf():
    dense = ["a", "b"]
    keyword = ["b", "a"]
    assert dense_anchored_rrf(
        dense, keyword, dense_anchor=0, keyword_weight=0.5
    ) == weighted_rrf([dense, keyword], [1.0, 0.5])
