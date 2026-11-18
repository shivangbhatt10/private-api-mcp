"""Idiomatic Python client code generator using httpx and Pydantic v2."""

from __future__ import annotations

import ast
import keyword
import re
from typing import Any, Optional, Union
from urllib.parse import urlparse

from private_api_mcp.engine.auth_analyzer import (
    extract_credentials,
    extract_env_mapping,
    generate_env_example_content,
    generate_env_file_content,
)
from private_api_mcp.engine.cluster import cluster_exchanges
from private_api_mcp.models import CapturedExchange, EndpointCluster

PYTHON_KEYWORDS = set(keyword.kwlist)


def to_snake_case(name: str) -> str:
    """Convert PascalCase, camelCase, or hyphenated string to snake_case."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", s2).lower().strip("_")
    return clean or "item"


def to_pascal_case(name: str) -> str:
    """Convert snake_case or hyphenated string to PascalCase."""
    tokens = re.split(r"[^A-Za-z0-9]+", name)
    return "".join(t.capitalize() for t in tokens if t) or "Model"


def sanitize_identifier(name: str) -> str:
    """Ensure identifier is a valid Python variable name and does not clash with keywords."""
    clean = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if clean and clean[0].isdigit():
        clean = "_" + clean
    if clean in PYTHON_KEYWORDS:
        clean = f"{clean}_"
    return clean or "var"


def format_method_name(method: str, path_pattern: str, operation_name: Optional[str]) -> str:
    """Generate a clean, idiomatic Python method name."""
    if operation_name:
        return to_snake_case(operation_name)

    clean_path = path_pattern.replace("{id}", "by_id").replace("{uuid}", "by_uuid")
    # Strip common leading api versions
    clean_path = re.sub(r"^/api/v\d+/", "/", clean_path)
    clean_path = re.sub(r"^/v\d+/", "/", clean_path)

    segments = [s for s in clean_path.strip("/").split("/") if s]
    path_slug = "_".join(segments)
    method_name = f"{method.lower()}_{path_slug}" if path_slug else method.lower()
    return to_snake_case(method_name)


def map_json_type_to_python(schema_type: Any) -> str:
    """Map JSON Schema primitive types to Python type annotations."""
    if isinstance(schema_type, list):
        types = [map_json_type_to_python(t) for t in schema_type if t != "null"]
        if not types:
            return "Any"
        return f"Union[{', '.join(types)}]"

    mapping = {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "array": "list[Any]",
        "object": "dict[str, Any]",
        "null": "None",
    }
    return mapping.get(str(schema_type), "Any")


def generate_pydantic_model(model_name: str, schema: Optional[dict[str, Any]]) -> str:
    """Generate a Pydantic v2 response model class string."""
    if not schema or schema.get("type") != "object":
        return ""

    properties = schema.get("properties", {})
    if not properties:
        return ""

    lines = [
        f"class {model_name}(BaseModel):",
        '    """Inferred API response model."""',
        "    model_config = ConfigDict(populate_by_name=True, extra='ignore')",
        "",
    ]

    for prop_name, prop_schema in properties.items():
        field_id = sanitize_identifier(prop_name)
        prop_type = map_json_type_to_python(prop_schema.get("type", "Any"))

        if field_id != prop_name:
            line = f"    {field_id}: Optional[{prop_type}] = Field(default=None, alias='{prop_name}')"
        else:
            line = f"    {field_id}: Optional[{prop_type}] = None"
        lines.append(line)

    lines.append("")
    return "\n".join(lines)


def generate_python_client(
    clusters: Union[list[EndpointCluster], list[CapturedExchange]],
    client_name: str = "ApiClient",
    base_url: Optional[str] = None,
) -> tuple[str, str]:
    """Deterministically generate an idiomatic, typed Python client module (httpx + Pydantic v2)
    and an accompanying .env file content.

    Args:
        clusters: List of EndpointCluster instances, or raw CapturedExchange instances.
        client_name: Name of the generated client class (e.g. 'ApiClient').
        base_url: Optional base URL; inferred from sample exchanges if omitted.

    Returns:
        tuple of (generated_python_code: str, env_file_content: str).
    """
    endpoint_clusters: list[EndpointCluster]
    all_exchanges: list[CapturedExchange] = []

    if clusters and isinstance(clusters[0], CapturedExchange):
        all_exchanges = list(clusters)  # type: ignore
        cluster_dict = cluster_exchanges(all_exchanges)
        endpoint_clusters = list(cluster_dict.values())
    else:
        endpoint_clusters = list(clusters)  # type: ignore
        for c in endpoint_clusters:
            all_exchanges.extend(c.exchanges)

    # 1. Determine base URL
    if not base_url:
        for ex in all_exchanges:
            if ex.url:
                parsed = urlparse(ex.url)
                if parsed.scheme and parsed.netloc:
                    base_url = f"{parsed.scheme}://{parsed.netloc}"
                    break
    if not base_url:
        base_url = "https://api.example.com"

    # 2. Extract credentials and generate .env mappings
    all_credentials = []
    for ex in all_exchanges:
        all_credentials.extend(extract_credentials(ex))
    env_mapping = extract_env_mapping(all_credentials)
    env_content = generate_env_file_content(env_mapping)

    # 3. Build Python client module
    code_sections = [
        '"""Auto-generated Python client for private API interaction.',
        "",
        "Generated by private-api-mcp.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import os",
        "from typing import Any, Optional, Union",
        "import httpx",
        "from pydantic import BaseModel, ConfigDict, Field",
        "",
        "",
    ]

    # Models section
    models_code: list[str] = []
    cluster_model_map: dict[str, str] = {}
    seen_model_names: set[str] = set()

    for cluster in endpoint_clusters:
        if cluster.response_schema and cluster.response_schema.get("type") == "object":
            raw_name = cluster.operation_name or format_method_name(
                cluster.method, cluster.path_pattern, None
            )
            model_name = f"{to_pascal_case(raw_name)}Response"
            if model_name in seen_model_names:
                model_name = f"{model_name}_{len(seen_model_names)}"
            seen_model_names.add(model_name)

            model_str = generate_pydantic_model(model_name, cluster.response_schema)
            if model_str:
                models_code.append(model_str)
                cluster_model_map[cluster.signature] = model_name

    if models_code:
        code_sections.extend(models_code)
        code_sections.append("")

    # Client class section
    client_lines = [
        f"class {client_name}:",
        f'    """Client for interacting with {base_url}."""',
        "",
        "    def __init__(",
        f'        self,',
        f'        base_url: str = "{base_url}",',
        f'        timeout: float = 30.0,',
    ]

    # Add environment variable arguments to __init__
    for env_var in sorted(env_mapping.keys()):
        arg_name = sanitize_identifier(env_var.lower())
        client_lines.append(f"        {arg_name}: Optional[str] = None,")

    client_lines.extend(
        [
            "    ) -> None:",
            "        self.base_url = base_url.rstrip('/')",
            "        self.headers: dict[str, str] = {",
            "            'Accept': 'application/json',",
            "            'User-Agent': 'private-api-mcp-client/1.0',",
            "        }",
            "        self.cookies: dict[str, str] = {}",
            "",
        ]
    )

    # Initialize auth headers and cookies from env or parameters
    for env_var, sample_val in sorted(env_mapping.items()):
        arg_name = sanitize_identifier(env_var.lower())
        client_lines.extend(
            [
                f"        # Resolve {env_var}",
                f"        val_{arg_name} = {arg_name} or os.getenv('{env_var}')",
            ]
        )
        if "AUTH" in env_var or "TOKEN" in env_var or "KEY" in env_var:
            if sample_val.lower().startswith("bearer "):
                client_lines.append(
                    f"        if val_{arg_name}:\n            self.headers['Authorization'] = val_{arg_name}"
                )
            else:
                client_lines.append(
                    f"        if val_{arg_name}:\n            self.headers['Authorization'] = f'Bearer {{val_{arg_name}}}'"
                )
        elif "COOKIE" in env_var:
            cookie_key = env_var.replace("API_COOKIE_", "").lower()
            client_lines.append(
                f"        if val_{arg_name}:\n            self.cookies['{cookie_key}'] = val_{arg_name}"
            )
        else:
            client_lines.append(
                f"        if val_{arg_name}:\n            self.headers['{env_var}'] = val_{arg_name}"
            )

    client_lines.extend(
        [
            "",
            "        self.client = httpx.Client(",
            "            base_url=self.base_url,",
            "            headers=self.headers,",
            "            cookies=self.cookies,",
            "            timeout=timeout,",
            "        )",
            "",
            "    def __enter__(self) -> " + client_name + ":",
            "        return self",
            "",
            "    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:",
            "        self.close()",
            "",
            "    def close(self) -> None:",
            "        self.client.close()",
            "",
        ]
    )

    # Generate method for each endpoint cluster
    seen_methods: set[str] = set()

    for cluster in endpoint_clusters:
        base_mname = format_method_name(cluster.method, cluster.path_pattern, cluster.operation_name)
        mname = base_mname
        counter = 1
        while mname in seen_methods or mname in PYTHON_KEYWORDS:
            counter += 1
            mname = f"{base_mname}_{counter}"
        seen_methods.add(mname)

        # Detect path parameters like {id} or {uuid}
        raw_path_params = re.findall(r"\{([A-Za-z0-9_]+)\}", cluster.path_pattern)
        path_args: list[str] = []
        path_format_expr = cluster.path_pattern

        param_counts: dict[str, int] = {}
        for p in raw_path_params:
            param_counts[p] = param_counts.get(p, 0) + 1

        param_seen: dict[str, int] = {}
        for p in raw_path_params:
            p_arg = p
            if param_counts[p] > 1:
                param_seen[p] = param_seen.get(p, 0) + 1
                p_arg = f"{p}_{param_seen[p]}"
                path_format_expr = path_format_expr.replace(f"{{{p}}}", f"{{{p_arg}}}", 1)
            path_args.append(p_arg)

        # Build method parameters list
        method_args = ["self"]
        for pa in path_args:
            method_args.append(f"{pa}: str")

        # Query parameter arguments
        query_param_args: list[str] = []
        for qp in cluster.query_param_names:
            clean_qp = sanitize_identifier(qp)
            query_param_args.append(f"{clean_qp}: Optional[str] = None")
        method_args.extend(query_param_args)

        # Body argument if mutating or GraphQL
        is_graphql = cluster.operation_name is not None or "graphql" in cluster.path_pattern.lower()
        if is_graphql:
            method_args.append("variables: Optional[dict[str, Any]] = None")
        elif cluster.method in ("POST", "PUT", "PATCH"):
            method_args.append("body: Optional[Union[dict[str, Any], Any]] = None")

        # Return type annotation
        resp_model = cluster_model_map.get(cluster.signature)
        return_type = resp_model if resp_model else "dict[str, Any]"

        args_str = ", ".join(method_args)
        client_lines.extend(
            [
                f"    def {mname}({args_str}) -> {return_type}:",
                f'        """{cluster.method} {cluster.path_pattern}"""',
            ]
        )

        # Construct path URL
        if path_args:
            client_lines.append(f'        path = f"{path_format_expr}"')
        else:
            client_lines.append(f'        path = "{cluster.path_pattern}"')

        # Construct query params
        if cluster.query_param_names:
            dict_entries = []
            for qp in cluster.query_param_names:
                clean_qp = sanitize_identifier(qp)
                dict_entries.append(f"'{qp}': {clean_qp}")
            client_lines.append(
                f"        params = {{k: v for k, v in {{{', '.join(dict_entries)}}}.items() if v is not None}}"
            )
        else:
            client_lines.append("        params = None")

        # Execute request
        if is_graphql:
            # Inline query if captured
            query_str = ""
            for ex in cluster.exchanges:
                if isinstance(ex.request_body, dict) and ex.request_body.get("query"):
                    query_str = ex.request_body["query"].strip()
                    break

            if query_str:
                escaped_query = query_str.replace('"""', '\\"\\"\\"')
                client_lines.extend(
                    [
                        f'        query = """{escaped_query}"""',
                        "        payload = {'query': query, 'variables': variables or {}}",
                    ]
                )
            else:
                client_lines.append(
                    "        payload = {'operationName': "
                    + (f"'{cluster.operation_name}'" if cluster.operation_name else "None")
                    + ", 'variables': variables or {}}"
                )

            client_lines.append(
                f"        response = self.client.request('{cluster.method}', path, params=params, json=payload)"
            )
        elif cluster.method in ("POST", "PUT", "PATCH"):
            client_lines.append(
                f"        response = self.client.request('{cluster.method}', path, params=params, json=body)"
            )
        else:
            client_lines.append(
                f"        response = self.client.request('{cluster.method}', path, params=params)"
            )

        client_lines.extend(
            [
                "        response.raise_for_status()",
                "        data = response.json()",
            ]
        )

        if resp_model:
            client_lines.append(f"        return {resp_model}.model_validate(data)")
        else:
            client_lines.append("        return data")

        client_lines.append("")

    code_sections.append("\n".join(client_lines))

    # Smoke-test block
    smoke_test = [
        "",
        'if __name__ == "__main__":',
        f'    print("Initializing {client_name}...")',
        f'    with {client_name}() as client:',
        f'        print(f"Connected to {{client.base_url}} successfully")',
        "",
    ]
    code_sections.append("\n".join(smoke_test))

    full_python_code = "\n".join(code_sections)

    # Validate Python syntax via AST
    try:
        ast.parse(full_python_code)
    except SyntaxError as e:
        raise ValueError(f"Generated Python code contains syntax errors: {e}") from e

    return full_python_code, env_content
