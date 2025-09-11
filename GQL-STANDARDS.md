# GraphQL Standards for Associative Architecture

## Overview

GraphQL standards (GQL standards) in the LinksStandard ecosystem define a standardized GraphQL schema and API for working with associative databases. These standards ensure consistent interfaces across different implementations of associative data storage systems.

## What are GQL Standards?

GQL standards are GraphQL schema specifications that define:

1. **Uniform Interface**: A consistent GraphQL API across different associative database implementations
2. **Interoperability**: Standard operations that work the same way whether using PostgreSQL+Hasura, Doublets C#, or Doublets Rust
3. **Associative Operations**: Specialized GraphQL operations designed for associative data structures

## Core Schema Components

### Links Type
The fundamental data structure in associative architecture:

```graphql
type links {
  id: bigint!
  from_id: bigint!
  to_id: bigint!
  type_id: bigint
  from: links
  to: links
  type: links
  typed: [links!]!
}
```

### Standard Queries

#### Basic Queries
- `links`: Query links with filtering, sorting, and pagination
- `links_by_pk`: Get a single link by primary key
- `links_aggregate`: Aggregate operations on links

#### Query Arguments
- `distinct_on`: Remove duplicate results
- `limit`: Limit number of results
- `offset`: Skip results for pagination  
- `order_by`: Sort results
- `where`: Filter conditions

### Standard Mutations

#### Insert Operations
```graphql
insert_links(objects: [links_insert_input!]!): links_mutation_response
insert_links_one(object: links_insert_input!): links
```

#### Update Operations
```graphql
update_links(where: links_bool_exp!, _set: links_set_input): links_mutation_response
update_links_by_pk(pk_columns: links_pk_columns_input!, _set: links_set_input): links
```

#### Delete Operations
```graphql
delete_links(where: links_bool_exp!): links_mutation_response
delete_links_by_pk(id: bigint!): links
```

### Boolean Expressions
Complex filtering capabilities using boolean expressions:
- `_and`, `_or`, `_not`: Logical operators
- `_eq`, `_neq`: Equality comparisons
- `_gt`, `_gte`, `_lt`, `_lte`: Numerical comparisons
- `_in`, `_nin`: Array membership
- `_is_null`: Null checks

## Why Standards Instead of Servers?

As mentioned in the issue discussion: "The standard is the same – implementations (servers) are different."

### Benefits of Standards
1. **Consistency**: Same GraphQL API across different backend technologies
2. **Portability**: Applications can switch between implementations without changing client code  
3. **Interoperability**: Tools and clients work with any compliant implementation
4. **Evolution**: Standards can evolve while maintaining backward compatibility

### Implementation Variations
- **Deep (PostgreSQL + Hasura)**: Traditional relational database with GraphQL layer
- **Doublets Rust**: High-performance native associative database
- **Doublets C#**: .NET-based associative database implementation

## Associative Architecture Features

### Triple Structure
Every link represents a triple: `(from, to, type)`
- `from_id`: Source link identifier
- `to_id`: Target link identifier  
- `type_id`: Optional type classifier

### Self-Referential Nature
Links can reference other links, enabling:
- Hierarchical structures
- Semantic relationships
- Meta-programming capabilities
- Graph-based data modeling

### Performance Characteristics
- Optimized for relationship-heavy data
- Efficient traversal operations
- Minimal storage overhead
- Scalable associative operations

## Compliance Requirements

For an implementation to be GQL standards compliant:

1. **Schema Compatibility**: Must implement the core links type and operations
2. **Query Support**: All standard query operations must be supported
3. **Mutation Support**: Standard CRUD operations must work as specified
4. **Boolean Expressions**: Full filtering capability support
5. **Type System**: Proper GraphQL type definitions and relationships

## Example Usage

### Creating Links
```graphql
mutation {
  insert_links(objects: [
    { from_id: 1, to_id: 2, type_id: 3 },
    { from_id: 2, to_id: 3, type_id: 3 }
  ]) {
    returning { id from_id to_id type_id }
  }
}
```

### Querying Relationships
```graphql
query {
  links(where: { type_id: { _eq: 3 } }) {
    id
    from { id }
    to { id }
    type { id }
  }
}
```

### Complex Filtering
```graphql
query {
  links(where: {
    _and: [
      { from_id: { _gt: 100 } },
      { type_id: { _in: [1, 2, 3] } }
    ]
  }) {
    id from_id to_id type_id
  }
}
```

## Implementation Testing

This repository provides benchmarking tools to compare different GQL standards implementations:

- **Performance Testing**: Automated load testing with configurable scenarios
- **Compatibility Testing**: Ensures all implementations handle the same operations
- **Consistency Verification**: Validates identical results across implementations

## Conclusion

GQL standards provide a unified GraphQL interface for associative database operations, enabling consistent development experience across different backend implementations while maintaining the flexibility to choose the most appropriate technology stack for specific use cases.