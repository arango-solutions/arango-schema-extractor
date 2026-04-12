class SchemaAnalyzerError(Exception):
    """Base exception for schema analyzer operations.

    Standard error codes:
        PROVIDER_MISSING   - Required LLM provider SDK is not installed.
        PROVIDER_ERROR     - LLM provider request failed (may be transient).
        PARSE_ERROR        - Failed to parse or extract JSON from LLM output.
        VALIDATION_ERROR   - LLM output failed schema validation.
        INVALID_REQUEST    - Tool contract request validation failed.
        INVALID_ARGUMENT   - A function/method received an invalid argument.
        INVALID_MAPPING    - Physical mapping data is missing or malformed.
        MAPPING_NOT_FOUND  - No mapping exists for the requested entity/relationship.
        INTERNAL_ERROR     - Unexpected error (catch-all).
    """

    def __init__(self, message: str, code: str | None = None, *, cause: Exception | None = None):
        super().__init__(message)
        self.code = code
        self.__cause__ = cause
