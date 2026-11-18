"""JSON Schema inference and compact field extraction for token-efficient inspection."""

from __future__ import annotations

from typing import Any, Optional


def infer_json_schema(data: Any) -> dict[str, Any]:
    """Infer a standard JSON Schema from a Python data structure.

    Args:
        data: Parsed JSON payload (dict, list, str, int, float, bool, None).

    Returns:
        A JSON Schema dictionary (draft-7 compatible).
    """
    if data is None:
        return {"type": "null"}

    # In Python, bool is a subclass of int, so check bool first!
    if isinstance(data, bool):
        return {"type": "boolean"}

    if isinstance(data, int):
        return {"type": "integer"}

    if isinstance(data, float):
        return {"type": "number"}

    if isinstance(data, str):
        return {"type": "string"}

    if isinstance(data, list):
        if not data:
            return {"type": "array", "items": {}}

        # Merge schemas across all elements in the list
        item_schema = infer_json_schema(data[0])
        for item in data[1:]:
            item_schema = merge_schemas(item_schema, infer_json_schema(item))

        return {"type": "array", "items": item_schema}

    if isinstance(data, dict):
        properties: dict[str, Any] = {}
        required: list[str] = []

        for key, value in data.items():
            str_key = str(key)
            properties[str_key] = infer_json_schema(value)
            required.append(str_key)

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": sorted(required),
        }
        return schema

    # Fallback
    return {"type": "string"}


def merge_schemas(
    schema1: Optional[dict[str, Any]],
    schema2: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Merge two JSON schemas into a unified schema representing both observed samples."""
    if not schema1:
        return schema2 or {}
    if not schema2:
        return schema1 or {}

    t1 = schema1.get("type")
    t2 = schema2.get("type")

    # If both are objects
    if t1 == "object" and t2 == "object":
        p1 = schema1.get("properties", {})
        p2 = schema2.get("properties", {})
        all_keys = set(p1.keys()) | set(p2.keys())

        merged_props: dict[str, Any] = {}
        for k in all_keys:
            if k in p1 and k in p2:
                merged_props[k] = merge_schemas(p1[k], p2[k])
            elif k in p1:
                merged_props[k] = p1[k]
            else:
                merged_props[k] = p2[k]

        # Only fields present in both are guaranteed required
        r1 = set(schema1.get("required", []))
        r2 = set(schema2.get("required", []))
        common_required = sorted(list(r1 & r2))

        return {
            "type": "object",
            "properties": merged_props,
            "required": common_required,
        }

    # If both are arrays
    if t1 == "array" and t2 == "array":
        i1 = schema1.get("items", {})
        i2 = schema2.get("items", {})
        merged_items = merge_schemas(i1, i2)
        return {"type": "array", "items": merged_items}

    # If types are the same
    if t1 == t2:
        return schema1

    # Mixed / union types
    types1 = [t1] if isinstance(t1, str) else list(t1 or [])
    types2 = [t2] if isinstance(t2, str) else list(t2 or [])
    combined_types = sorted(list(set(types1 + types2)))

    if len(combined_types) == 1:
        return {"type": combined_types[0]}
    return {"type": combined_types}


def extract_compact_fields(
    data: Any,
    prefix: str = "",
    max_depth: int = 8,
) -> list[str]:
    """Extract a flattened, dot-notated list of fields and data types.

    Ideal for token-efficient LLM prompt inclusion and quick signature discovery.

    Example output:
        ['id: integer', 'user.name: string', 'user.tags[]: string', 'items[].sku: string']
    """
    if max_depth <= 0:
        return [f"{prefix}: any"] if prefix else []

    fields: set[str] = set()

    if isinstance(data, dict):
        for key, val in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(val, dict):
                subfields = extract_compact_fields(val, prefix=path, max_depth=max_depth - 1)
                fields.update(subfields)
            elif isinstance(val, list):
                if not val:
                    fields.add(f"{path}[]: empty_array")
                else:
                    subfields = extract_compact_fields(val, prefix=f"{path}[]", max_depth=max_depth - 1)
                    fields.update(subfields)
            else:
                type_name = _get_primitive_type_name(val)
                fields.add(f"{path}: {type_name}")

    elif isinstance(data, list):
        if not data:
            fields.add(f"{prefix}: empty_array" if prefix else "[]: empty_array")
        else:
            list_prefix = prefix if prefix.endswith("[]") else f"{prefix}[]" if prefix else "[]"
            # Sample first few elements to keep inspection efficient
            sample_items = data[:3]
            for item in sample_items:
                if isinstance(item, dict):
                    subfields = extract_compact_fields(item, prefix=list_prefix, max_depth=max_depth - 1)
                    fields.update(subfields)
                elif isinstance(item, list):
                    subfields = extract_compact_fields(item, prefix=f"{list_prefix}[]", max_depth=max_depth - 1)
                    fields.update(subfields)
                else:
                    type_name = _get_primitive_type_name(item)
                    fields.add(f"{list_prefix}: {type_name}")
    else:
        type_name = _get_primitive_type_name(data)
        fields.add(f"{prefix}: {type_name}" if prefix else f"value: {type_name}")

    return sorted(list(fields))


def _get_primitive_type_name(val: Any) -> str:
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "boolean"
    if isinstance(val, int):
        return "integer"
    if isinstance(val, float):
        return "number"
    if isinstance(val, str):
        return "string"
    return "any"

