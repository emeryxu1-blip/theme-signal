"""Fatal model failures that must terminate an entire workflow run."""


class LLMWorkflowAbort(BaseException):
    """Stop the run instead of treating model failure as missing evidence.

    This deliberately derives from BaseException, like cancellation, so the
    workflow's ordinary ``except Exception`` fallback handlers cannot swallow
    exhaustion. CLI boundaries catch it explicitly before publishing output.
    """
