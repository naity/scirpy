from scirpy.util import (
    _is_na,
    _is_false,
    _is_true,
    _normalize_counts,
    _is_symmetric,
    _translate_dna_to_protein,
)
from scirpy.util.graph import (
    igraph_from_sparse_matrix,
    layout_components,
    _distance_to_connectivity,
    _get_sparse_from_igraph,
)
from itertools import combinations
import igraph as ig
import numpy as np
import pandas as pd
import numpy.testing as npt
import pytest
import scipy.sparse
from .fixtures import adata_tra

import warnings


def test_is_symmetric():
    M = np.array([[1, 2, 2], [2, 1, 3], [2, 3, 1]])
    S_csr = scipy.sparse.csr_matrix(M)
    S_csc = scipy.sparse.csc_matrix(M)
    S_lil = scipy.sparse.lil_matrix(M)
    assert _is_symmetric(M)
    assert _is_symmetric(S_csr)
    assert _is_symmetric(S_csc)
    assert _is_symmetric(S_lil)

    M = np.array([[1, 2, 2], [2, 1, np.nan], [2, np.nan, np.nan]])
    S_csr = scipy.sparse.csr_matrix(M)
    S_csc = scipy.sparse.csc_matrix(M)
    S_lil = scipy.sparse.lil_matrix(M)
    assert _is_symmetric(M)
    assert _is_symmetric(S_csr)
    assert _is_symmetric(S_csc)
    assert _is_symmetric(S_lil)

    M = np.array([[1, 2, 2], [2, 1, 3], [3, 2, 1]])
    S_csr = scipy.sparse.csr_matrix(M)
    S_csc = scipy.sparse.csc_matrix(M)
    S_lil = scipy.sparse.lil_matrix(M)
    assert not _is_symmetric(M)
    assert not _is_symmetric(S_csr)
    assert not _is_symmetric(S_csc)
    assert not _is_symmetric(S_lil)


def test_is_na():
    warnings.filterwarnings("error")
    assert _is_na(None)
    assert _is_na(np.nan)
    assert _is_na("nan")
    assert not _is_na(42)
    assert not _is_na("Foobar")
    assert not _is_na(dict())
    array_test = np.array(["None", "nan", None, np.nan, "foobar"])
    array_expect = np.array([True, True, True, True, False])
    array_test_bool = np.array([True, False, True])
    array_expect_bool = np.array([False, False, False])

    npt.assert_equal(_is_na(array_test), array_expect)
    npt.assert_equal(_is_na(pd.Series(array_test)), array_expect)

    npt.assert_equal(_is_na(array_test_bool), array_expect_bool)
    npt.assert_equal(_is_na(pd.Series(array_test_bool)), array_expect_bool)


def test_is_false():
    warnings.filterwarnings("error")
    assert _is_false(False)
    assert _is_false(0)
    # assert _is_false("") -> I redelacred this as nan, as read_airr results in
    # null fields being "".
    assert _is_false("False")
    assert _is_false("false")
    assert not _is_false(42)
    assert not _is_false(True)
    assert not _is_false("true")
    assert not _is_false("foobar")
    assert not _is_false(np.nan)
    assert not _is_false(None)
    assert not _is_false("nan")
    assert not _is_false("None")
    array_test = np.array(
        ["False", "false", 0, 1, True, False, "true", "Foobar", np.nan, "nan"],
        dtype=object,
    )
    array_test_str = array_test.astype("str")
    array_expect = np.array(
        [True, True, True, False, False, True, False, False, False, False]
    )
    array_test_bool = np.array([True, False, True])
    array_expect_bool = np.array([False, True, False])

    npt.assert_equal(_is_false(array_test), array_expect)
    npt.assert_equal(_is_false(array_test_str), array_expect)
    npt.assert_equal(_is_false(pd.Series(array_test)), array_expect)
    npt.assert_equal(_is_false(pd.Series(array_test_str)), array_expect)
    npt.assert_equal(_is_false(array_test_bool), array_expect_bool)
    npt.assert_equal(_is_false(pd.Series(array_test_bool)), array_expect_bool)


def test_is_true():
    warnings.filterwarnings("error")
    assert not _is_true(False)
    assert not _is_true(0)
    assert not _is_true("")
    assert not _is_true("False")
    assert not _is_true("false")
    assert not _is_true("0")
    assert not _is_true(np.nan)
    assert not _is_true(None)
    assert not _is_true("nan")
    assert not _is_true("None")
    assert _is_true(42)
    assert _is_true(True)
    assert _is_true("true")
    assert _is_true("foobar")
    assert _is_true("True")
    array_test = np.array(
        ["False", "false", 0, 1, True, False, "true", "Foobar", np.nan, "nan"],
        dtype=object,
    )
    array_test_str = array_test.astype("str")
    array_expect = np.array(
        [False, False, False, True, True, False, True, True, False, False]
    )
    array_test_bool = np.array([True, False, True])
    array_expect_bool = np.array([True, False, True])

    npt.assert_equal(_is_true(array_test), array_expect)
    npt.assert_equal(_is_true(array_test_str), array_expect)
    npt.assert_equal(_is_true(pd.Series(array_test)), array_expect)
    npt.assert_equal(_is_true(pd.Series(array_test_str)), array_expect)
    npt.assert_equal(_is_true(array_test_bool), array_expect_bool)
    npt.assert_equal(_is_true(pd.Series(array_test_bool)), array_expect_bool)


@pytest.fixture
def group_df():
    return pd.DataFrame().assign(
        cell=["c1", "c2", "c3", "c4", "c5", "c6"],
        sample=["s2", "s1", "s2", "s2", "s2", "s1"],
    )


def test_normalize_counts(group_df):
    with pytest.raises(ValueError):
        _normalize_counts(group_df, True, None)

    npt.assert_equal(_normalize_counts(group_df, False), [1] * 6)
    npt.assert_equal(
        _normalize_counts(group_df, "sample"), [0.25, 0.5, 0.25, 0.25, 0.25, 0.5]
    )
    npt.assert_equal(
        _normalize_counts(group_df, True, "sample"), [0.25, 0.5, 0.25, 0.25, 0.25, 0.5]
    )


@pytest.mark.filterwarnings("ignore:UserWarning")
@pytest.mark.parametrize("arrange_boxes", ("size", "rpack", "squarify"))
@pytest.mark.parametrize("component_layout", ("fr_size_aware", "fr"))
def test_layout_components(arrange_boxes, component_layout):
    g = ig.Graph()

    # add 100 unconnected nodes
    g.add_vertices(100)

    # add 50 2-node components
    g.add_vertices(100)
    g.add_edges([(ii, ii + 1) for ii in range(100, 200, 2)])

    # add 33 3-node components
    g.add_vertices(100)
    for ii in range(200, 299, 3):
        g.add_edges([(ii, ii + 1), (ii, ii + 2), (ii + 1, ii + 2)])

    # add a couple of larger components
    n = 300
    for ii in np.random.randint(4, 30, size=10):
        g.add_vertices(ii)
        g.add_edges(combinations(range(n, n + ii), 2))
        n += ii

    try:
        layout_components(
            g, arrange_boxes=arrange_boxes, component_layout=component_layout
        )
    except ImportError:
        warnings.warn(
            f"The '{component_layout}' layout-test was skipped because rectangle "
            "packer is not installed. "
        )


def test_translate_dna_to_protein(adata_tra):
    for nt, aa in zip(
        adata_tra.obs["IR_VJ_1_junction"], adata_tra.obs["IR_VJ_1_junction_aa"]
    ):
        assert _translate_dna_to_protein(nt) == aa


@pytest.mark.parametrize(
    "dist,expected_conn,max_value",
    [
        (
            [[0, 1, 1, 5], [0, 0, 2, 8], [1, 5, 0, 2], [10, 0, 0, 0]],
            [[0, 1, 1, 0.6], [0, 0, 0.9, 0.3], [1, 0.6, 0, 0.9], [0.1, 0, 0, 0]],
            None,
        ),
        (
            [[0, 1, 1, 5], [0, 0, 2, 8], [1, 5, 0, 2], [10, 0, 0, 0]],
            [
                [0, 1, 1, 0.8],
                [0, 0, 0.95, 0.65],
                [1, 0.8, 0, 0.95],
                [0.55, 0, 0, 0],
            ],
            20,
        ),
        (
            [[0, 1, 1, 0], [0, 0, 1, 0], [1, 0, 0, 0], [0, 0, 0, 0]],
            [[0, 1, 1, 0], [0, 0, 1, 0], [1, 0, 0, 0], [0, 0, 0, 0]],
            None,
        ),
    ],
)
def test_dist_to_connectivities(dist, expected_conn, max_value):
    dist_mat = scipy.sparse.csr_matrix(dist)
    C = _distance_to_connectivity(dist_mat, max_value=max_value)
    assert C.nnz == dist_mat.nnz
    npt.assert_equal(
        C.toarray(),
        np.array(expected_conn),
    )


@pytest.mark.parametrize("simplify", [True, False])
@pytest.mark.parametrize(
    "matrix,is_symmetric",
    [
        (
            [
                [1.0, 0.0, 0.0, 0.6],
                [0.0, 1.0, 0.9, 0.3],
                [1.0, 0.6, 1.0, 0.9],
                [0.1, 0.0, 0.0, 1.0],
            ],
            False,
        ),
        (
            [
                [0.0, 1.0, 1.0, 0.6],
                [0.0, 0.0, 0.9, 0.3],
                [1.0, 0.6, 0.0, 0.9],
                [0.1, 0.0, 0.0, 0.0],
            ],
            False,
        ),
        (
            [
                [1.0, 0.3, 0.0, 0.6],
                [0.3, 1.0, 0.9, 0.3],
                [0.0, 0.9, 1.0, 0.9],
                [0.6, 0.3, 0.9, 1.0],
            ],
            True,
        ),
    ],
)
def test_igraph_from_adjacency(matrix, is_symmetric, simplify):
    matrix = scipy.sparse.csr_matrix(matrix)
    g = igraph_from_sparse_matrix(matrix, matrix_type="connectivity", simplify=simplify)
    assert len(list(g.vs)) == matrix.shape[0]
    matrix_roundtrip = _get_sparse_from_igraph(
        g, simplified=simplify, weight_attr="weight"
    )
    if simplify and not is_symmetric:
        with pytest.raises(AssertionError):
            npt.assert_equal(matrix.toarray(), matrix_roundtrip.toarray())
    else:
        npt.assert_equal(matrix.toarray(), matrix_roundtrip.toarray())
