import matplotlib.pyplot as plt
from .._compat import Literal
from anndata import AnnData
import pandas as pd
from .._util import _is_na
from .. import tl
from . import _base as base
from typing import Union, List
from . import base


def group_abundance(
    adata: Union[dict, AnnData],
    groupby: str,
    target_col: str = "has_tcr",
    *,
    fraction: Union[None, str, bool] = None,
    max_cols: Union[None, int] = None,
    **kwargs
) -> plt.Axes:
    """Plots how many cells belong to each clonotype. 

    Ignores NaN values. 
    
    Parameters
    ----------
    adata
        AnnData object to work on.
    groupby
        Group by this column from `obs`. Samples or diagnosis for example.
    target_col
        Column on which to compute the abundance. 
        Defaults to `has_tcr` which computes the number of all cells
        that have a T-cell receptor. 
    fraction
        If True, compute fractions of abundances relative to the `groupby` column
        rather than reporting abosolute numbers. Alternatively, a column 
        name can be provided according to that the values will be normalized. 
    max_cols: 
        Only plot the first `max_cols` columns. Will raise a 
        `ValueError` if attempting to plot more than 100 columsn. 
        Set to `0` to disable. 
    **kwargs
        Additional arguments passed to the base plotting function.  
    
    Returns
    -------
    Axes object
    """

    abundance = tl.group_abundance(
        adata, groupby, target_col=target_col, fraction=fraction
    )
    if abundance.shape[0] > 100 and max_cols is None:
        raise ValueError(
            "Attempting to plot more than 100 columns. "
            "Set `max_cols` to a sensible value or to `0` to disable this message"
        )
    if max_cols is not None and max_cols > 0:
        abundance = abundance.iloc[:max_cols, :]

    # Create text for default labels
    if fraction:
        fraction_base = target_col if fraction is True else fraction
        title = "Fraction of " + target_col + " in each " + groupby
        xlab = "Fraction of cells in " + fraction_base
        ylab = "Fraction of cells in " + fraction_base
    else:
        title = "Number of cells in " + groupby + " by " + target_col
        xlab = "Number of cells"
        ylab = "Number of cells"

    default_style_kws = {"title": title, "xlab": xlab, "ylab": ylab}
    if "style_kws" in kwargs:
        default_style_kws.update(kwargs["style_kws"])
    kwargs["style_kws"] = default_style_kws

    return base.bar(abundance, **kwargs)
