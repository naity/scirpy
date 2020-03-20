import parasail
from multiprocessing import Pool
import itertools
from anndata import AnnData
from typing import Union, Collection, List, Tuple
from .._compat import Literal
import pandas as pd
import numpy as np
from scanpy import logging
import numpy.testing as npt
from .._util import _is_na, _is_symmetric
import abc
from Levenshtein import distance as levenshtein_dist
import scipy.spatial
import igraph as ig
from .._util import get_igraph_from_adjacency


def _define_clonotypes_no_graph(
    adata: AnnData,
    *,
    flavor: Literal["all_chains", "primary_only"] = "all_chains",
    inplace: bool = True,
    key_added: str = "clonotype",
) -> Union[None, np.ndarray]:
    """Old version of clonotype definition that works without graphs.

    The current definition of a clonotype is
    same CDR3 sequence for both primary and secondary
    TRA and TRB chains. If all chains are `NaN`, the clonotype will
    be `NaN` as well. 

    Parameters
    ----------
    adata
        Annotated data matrix
    flavor
        Biological model to define clonotypes. 
        `all_chains`: All four chains of a cell in a clonotype need to be the same. 
        `primary_only`: Only primary alpha and beta chain need to be the same. 
    inplace
        If True, adds a column to adata.obs
    key_added
        Column name to add to 'obs'

    Returns
    -------
    Depending on the value of `inplace`, either
    returns a Series with a clonotype for each cell 
    or adds a `clonotype` column to `adata`. 
    
    """
    groupby_cols = {
        "all_chains": ["TRA_1_cdr3", "TRB_1_cdr3", "TRA_2_cdr3", "TRA_2_cdr3"],
        "primary_only": ["TRA_1_cdr3", "TRB_1_cdr3"],
    }
    clonotype_col = np.array(
        [
            "clonotype_{}".format(x)
            for x in adata.obs.groupby(groupby_cols[flavor]).ngroup()
        ]
    )
    clonotype_col[
        _is_na(adata.obs["TRA_1_cdr3"])
        & _is_na(adata.obs["TRA_2_cdr3"])
        & _is_na(adata.obs["TRB_1_cdr3"])
        & _is_na(adata.obs["TRB_2_cdr3"])
    ] = np.nan

    if inplace:
        adata.obs[key_added] = clonotype_col
    else:
        return clonotype_col


class _DistanceCalculator(abc.ABC):
    def __init__(self, n_jobs: Union[int, None] = None):
        """
        Parameters
        ----------
        n_jobs
            Number of jobs to use for the pairwise distance calculation. 
            If None, use all jobs. 
        """
        self.n_jobs = n_jobs

    @abc.abstractmethod
    def calc_dist_mat(self, seqs: np.ndarray) -> np.ndarray:
        """Calculate a symmetric, pairwise distance matrix of all sequences in `seq`.
        Distances are non-negative values"""
        pass


class _IdentityDistanceCalculator(_DistanceCalculator):
    """Calculate the distance between TCR based on the identity 
    of sequences. I.e. 0 = sequence identical, 1 = sequences not identical
    """

    def calc_dist_mat(self, seqs: np.ndarray) -> np.ndarray:
        return 1 - np.identity(len(seqs))


class _LevenshteinDistanceCalculator(_DistanceCalculator):
    """Calculates the Levenshtein (i.e. edit-distance) between sequences. """

    def calc_dist_mat(self, seqs: np.ndarray) -> np.ndarray:
        dist = scipy.spatial.distance.pdist(
            seqs.reshape(-1, 1), metric=lambda x, y: levenshtein_dist(x[0], y[0])
        )
        return scipy.spatial.distance.squareform(dist)


class _AlignmentDistanceCalculator(_DistanceCalculator):
    def __init__(
        self,
        subst_mat: str = "blosum62",
        gap_open: int = 11,
        gap_extend: int = 1,
        n_jobs: Union[int, None] = None,
    ):
        """Class to generate pairwise alignment distances
        
        High-performance sequence alignment through parasail library [Daily2016]_

        Parameters
        ----------
        subst_mat
            Name of parasail substitution matrix
        gap_open
            Gap open penalty
        gap_extend
            Gap extend penatly
        n_jobs
            Number of processes to use. Will be passed to :meth:`multiprocessing.Pool`
        """
        self.subst_mat = subst_mat
        self.gap_open = gap_open
        self.gap_extend = gap_extend
        self.n_jobs = n_jobs

    def _align_row(self, seqs: np.ndarray, i_row: int) -> np.ndarray:
        """Generates a row of the triangular distance matrix. 
        
        Aligns `seqs[i_row]` with all other sequences in `seqs[i_row:]`. 

        Parameters
        ----------
        seqs
            Array of amino acid sequences 
        i_row
            Index of the row in the final distance matrix. Determines the target sequence. 

        Returns
        -------
        The i_th row of the final score matrix. 
        """
        subst_mat = parasail.Matrix(self.subst_mat)
        target = seqs[i_row]
        profile = parasail.profile_create_16(target, subst_mat)
        result = np.empty(len(seqs))
        result[:] = np.nan
        for j, s2 in enumerate(seqs[i_row:], start=i_row):
            r = parasail.nw_scan_profile_16(profile, s2, self.gap_open, self.gap_extend)
            result[j] = r.score

        return result

    def _calc_norm_factors(self, score_mat: np.ndarray) -> np.ndarray:
        """Calculate normalization factors to normaliza a score matrix between 0 and 1. 
        
        We define the normalization factors as the minimum of the self-alignment score
        of each pair of sequences. The refers to the max. possible score of an alignment
        between the two sequences. 
        """
        self_scores = np.diag(score_mat)
        a1, a2 = np.meshgrid(self_scores, self_scores)
        norm_factors = np.minimum(a1, a2)

        assert _is_symmetric(norm_factors), "Matrix not symmetric"

        return norm_factors

    def _score_to_dist(self, score_mat: np.ndarray) -> np.ndarray:
        """Convert an alignment score matrix into a distance. 
        
        This is achieved by subtracting the alignment score from a normalization
        vector that refers to the maximum possible alignment score between
        two sequences. """
        assert np.all(
            np.argmax(score_mat, axis=1) == np.diag_indices_from(score_mat)
        ), """Max value not on the diagonal"""

        norm_factors = self._calc_norm_factors(score_mat)

        dist_mat = norm_factors - score_mat
        assert np.all(
            dist_mat >= 0
        ), "The norm factors should be the highest achievable score. "

        return dist_mat

    def _calc_score_mat(self, seqs: Collection) -> np.ndarray:
        """Calculate the alignment scores of all-against-all pairwise
        sequence alignments"""
        p = Pool(self.n_jobs)
        rows = p.starmap(self._align_row, zip(itertools.repeat(seqs), range(len(seqs))))

        score_mat = np.vstack(rows)
        assert score_mat.shape[0] == score_mat.shape[1]

        # mirror matrix at diagonal (https://stackoverflow.com/a/42209263/2340703)
        i_lower = np.tril_indices(score_mat.shape[0], -1)
        score_mat[i_lower] = score_mat.T[i_lower]

        assert _is_symmetric(score_mat), "Matrix not symmetric"

        return score_mat

    def calc_dist_mat(self, seqs: Collection) -> np.ndarray:
        """Calculate the distances between amino acid sequences based on
        of all-against-all pairwise sequence alignments.

        Parameters
        ----------
        seqs
            Array of amino acid sequences

        Returns
        -------
        Symmetric, square matrix of normalized alignment distances. 
        """
        score_mat = self._calc_score_mat(seqs)
        dist_mat = self._score_to_dist(score_mat)
        return dist_mat


def _dist_for_chain(
    adata, chain: Literal["TRA", "TRB"], distance_calculator: _DistanceCalculator
) -> List[np.ndarray]:
    """Compute distances for all combinations of 
    (TRx1,TRx1), (TRx1,TRx2), (TRx2, TRx1), (TRx2, TRx2). 
    
    Parameters
    ----------
    adata
        Annotated data matrix
    chain
        Chain to work on. Can be "TRA" or "TRB". Will include both 
        primary (TRA_1_cdr3) and secondary (TRA_2_cdr3) chains. 
    distance_calculator
        Class implementing a calc_dist_mat(seqs) function 
        that computes pariwise distances between all cdr3 sequences. 
    """

    chains = ["{}_{}".format(chain, i) for i in ["1", "2"]]
    tr_seqs = {k: adata.obs["{}_cdr3".format(k)].values for k in chains}
    unique_seqs = np.hstack(list(tr_seqs.values()))
    unique_seqs = np.unique(unique_seqs[~_is_na(unique_seqs)]).astype(str)
    # reverse mapping of amino acid sequence to index in distance matrix
    seq_to_index = {seq: i for i, seq in enumerate(unique_seqs)}

    logging.debug("Started computing {} pairwise distances.".format(chain))
    dist_mat = distance_calculator.calc_dist_mat(unique_seqs)
    logging.info("Finished computing {} pairwise distances.".format(chain))

    # indices of cells in adata that have a CDR3 sequence.
    cells_with_chain = {k: np.where(~_is_na(tr_seqs[k]))[0] for k in chains}
    # indices of the corresponding sequences in the distance matrix.
    seq_inds = {
        k: np.array([seq_to_index[tr_seqs[k][i]] for i in cells_with_chain[k]])
        for k in chains
    }

    # assert that the indices are correct...
    for k in chains:
        npt.assert_equal(unique_seqs[seq_inds[k]], tr_seqs[k][~_is_na(tr_seqs[k])])

    # compute cell x cell matrix for each combination of chains
    cell_mats = list()
    for chain1, chain2 in [(1, 1), (1, 2), (2, 2)]:
        chain1, chain2 = "{}_{}".format(chain, chain1), "{}_{}".format(chain, chain2)
        cell_mat = np.full([adata.n_obs] * 2, np.nan)

        # 2d indices in the cell matrix
        # This is several orders of magnitudes faster than using nested for loops.
        i_cm_0, i_cm_1 = np.meshgrid(cells_with_chain[chain1], cells_with_chain[chain2])
        # 2d indices of the sequences in the distance matrix
        i_dm_0, i_dm_1 = np.meshgrid(seq_inds[chain1], seq_inds[chain2])

        cell_mat[i_cm_0, i_cm_1] = dist_mat[i_dm_0, i_dm_1]

        if chain1 == chain2:
            # TRX1:TRX2 is not supposed to be symmetric
            assert _is_symmetric(cell_mat), "matrix not symmetric"

        cell_mats.append(cell_mat)
        if chain1 != chain2:
            cell_mats.append(cell_mat.T)

    return cell_mats


def tcr_dist(
    adata: AnnData,
    *,
    metric: Literal["alignment", "identity", "levenshtein"] = "alignment",
    n_jobs: Union[int, None] = None,
) -> Tuple:
    """Computes the distances between CDR3 sequences 

    Parameters
    ----------
    metric
        Distance metric to use. `alignment` will calculate an alignment distance
        based on normalized BLOSUM62 scores. 
        `identity` results in `0` for an identical sequence, `1` for different sequence. 
        `levenshtein` is the Levenshtein edit distance between two strings. 

    Returns
    -------
    tra_dists, trb_dists

    """
    if metric == "alignment":
        dist_calc = _AlignmentDistanceCalculator(n_jobs=n_jobs)
    elif metric == "identity":
        dist_calc = _IdentityDistanceCalculator()
    elif metric == "levenshtein":
        dist_calc = _LevenshteinDistanceCalculator()
    else:
        raise ValueError("Invalid distance metric.")

    tra_dists = _dist_for_chain(adata, "TRA", dist_calc)
    trb_dists = _dist_for_chain(adata, "TRB", dist_calc)

    return tra_dists, trb_dists


def define_clonotypes(
    adata: AnnData,
    *,
    metric: Literal["alignment", "levenshtein"] = "alignment",
    key_added: str = "clonotype",
    cutoff: int = 2,
    strategy: Literal["TRA", "TRB", "all", "any", "lenient"] = "any",
    chains: Literal["primary_only", "all"] = "primary_only",
    inplace: bool = True,
    resolution: float = 1,
    n_iterations: int = 5,
    n_jobs: Union[int, None] = None,
) -> Union[Tuple, None]:
    """Define clonotypes based on cdr3 distance.
    
    For now, uses primary TRA and TRB only. 

    Parameters
    ----------
    metric
        "alignment" - Calculate distance using pairwise sequence alignment 
            and BLOSUM62 matrix
        "levenshtein" - Levenshtein edit distance
    cutoff
        Two cells with a distance <= the cutoff will be connected. 
        If cutoff = 0, the CDR3 sequences need to be identical. In this 
        case, no alignment is performed. 
    strategy:
        "TRA" - only consider TRA sequences
        "TRB" - only consider TRB sequences
        "all" - both TRA and TRB need to match
        "any" - either TRA or TRB need to match
        "lenient" - both TRA and TRB need to match, however it is tolerated if for a 
          given cell pair, no TRA or TRB sequence is available. 
    chains:
        Use only primary chains ("TRA_1", "TRB_1") or all four chains? 
        When considering all four chains, at least one of them needs
        to match. 

    """
    if cutoff == 0:
        metric = "identity"
    tra_dists, trb_dists = tcr_dist(adata, metric=metric, n_jobs=n_jobs)

    if chains == "primary_only":
        tra_dist = tra_dists[0]
        trb_dist = trb_dists[0]
    elif chains == "all":
        tra_dist = np.fmin.reduce(tra_dists)
        trb_dist = np.fmin.reduce(trb_dists)
    else:
        raise ValueError("Unknown value for `chains`")

    assert _is_symmetric(tra_dist)
    assert _is_symmetric(trb_dist)

    # TODO implement weights
    with np.errstate(invalid="ignore"):
        if strategy == "TRA":
            adj = tra_dist <= cutoff
        elif strategy == "TRB":
            adj = trb_dist <= cutoff
        elif strategy == "any":
            adj = (tra_dist <= cutoff) | (trb_dist <= cutoff)
        elif strategy == "all":
            adj = (tra_dist <= cutoff) & (trb_dist <= cutoff)
        elif strategy == "lenient":
            adj = (
                ((tra_dist == 0) | np.isnan(tra_dist))
                & ((trb_dist == 0) | np.isnan(trb_dist))
                & ~(np.isnan(tra_dist) & np.isnan(trb_dist))
            )
        else:
            raise ValueError("Unknown strategy. ")

    g = get_igraph_from_adjacency(adj)

    # find all connected partitions that are
    # connected by at least one edge
    partitions = g.community_leiden(
        objective_function="modularity",
        resolution_parameter=resolution,
        n_iterations=n_iterations,
    )

    clonotype = np.array([str(x) for x in partitions.membership])
    clonotype_size = pd.Series(clonotype).groupby(clonotype).transform("count").values
    assert len(clonotype) == len(clonotype_size) == adata.obs.shape[0]

    if not inplace:
        return (adj, clonotype, clonotype_size)
    else:
        if "sctcrpy" not in adata.uns:
            adata.uns["sctcrpy"] = dict()
        adata.uns["sctcrpy"][key_added + "_connectivities"] = adj
        adata.obs[key_added] = clonotype
        adata.obs[key_added + "_size"] = clonotype_size


def clonotype_network(
    adata, *, layout="fr", key="clonotype", key_added="X_clonotype_network", min_size=1
):
    """Build the clonotype network for plotting
    
    Parameters
    ----------
    min_size
        Only show clonotypes with at least `min_size` cells.
    """
    try:
        graph = get_igraph_from_adjacency(adata.uns["sctcrpy"][key + "_connectivities"])
    except KeyError:
        raise ValueError("You need to run define_clonotypes first.")

    subgraph_idx = np.where(adata.obs[key + "_size"].values >= min_size)[0]
    if len(subgraph_idx) == 0:
        raise ValueError("No subgraphs with size >= {} found.".format(min_size))
    graph = graph.subgraph(subgraph_idx)
    layout_ = graph.layout(layout)
    coordinates = np.full((adata.n_obs, 2), fill_value=np.nan)
    coordinates[subgraph_idx, :] = layout_.coords
    adata.obsm["X_clonotype_network"] = coordinates