from collections.abc import Iterable
from sqlalchemy import String, and_, cast, func, or_

BLANK_FILTER_VALUE = "__blank__"


def apply_column_filters(
    query, params, mapping, exact_headers=(), exclude_headers=()
):
    """Apply cf_<visible header> filters before pagination."""
    exact_headers = set(exact_headers)
    exclude_headers = set(exclude_headers)
    grouped_values = {}
    for key, raw_value in params.multi_items():
        if not key.startswith("cf_"):
            continue
        header = key[3:]
        if header in exclude_headers:
            continue
        value = str(raw_value or "").strip()
        if not value or mapping.get(header) is None:
            continue
        grouped_values.setdefault(header, []).append(value)

    for header, values in grouped_values.items():
        expressions = mapping[header]
        if not isinstance(expressions, Iterable) or isinstance(
            expressions, (str, bytes)
        ):
            expressions = [expressions]
        expressions = list(expressions)
        value_conditions = []
        for value in dict.fromkeys(values):
            if value.lower() == BLANK_FILTER_VALUE:
                value_conditions.append(
                    and_(
                        *[
                            or_(
                                expression.is_(None),
                                func.trim(cast(expression, String)) == "",
                            )
                            for expression in expressions
                        ]
                    )
                )
            elif header in exact_headers:
                value_conditions.append(
                    or_(
                        *[
                            func.lower(func.trim(cast(expression, String)))
                            == value.lower()
                            for expression in expressions
                        ]
                    )
                )
            else:
                token = f"%{value}%"
                value_conditions.append(
                    or_(
                        *[
                            cast(expression, String).ilike(token)
                            for expression in expressions
                        ]
                    )
                )
        query = query.filter(or_(*value_conditions))
    return query