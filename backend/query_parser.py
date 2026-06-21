"""Advanced query parser with support for complex search operators."""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedQuery:
    """Parsed query structure."""
    main_query: str
    field_queries: dict  # e.g., {'title': 'Engineer', 'location': 'NYC'}
    exact_phrases: list  # e.g., ["Senior Engineer", "Remote"]
    exclude_terms: list  # terms with NOT
    or_terms: list  # terms with OR
    boolean_and: bool = True


def parse_advanced_query(query: str) -> ParsedQuery:
    """
    Parse advanced query with support for:
    - "exact phrase" searches
    - Boolean operators: AND, OR, NOT
    - Wildcards: Senior*, Engineer?
    - Field-specific: title:Engineer location:NYC salary:>100k
    """
    if not query or not query.strip():
        return ParsedQuery(main_query="", field_queries={}, exact_phrases=[], exclude_terms=[], or_terms=[])

    query = query.strip()
    field_queries = {}
    exact_phrases = []
    exclude_terms = []
    or_terms = []
    main_query_parts = []
    boolean_and = True

    # Extract exact phrases first (in quotes)
    phrase_pattern = r'"([^"]+)"'
    exact_phrases = re.findall(phrase_pattern, query)
    query_without_phrases = re.sub(phrase_pattern, "", query)

    # Extract field-specific queries (e.g., title:Engineer, location:NYC)
    field_pattern = r'(\w+):([^\s]+)'
    field_matches = re.findall(field_pattern, query_without_phrases)
    for field, value in field_matches:
        if field.lower() in ['title', 'company', 'location', 'salary', 'source']:
            field_queries[field.lower()] = value
            query_without_phrases = re.sub(rf'{field}:{value}', '', query_without_phrases)

    # Handle boolean operators
    if ' OR ' in query_without_phrases:
        boolean_and = False
        or_terms = [term.strip() for term in query_without_phrases.split(' OR ')]
    elif ' NOT ' in query_without_phrases:
        not_pattern = r'NOT\s+(\w+)'
        exclude_terms = re.findall(not_pattern, query_without_phrases, re.IGNORECASE)
        query_without_phrases = re.sub(not_pattern, '', query_without_phrases, flags=re.IGNORECASE)

    # Extract main query (remaining terms, excluding AND for cleanup)
    query_without_phrases = re.sub(r'\s+AND\s+', ' ', query_without_phrases, flags=re.IGNORECASE)
    main_query_parts = [term.strip() for term in query_without_phrases.split() if term.strip()]
    main_query = ' '.join(main_query_parts)

    return ParsedQuery(
        main_query=main_query,
        field_queries=field_queries,
        exact_phrases=exact_phrases,
        exclude_terms=exclude_terms,
        or_terms=or_terms,
        boolean_and=boolean_and,
    )


def expand_wildcards(term: str) -> str:
    """
    Expand wildcard patterns to SQL LIKE patterns.
    Examples:
    - Senior* -> Senior%
    - *Engineer -> %Engineer
    - Seni?r -> Seni_r
    """
    term = term.replace('*', '%').replace('?', '_')
    return term


def build_sql_filter(parsed_query: ParsedQuery) -> tuple[str, list]:
    """
    Build SQL WHERE clause and parameters from parsed query.
    Returns: (where_clause, parameters)
    """
    conditions = []
    params = []

    # Main query search (title, company, description)
    if parsed_query.main_query:
        # Expand wildcards
        search_term = expand_wildcards(parsed_query.main_query)
        # Search in multiple fields
        if parsed_query.boolean_and:
            conditions.append(
                "(title LIKE ? OR company LIKE ? OR description LIKE ?)"
            )
            params.extend([search_term, search_term, search_term])
        else:
            # OR mode: create condition for each part
            or_conditions = []
            for term in parsed_query.or_terms:
                term_expanded = expand_wildcards(term)
                or_conditions.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
                params.extend([term_expanded, term_expanded, term_expanded])
            conditions.append("(" + " OR ".join(or_conditions) + ")")

    # Field-specific queries
    if parsed_query.field_queries:
        for field, value in parsed_query.field_queries.items():
            if field == 'title':
                conditions.append("title LIKE ?")
                params.append(expand_wildcards(value))
            elif field == 'location':
                conditions.append("location LIKE ?")
                params.append(f"%{value}%")
            elif field == 'company':
                conditions.append("company LIKE ?")
                params.append(f"%{value}%")
            elif field == 'source':
                conditions.append("source = ?")
                params.append(value)
            elif field == 'salary':
                # Handle salary ranges: >100k, <150k, 100k-150k
                salary_range = parse_salary_query(value)
                if salary_range['min']:
                    conditions.append("salary_min >= ?")
                    params.append(salary_range['min'])
                if salary_range['max']:
                    conditions.append("salary_max <= ?")
                    params.append(salary_range['max'])

    # Exact phrase queries
    if parsed_query.exact_phrases:
        for phrase in parsed_query.exact_phrases:
            conditions.append("(title LIKE ? OR description LIKE ?)")
            params.extend([f"%{phrase}%", f"%{phrase}%"])

    # Exclude terms
    if parsed_query.exclude_terms:
        for term in parsed_query.exclude_terms:
            conditions.append("title NOT LIKE ? AND description NOT LIKE ?")
            params.extend([f"%{term}%", f"%{term}%"])

    # Combine conditions
    if conditions:
        where_clause = " AND ".join(conditions)
        return where_clause, params
    else:
        return "1=1", []


def parse_salary_query(salary_str: str) -> dict:
    """
    Parse salary query formats:
    - >100k, <150k: greater than/less than
    - 100k-150k: range
    Returns: {'min': int, 'max': int}
    """
    result = {'min': None, 'max': None}

    # Remove 'k' suffix and convert to actual numbers
    salary_str = salary_str.lower().replace('k', '000').replace(',', '')

    if '-' in salary_str:
        # Range format: 100k-150k
        parts = salary_str.split('-')
        try:
            result['min'] = int(parts[0].strip())
            result['max'] = int(parts[1].strip())
        except ValueError:
            pass
    elif salary_str.startswith('>'):
        # Greater than format
        try:
            result['min'] = int(salary_str[1:].strip())
        except ValueError:
            pass
    elif salary_str.startswith('<'):
        # Less than format
        try:
            result['max'] = int(salary_str[1:].strip())
        except ValueError:
            pass

    return result


def suggest_search_operators() -> list[dict]:
    """Return suggestions for search operators."""
    return [
        {
            "operator": '"phrase"',
            "description": "Search for exact phrase",
            "example": '"Senior Software Engineer"',
        },
        {
            "operator": "title:term",
            "description": "Search within job title",
            "example": "title:Engineer",
        },
        {
            "operator": "location:term",
            "description": "Search by location",
            "example": "location:NYC",
        },
        {
            "operator": "company:term",
            "description": "Search by company",
            "example": "company:Google",
        },
        {
            "operator": "salary:range",
            "description": "Search by salary range",
            "example": "salary:100k-150k",
        },
        {
            "operator": "term*",
            "description": "Wildcard: match anything after",
            "example": "Senior*",
        },
        {
            "operator": "term1 OR term2",
            "description": "Boolean OR: match either",
            "example": "Python OR Java",
        },
        {
            "operator": "term1 NOT term2",
            "description": "Boolean NOT: exclude term",
            "example": "Engineer NOT Junior",
        },
        {
            "operator": "term1 AND term2",
            "description": "Boolean AND: match both (default)",
            "example": "Python AND Remote",
        },
    ]
