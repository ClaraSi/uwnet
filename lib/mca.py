"""Code for maximum covariance analysis regression
"""
import attr
import numpy as np
from scipy.linalg import svd, lstsq
from sklearn.base import BaseEstimator, RegressorMixin, TransformerMixin
from sklearn.decomposition import PCA
from sklearn.linear_model import LinearRegression
from sklearn.neighbors import KDTree

from tqdm import tqdm

import dask.array as da


class Identity(TransformerMixin):
    def fit_transform(self, x):
        return x

class MCA(BaseEstimator, TransformerMixin):

    def __init__(self, n_components=4, demean=True,
                 y_transformer=None, whiten=False):
        self.n_components = n_components
        self.demean = demean

        if y_transformer is None:
            self.y_transformer = Identity()
        else:
            self.y_transformer = y_transformer

    def fit(self, X, y=None):

        # This is basically a hack to work around the difficult
        # with using scaled output for MCA but not for the linear regression
        y = self.y_transformer.fit_transform(y)

        X = np.asarray(X)
        Y = np.asarray(y)

        m, n = X.shape
        self.x_mean_ = X.mean(axis=0)
        self.y_mean_ = Y.mean(axis=0)

        if self.demean:
            X = X - self.x_mean_
            Y = Y - self.y_mean_

        self.cov_ = X.T.dot(Y)/(m-1)  # covariance matrix
        U, S, Vt = svd(self.cov_, full_matrices=False)

        self._u = U
        self._v = Vt.T

        # need to add back mean before using transform and other methods
        self.explained_var_ = self.x_explained_variance_(X+self.x_mean_)

        x_scores = (X-self.x_mean_).dot(self.x_components_)
        # x_scores = self.transform(X)
        # b = lstsq(x_scores, Y)[0]
        # self.coef_ = self.x_components_ @ b

        return self

    def transform(self, X, y=None):

        X = np.asarray(X)
        x_scores = (X-self.x_mean_).dot(self.x_components_)
        if y is not None:
            return x_scores, self.transform_y(y)
        else:
            return x_scores

    def transform_y(self, y):
        Y = np.asarray(y)
        y_scores = (Y-self.y_mean_).dot(self.y_components_)
        return y_scores


    def inverse_transform(self, X):
        return X.dot(self.x_components_.T) + self.x_mean_

    def project_x(self, x):
        return self.inverse_transform(self.transform(x))

    def project_y(self, y):
        return ((y - self.y_mean_).dot(self.y_components_))\
                                 .dot(self.y_components_.T)\
                                 + self.y_mean_

    @property
    def x_components_(self):
        return self._u[:, :self.n_components]

    @property
    def y_components_(self):
        return self._v[:, :self.n_components]

    def x_explained_variance_(self, X):
        X = np.asarray(X)
        sse = np.sum((self.project_x(X) - X)**2)
        ss = np.sum((X-X.mean(axis=0))**2)
        return 1 - sse/ss


    def y_explained_variance_(self, y):
        y = np.asarray(y)
        sse = np.sum((self.project_y(y) - y)**2)
        ss = np.sum((y-y.mean(axis=0))**2)
        return 1 - sse/ss


class MCARegression(BaseEstimator, RegressorMixin):
    """Weighted MCA regression class
    """

    def __init__(self, mod, scale=(1, 1), **kwargs):
        self.mca = MCA(**kwargs)
        self.mod = mod
        self.scale = scale


    def fit(self, x, y):
        scale_in, scale_out = self.scale

        # fit mca
        x_scores = self.mca.fit_transform(x*scale_in, y*scale_out)

        # fit other model
        self.mod.fit(x_scores, y)

        return self

    def predict(self, x):
        scale_in, scale_out = self.scale
        x_scores = self.mca.transform(x*scale_in)
        return self.mod.predict(x_scores)

@attr.s
class PCARegression(BaseEstimator, RegressorMixin):
    """Weighted principle components regression class.
    """

    scale = attr.ib(default=1.0)
    n_components = attr.ib(default=2)
    mod = attr.ib(default=attr.Factory(LinearRegression))

    def fit(self, x, y):
        # fit pca using outputs (since these don't include the forcing)
        x = np.asarray(x)
        y = np.asarray(y)

        self.pca = PCA(n_components=self.n_components, whiten=True)
        x_scores = self.pca.fit_transform(x*self.scale)
        # regress onto outputs
        self.mod.fit(x_scores, y)
        return self

    @property
    def explained_variance_ratio_(self):
        return self.pca.explained_variance_ratio_

    def predict(self, x):
        x = np.asarray(x)

        x_scores = self.pca.transform(x*self.scale)

        return self.mod.predict(x_scores)


@attr.s
class PCABinner(BaseEstimator, RegressorMixin):
    """Weighted principle components binning class

    The binning capability should probably be moved into a separate class
    """

    scale = attr.ib(default=1.0)
    n_components = attr.ib(default=2)
    n_query = attr.ib(default=100)
    batch_avg_size = attr.ib(default=1000)

    def fit(self, x, y):
        # fit pca using outputs (since these don't include the forcing)
        x = np.asarray(x)
        y = np.asarray(y)

        self.pca = PCA(n_components=self.n_components, whiten=True)
        x_scores = self.pca.fit_transform(x*self.scale)

        self.y_library_ = y

        # Generate KDTree
        self.kdtree_ = KDTree(x_scores)

        return self

    @property
    def explained_variance_ratio_(self):
        return self.pca.explained_variance_ratio_

    def neighbor_idx(self, x):
        x_scores = self.pca.transform(x*self.scale)
        idx = self.kdtree_.query(x_scores, k=self.n_query,
                                 return_distance=False)

        return idx

    def avg_over_neighbs(self, x):
        idx = self.neighbor_idx(x)
        return self.y_library_[idx].mean(axis=1)

    def predict(self, x):
        x = np.asarray(x)

        # x_dask = da.from_array(x, chunks=(self.batch_avg_size, -1))
        # y = x_dask.map_blocks(self.avg_over_neighbs)

        return np.vstack(self.avg_over_neighbs(x[sl])
                         for sl in tqdm(split_slices(x.shape[0],
                                                     self.batch_avg_size)))

        # pred = np.concatenate(
        #     (self.y_library_[idx[sl]]
        #      for sl in split_slices(x.shape[0], self.batch_avg_size))
        #     axis=0)

        # take mean of the neighborhood


def split_slices(n, k):
    """python generator for splitting an array into chunks of size k"""

    starts = range(0, n, k)

    for start in starts:
        end = min(start + k, n)
        yield slice(start, end)
