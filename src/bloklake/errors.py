from typing import Optional


class BloklakeError(Exception):
    """An API error (``status`` > 0, ``code`` from the response body) or a client-side
    failure (``status`` 0: ``truncated``, ``network``, ``timeout``).

    Codes: https://docs.bloklake.com/api-reference/errors
    """

    def __init__(self, status: int, code: str, message: str, retry_after: Optional[float] = None,
                 request_id: Optional[str] = None) -> None:
        super().__init__(f"{f'{status} ' if status else ''}{code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        #: Seconds from ``Retry-After``, when the server sent it.
        self.retry_after = retry_after
        #: ``X-Request-Id`` of the response: quote it to support.
        self.request_id = request_id

    @property
    def retryable(self) -> bool:
        """Worth retrying as is: rate limits, busy or failing servers, broken connections."""
        return self.status == 429 or self.status >= 500 or self.code in ("truncated", "network", "timeout")
