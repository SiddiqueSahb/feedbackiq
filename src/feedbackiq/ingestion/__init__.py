"""
Getting a customer's feedback into FeedbackIQ.

    CSV bytes  ──▶  csv_reader (validate)  ──▶  ParsedFeedback rows + RowError list
                                                        │
                                      services/imports.py stores them

This package is deliberately narrow: it parses and validates, and it knows nothing about
databases, HTTP or the analytics engine. That makes the validation rules testable on
strings alone, with no fixtures.
"""
