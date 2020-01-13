"""
This module implements some standard regression models: OLS and WLS
models, as well as an AR(p) regression model.

Models are specified with a design matrix and are fit using their
'fit' method.

Subclasses that have more complicated covariance matrices
should write over the 'whiten' method as the fit method
prewhitens the response by calling 'whiten'.

General reference for regression models:

'Introduction to Linear Regression Analysis', Douglas C. Montgomery,
    Elizabeth A. Peck, G. Geoffrey Vining. Wiley, 2006.

"""

__docformat__ = 'restructuredtext en'

import numpy as np

from nibabel.onetime import setattr_on_read
from numpy.linalg import matrix_rank
import scipy.linalg as spl

from .model import LikelihoodModelResults
from .utils import positive_reciprocal


class OLSModel(object):
    """ A simple ordinary least squares model.

    Parameters
    ----------
    design : array-like
        This is your design matrix.  Data are assumed to be column ordered with
        observations in rows.

    Methods
    -------
    model.__init___(design)
    model.logL(b=self.beta, Y)

    Attributes
    ----------
    design : ndarray
        This is the design, or X, matrix.

    wdesign : ndarray
        This is the whitened design matrix.  `design` == `wdesign` by default
        for the OLSModel, though models that inherit from the OLSModel will
        whiten the design.

    calc_beta : ndarray
        This is the Moore-Penrose pseudoinverse of the whitened design matrix.

    normalized_cov_beta : ndarray
        ``np.dot(calc_beta, calc_beta.T)``

    df_resid : scalar
        Degrees of freedom of the residuals.  Number of observations less the
        rank of the design.

    df_model : scalar
        Degrees of freedome of the model.  The rank of the design.
    """

    def __init__(self, design):
        """
        Parameters
        ----------
        design : array-like
            This is your design matrix.
            Data are assumed to be column ordered with
            observations in rows.
        """
        super(OLSModel, self).__init__()
        self.initialize(design)

    def initialize(self, design):
        # PLEASE don't assume we have a constant...
        # TODO: handle case for noconstant regression
        self.design = design
        self.wdesign = self.whiten(self.design)
        self.calc_beta = spl.pinv(self.wdesign)
        self.normalized_cov_beta = np.dot(self.calc_beta,
                                          np.transpose(self.calc_beta))
        self.df_total = self.wdesign.shape[0]

        eps = np.abs(self.design).sum() * np.finfo(np.float).eps
        self.df_model = matrix_rank(self.design, eps)
        self.df_resid = self.df_total - self.df_model

    def logL(self, beta, Y, nuisance=None):
        r''' Returns the value of the loglikelihood function at beta.

        Given the whitened design matrix, the loglikelihood is evaluated
        at the parameter vector, beta, for the dependent variable, Y
        and the nuisance parameter, sigma.

        Parameters
        ----------
        beta : ndarray
            The parameter estimates.  Must be of length df_model.

        Y : ndarray
            The dependent variable

        nuisance : dict, optional
            A dict with key 'sigma', which is an optional estimate of sigma. If
            None, defaults to its maximum likelihood estimate (with beta fixed)
            as ``sum((Y - X*beta)**2) / n``, where n=Y.shape[0], X=self.design.

        Returns
        -------
        loglf : float
            The value of the loglikelihood function.

        Notes
        -----
        The log-Likelihood Function is defined as

        .. math::

            \ell(\beta,\sigma,Y)=
            -\frac{n}{2}\log(2\pi\sigma^2) - \|Y-X\beta\|^2/(2\sigma^2)

        The parameter :math:`\sigma` above is what is sometimes referred to as a
        nuisance parameter. That is, the likelihood is considered as a function
        of :math:`\beta`, but to evaluate it, a value of :math:`\sigma` is
        needed.

        If :math:`\sigma` is not provided, then its maximum likelihood estimate:

        .. math::

            \hat{\sigma}(\beta) = \frac{\text{SSE}(\beta)}{n}

        is plugged in. This likelihood is now a function of only :math:`\beta`
        and is technically referred to as a profile-likelihood.

        References
        ----------
        .. [1] W. Green.  "Econometric Analysis," 5th ed., Pearson, 2003.
        '''
        # This is overwriting an abstract method of LikelihoodModel
        X = self.wdesign
        wY = self.whiten(Y)
        r = wY - np.dot(X, beta)
        n = self.df_total
        SSE = (r ** 2).sum(0)
        if nuisance is None:
            sigmasq = SSE / n
        else:
            sigmasq = nuisance['sigma']
        loglf = - n / 2. * np.log(2 * np.pi * sigmasq) - SSE / (2 * sigmasq)
        return loglf

    def whiten(self, X):
        """ Whiten design matrix

        Parameters
        ----------
        X : array
            design matrix

        Returns
        -------
        wX : array
            This matrix is the matrix whose pseudoinverse is ultimately
            used in estimating the coefficients. For OLSModel, it is
            does nothing. For WLSmodel, ARmodel, it pre-applies
            a square root of the covariance matrix to X.
        """
        return X

    def fit(self, Y):
        """ Fit model to data `Y`

        Full fit of the model including estimate of covariance matrix,
        (whitened) residuals and scale.

        Parameters
        ----------
        Y : array-like
            The dependent variable for the Least Squares problem.

        Returns
        -------
        fit : RegressionResults
        """
        # Other estimates of the covariance matrix for a heteroscedastic
        # regression model can be implemented in WLSmodel. (Weighted least
        # squares models assume covariance is diagonal, i.e. heteroscedastic).
        wY = self.whiten(Y)
        beta = np.dot(self.calc_beta, wY)
        wresid = wY - np.dot(self.wdesign, beta)
        dispersion = np.sum(wresid ** 2, 0) / (self.wdesign.shape[0] -
                                               self.wdesign.shape[1])
        lfit = RegressionResults(beta, Y, self,
                                 wY, wresid, dispersion=dispersion,
                                 cov=self.normalized_cov_beta)
        return lfit


class ARModel(OLSModel):
    """ A regression model with an AR(p) covariance structure.

    In terms of a LikelihoodModel, the parameters
    are beta, the usual regression parameters,
    and sigma, a scalar nuisance parameter that
    shows up as multiplier in front of the AR(p) covariance.

    """

    def __init__(self, design, rho):
        """ Initialize AR model instance

        Parameters
        ----------
        design : ndarray
            2D array with design matrix
        rho : int or array-like
            If int, gives order of model, and initializes rho to zeros.  If
            ndarray, gives initial estimate of rho. Be careful as ``ARModel(X,
            1) != ARModel(X, 1.0)``.
        """
        if type(rho) is type(1):
            self.order = rho
            self.rho = np.zeros(self.order, np.float64)
        else:
            self.rho = np.squeeze(np.asarray(rho))
            if len(self.rho.shape) not in [0, 1]:
                raise ValueError("AR parameters must be a scalar or a vector")
            if self.rho.shape == ():
                self.rho.shape = (1,)
            self.order = self.rho.shape[0]
        super(ARModel, self).__init__(design)

    def whiten(self, X):
        """ Whiten a series of columns according to AR(p) covariance structure

        Parameters
        ----------
        X : array-like of shape (n_features)
            array to whiten

        Returns
        -------
        wX : ndarray
            X whitened with order self.order AR
        """
        X = np.asarray(X, np.float64)
        _X = X.copy()
        for i in range(self.order):
            _X[(i + 1):] = _X[(i + 1):] - self.rho[i] * X[0: - (i + 1)]
        return _X


class RegressionResults(LikelihoodModelResults):
    """
    This class summarizes the fit of a linear regression model.

    It handles the output of contrasts, estimates of covariance, etc.
    """

    def __init__(self, theta, Y, model, wY, wresid, cov=None, dispersion=1.,
                 nuisance=None):
        """See LikelihoodModelResults constructor.

        The only difference is that the whitened Y and residual values
        are stored for a regression model.
        """
        LikelihoodModelResults.__init__(self, theta, Y, model, cov,
                                        dispersion, nuisance)
        self.wY = wY
        self.wresid = wresid
        self.wdesign = model.wdesign

    @setattr_on_read
    def resid(self):
        """
        Residuals from the fit.
        """
        return self.Y - self.predicted

    @setattr_on_read
    def norm_resid(self):
        """
        Residuals, normalized to have unit length.

        Notes
        -----
        Is this supposed to return "stanardized residuals,"
        residuals standardized
        to have mean zero and approximately unit variance?

        d_i = e_i / sqrt(MS_E)

        Where MS_E = SSE / (n - k)

        See: Montgomery and Peck 3.2.1 p. 68
             Davidson and MacKinnon 15.2 p 662
        """
        return self.resid * positive_reciprocal(np.sqrt(self.dispersion))

    @setattr_on_read
    def predicted(self):
        """ Return linear predictor values from a design matrix.
        """
        beta = self.theta
        # the LikelihoodModelResults has parameters named 'theta'
        X = self.wdesign
        return np.dot(X, beta)

    @setattr_on_read
    def SSE(self):
        """Error sum of squares. If not from an OLS model this is "pseudo"-SSE.
        """
        return (self.wresid ** 2).sum(0)

    @setattr_on_read
    def r_square(self):
        """Proportion of explained variance.
        If not from an OLS model this is "pseudo"-R2.
        """
        return np.var(self.predicted, 0) / np.var(self.wY, 0)

    @setattr_on_read
    def MSE(self):
        """ Mean square (error) """
        return self.SSE / self.df_resid


class SimpleRegressionResults(LikelihoodModelResults):
    """This class contains only information of the model fit necessary
    for contast computation.

    Its intended to save memory when details of the model are unnecessary.
    """
    def __init__(self, results):
        """See LikelihoodModelResults constructor.

        The only difference is that the whitened Y and residual values
        are stored for a regression model.
        """
        self.theta = results.theta
        self.cov = results.cov
        self.dispersion = results.dispersion
        self.nuisance = results.nuisance

        self.df_total = results.Y.shape[0]
        self.df_model = results.model.df_model
        # put this as a parameter of LikelihoodModel
        self.df_resid = self.df_total - self.df_model

    def logL(self, Y):
        """
        The maximized log-likelihood
        """
        raise ValueError('can not use this method for simple results')

    def resid(self, Y):
        """
        Residuals from the fit.
        """
        return Y - self.predicted

    def norm_resid(self, Y):
        """
        Residuals, normalized to have unit length.

        Notes
        -----
        Is this supposed to return "stanardized residuals,"
        residuals standardized
        to have mean zero and approximately unit variance?

        d_i = e_i / sqrt(MS_E)

        Where MS_E = SSE / (n - k)

        See: Montgomery and Peck 3.2.1 p. 68
             Davidson and MacKinnon 15.2 p 662
        """
        return self.resid(Y) * positive_reciprocal(np.sqrt(self.dispersion))

    def predicted(self):
        """ Return linear predictor values from a design matrix.
        """
        beta = self.theta
        # the LikelihoodModelResults has parameters named 'theta'
        X = self.model.design
        return np.dot(X, beta)
