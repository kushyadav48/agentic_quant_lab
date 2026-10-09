"""Fail-closed portfolio input, identity and budget errors."""


class PortfolioError(ValueError):
    pass


class PortfolioIdentityConflict(PortfolioError):
    pass


class PortfolioBudgetError(PortfolioError):
    pass
