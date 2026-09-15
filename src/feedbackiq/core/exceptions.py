"""
Custom exceptions for FeedbackIQ.
Import in any module: from exceptions import ModelNotLoadedError
"""

class FeedBackError(Exception):
    """Base exception for all Feedback exceptions."""
    def __init__(self,message:str):
        super().__init__(message)
        self.message = message
    
    def __str__(self):
        return f"[{self.__class__.__name__}] {self.message}"
    
class DataError(FeedBackError):
    """Exception for data related errors."""

class DataNotFoundError(DataError):
    """Exception for when processed data file is not found."""
    def __init__(self, path=""):
        if path:
            message = f"Processed data not found at '{path}'. Run: python scripts/preprocess.py"
        else:
            message = "Processed data file not found. Run: python scripts/preprocess.py"

        super().__init__(message)
        self.path = path


class JobStateError(FeedBackError):
    """
    An invalid job state transition was attempted.

    The lifecycle is queued -> running -> succeeded | failed. Anything else - finishing a
    job nobody claimed, re-running a finished one - is a bug in the caller, not a customer
    error, so it raises rather than quietly correcting itself. See db/jobs.py.
    """


class ImportError_(FeedBackError):
    """Reserved name placeholder - see IngestionError below."""


class IngestionError(FeedBackError):
    """
    An upload cannot be accepted at all: unreadable file, no text column, too large.

    Distinct from a *row* being rejected, which is reported per row in the import's error
    summary rather than raised - one bad line must not cost a customer their whole upload.
    """


class TaxonomyError(FeedBackError):
    """
    The complaint taxonomy is missing, unreadable or invalid.

    Raised instead of quietly substituting a different taxonomy: categorising against
    the wrong categories produces results that look correct and are not. See
    core/taxonomy.py and docs/production/milestone-05a.md.
    """


class CredentialError(FeedBackError):
    """
    A registration detail that breaks the rules: an invalid email address, a password that is
    too short, a blank organisation name.

    `message` is written for the person registering and never contains the password, so it
    is safe to return to a client. See auth/credentials.py and services/auth.py.
    """


class AccountExistsError(FeedBackError):
    """Registration with an email address that already has an account."""


class AccountDisabledError(FeedBackError):
    """
    The correct password for an account that has been disabled.

    Raised only *after* the password verifies, so it tells nobody anything they could not
    already find out by signing in. Every other failed sign-in is indistinguishable.
    """
