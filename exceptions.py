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
