"""Analysis, clustering, and schema inference engine."""

from private_api_mcp.engine.auth_analyzer import (
    classify_headers,
    extract_credentials,
    extract_env_mapping,
    filter_tracking_cookies,
    generate_env_example_content,
    generate_env_file_content,
    is_tracking_cookie,
)
from private_api_mcp.engine.cluster import (
    cluster_exchanges,
    extract_graphql_operation_name,
    generate_endpoint_signature,
    normalize_path,
)
from private_api_mcp.engine.minimizer import (
    MinimizationResult,
    is_safe_read_request,
    replay_and_minimize,
)
from private_api_mcp.engine.schema_inferrer import (
    extract_compact_fields,
    infer_json_schema,
    merge_schemas,
)

__all__ = [
    "normalize_path",
    "extract_graphql_operation_name",
    "generate_endpoint_signature",
    "cluster_exchanges",
    "infer_json_schema",
    "merge_schemas",
    "extract_compact_fields",
    "classify_headers",
    "filter_tracking_cookies",
    "is_tracking_cookie",
    "extract_credentials",
    "extract_env_mapping",
    "generate_env_file_content",
    "generate_env_example_content",
    "MinimizationResult",
    "is_safe_read_request",
    "replay_and_minimize",
]
