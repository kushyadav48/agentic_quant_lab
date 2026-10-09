"""Fixed journal boundary errors; no execution authority."""


class JournalError(ValueError):
    """Invalid, missing, incompatible or corrupt journal input."""


class JournalIdentityConflict(JournalError):
    """An existing identity was reused with different content."""


class JournalRecoveryRequired(JournalError):
    """Commit outcome is uncertain; close and reopen before further use."""
