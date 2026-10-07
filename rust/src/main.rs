use async_graphql::{
    Context, EmptySubscription, Enum, InputObject, InputValueError, InputValueResult, Object,
    Scalar, ScalarType, Schema, SimpleObject, Value,
};
use axum::{Json, Router, extract::State, routing::post};
use doublets::{Doublets, Links, data::Flow, mem::Global, unit};
use std::sync::Mutex;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Bigint(i64);

#[Scalar(name = "bigint")]
impl ScalarType for Bigint {
    fn parse(value: Value) -> InputValueResult<Self> {
        match value {
            Value::Number(n) => n
                .as_i64()
                .map(Self)
                .ok_or_else(|| InputValueError::custom("Expected signed 64-bit integer")),
            Value::String(s) => s.parse().map(Self).map_err(InputValueError::custom),
            value => Err(InputValueError::expected_type(value)),
        }
    }
    fn to_value(&self) -> Value {
        Value::from(self.0)
    }
}

#[derive(SimpleObject, Clone)]
#[graphql(name = "links", rename_fields = "snake_case")]
struct Link {
    id: Bigint,
    from_id: Option<Bigint>,
    to_id: Option<Bigint>,
}

#[derive(InputObject, Default)]
#[graphql(name = "bigint_comparison_exp")]
struct Comparison {
    #[graphql(name = "_eq")]
    eq: Option<Bigint>,
    #[graphql(name = "_in")]
    inside: Option<Vec<Bigint>>,
    #[graphql(name = "_gte")]
    gte: Option<Bigint>,
    #[graphql(name = "_lte")]
    lte: Option<Bigint>,
}
impl Comparison {
    fn matches(&self, value: Bigint) -> bool {
        self.eq.is_none_or(|v| v == value)
            && self.inside.as_ref().is_none_or(|v| v.contains(&value))
            && self.gte.is_none_or(|v| value.0 >= v.0)
            && self.lte.is_none_or(|v| value.0 <= v.0)
    }
}

#[derive(InputObject, Default)]
#[graphql(name = "links_bool_exp", rename_fields = "snake_case")]
struct Filter {
    id: Option<Comparison>,
    from_id: Option<Comparison>,
    to_id: Option<Comparison>,
    #[graphql(name = "_and")]
    and: Option<Vec<Filter>>,
    #[graphql(name = "_or")]
    or: Option<Vec<Filter>>,
    #[graphql(name = "_not")]
    not: Option<Box<Filter>>,
}
impl Filter {
    fn matches(&self, link: &Link) -> bool {
        self.id.as_ref().is_none_or(|c| c.matches(link.id))
            && self
                .from_id
                .as_ref()
                .is_none_or(|c| c.matches(link.from_id.unwrap()))
            && self
                .to_id
                .as_ref()
                .is_none_or(|c| c.matches(link.to_id.unwrap()))
            && self
                .and
                .as_ref()
                .is_none_or(|items| items.iter().all(|f| f.matches(link)))
            && self
                .or
                .as_ref()
                .is_none_or(|items| items.iter().any(|f| f.matches(link)))
            && self.not.as_ref().is_none_or(|f| !f.matches(link))
    }
}

#[derive(InputObject)]
#[graphql(name = "links_insert_input", rename_fields = "snake_case")]
struct Insert {
    from_id: Option<Bigint>,
    to_id: Option<Bigint>,
}
#[derive(InputObject)]
#[graphql(name = "links_set_input", rename_fields = "snake_case")]
struct Set {
    from_id: Option<Bigint>,
    to_id: Option<Bigint>,
}
#[derive(Enum, Copy, Clone, Eq, PartialEq)]
#[graphql(name = "order_by", rename_items = "snake_case")]
enum Order {
    Asc,
    Desc,
}
#[derive(InputObject)]
#[graphql(name = "links_order_by")]
struct OrderBy {
    id: Option<Order>,
}
#[derive(SimpleObject)]
#[graphql(name = "links_mutation_response", rename_fields = "snake_case")]
struct Response {
    affected_rows: i32,
    returning: Vec<Link>,
}
impl Response {
    fn new(returning: Vec<Link>) -> Self {
        Self {
            affected_rows: returning.len() as i32,
            returning,
        }
    }
}

type RawStore = unit::Store<usize, Global<unit::LinkPart<usize>>>;
struct Store(RawStore);
impl Store {
    fn select(&self, filter: &Filter) -> Vec<Link> {
        let constants = self.0.constants();
        let eq = |comparison: &Option<Comparison>| comparison.as_ref().and_then(|c| c.eq);
        // Push equality restrictions into Doublets' native indexes, then evaluate all predicates.
        let constraints = [eq(&filter.id), eq(&filter.from_id), eq(&filter.to_id)];
        if constraints.iter().flatten().any(|v| v.0 < 0) {
            return Vec::new();
        }
        let query = constraints.map(|v| v.map_or(constants.any, |v| v.0 as usize));
        let mut rows = Vec::new();
        let queries = if constraints[0].is_none() {
            if let Some(ids) = filter.id.as_ref().and_then(|c| c.inside.as_ref()) {
                ids.iter()
                    .filter(|id| id.0 >= 0)
                    .map(|id| id.0 as usize)
                    .collect::<std::collections::BTreeSet<_>>()
                    .into_iter()
                    .map(|id| [id, query[1], query[2]])
                    .collect::<Vec<_>>()
            } else {
                vec![query]
            }
        } else {
            vec![query]
        };
        for query in queries {
            self.0.each_by(query, |link| {
                let row = Link {
                    id: Bigint(link.index as i64),
                    from_id: Some(Bigint(link.source as i64)),
                    to_id: Some(Bigint(link.target as i64)),
                };
                if filter.matches(&row) {
                    rows.push(row);
                }
                Flow::Continue
            });
        }
        rows.sort_by_key(|row| row.id.0);
        rows
    }
}
struct Query;
#[Object(
    name = "query_root",
    rename_fields = "snake_case",
    rename_args = "snake_case"
)]
impl Query {
    async fn links(
        &self,
        ctx: &Context<'_>,
        r#where: Option<Filter>,
        order_by: Option<Vec<OrderBy>>,
        limit: Option<i32>,
        offset: Option<i32>,
    ) -> async_graphql::Result<Vec<Link>> {
        if limit.is_some_and(|n| n < 0) || offset.is_some_and(|n| n < 0) {
            return Err("limit and offset must be nonnegative".into());
        }
        let store = ctx
            .data_unchecked::<Mutex<Store>>()
            .lock()
            .map_err(|_| "Store lock poisoned")?;
        let mut rows = store.select(&r#where.unwrap_or_default());
        if order_by
            .as_ref()
            .and_then(|items| items.iter().find_map(|order| order.id))
            == Some(Order::Desc)
        {
            rows.reverse();
        }
        Ok(rows
            .into_iter()
            .skip(offset.unwrap_or(0) as usize)
            .take(limit.map_or(usize::MAX, |n| n as usize))
            .collect())
    }
}
struct Mutation;
fn endpoint(value: Option<Bigint>, fallback: i64) -> async_graphql::Result<usize> {
    let value = value.map_or(fallback, |v| v.0);
    if value < 0 {
        return Err("Doublets endpoints must be nonnegative".into());
    }
    Ok(value as usize)
}
#[Object(
    name = "mutation_root",
    rename_fields = "snake_case",
    rename_args = "snake_case"
)]
impl Mutation {
    async fn insert_links(
        &self,
        ctx: &Context<'_>,
        objects: Vec<Insert>,
    ) -> async_graphql::Result<Option<Response>> {
        let pairs = objects
            .into_iter()
            .map(|o| Ok((endpoint(o.from_id, 0)?, endpoint(o.to_id, 0)?)))
            .collect::<async_graphql::Result<Vec<_>>>()?;
        let mut store = ctx
            .data_unchecked::<Mutex<Store>>()
            .lock()
            .map_err(|_| "Store lock poisoned")?;
        let mut rows = Vec::new();
        for (source, target) in pairs {
            let id = store.0.create().map_err(|e| e.to_string())?;
            store
                .0
                .update(id, source, target)
                .map_err(|e| e.to_string())?;
            rows.push(Link {
                id: Bigint(id as i64),
                from_id: Some(Bigint(source as i64)),
                to_id: Some(Bigint(target as i64)),
            });
        }
        Ok(Some(Response::new(rows)))
    }
    async fn update_links(
        &self,
        ctx: &Context<'_>,
        r#where: Filter,
        #[graphql(name = "_set")] set: Option<Set>,
    ) -> async_graphql::Result<Option<Response>> {
        let mut store = ctx
            .data_unchecked::<Mutex<Store>>()
            .lock()
            .map_err(|_| "Store lock poisoned")?;
        let set = set.ok_or("_set is required for update")?;
        // Resolve the selection before changing index trees.
        let rows = store.select(&r#where);
        let updates = rows
            .into_iter()
            .map(|row| {
                Ok((
                    row.id,
                    endpoint(set.from_id, row.from_id.unwrap().0)?,
                    endpoint(set.to_id, row.to_id.unwrap().0)?,
                ))
            })
            .collect::<async_graphql::Result<Vec<_>>>()?;
        let mut returning = Vec::new();
        for (id, source, target) in updates {
            store
                .0
                .update(id.0 as usize, source, target)
                .map_err(|e| e.to_string())?;
            returning.push(Link {
                id,
                from_id: Some(Bigint(source as i64)),
                to_id: Some(Bigint(target as i64)),
            });
        }
        Ok(Some(Response::new(returning)))
    }
    async fn delete_links(
        &self,
        ctx: &Context<'_>,
        r#where: Filter,
    ) -> async_graphql::Result<Option<Response>> {
        let mut store = ctx
            .data_unchecked::<Mutex<Store>>()
            .lock()
            .map_err(|_| "Store lock poisoned")?;
        let rows = store.select(&r#where);
        for row in rows.iter().rev() {
            store
                .0
                .delete(row.id.0 as usize)
                .map_err(|e| e.to_string())?;
        }
        Ok(Some(Response::new(rows)))
    }
}

type GqlSchema = Schema<Query, Mutation, EmptySubscription>;
fn schema() -> GqlSchema {
    Schema::build(Query, Mutation, EmptySubscription)
        .data(Mutex::new(Store(
            RawStore::new(Global::new()).expect("Doublets store"),
        )))
        .limit_depth(32)
        .finish()
}
async fn graphql(
    State(schema): State<GqlSchema>,
    Json(request): Json<async_graphql::Request>,
) -> Json<async_graphql::Response> {
    Json(schema.execute(request).await)
}
#[tokio::main]
async fn main() {
    let address = std::env::var("LISTEN_ADDRESS").unwrap_or_else(|_| "127.0.0.1:8001".into());
    let listener = tokio::net::TcpListener::bind(&address)
        .await
        .expect("Bind server");
    eprintln!("Rust Doublets GraphQL: {address}/v1/graphql");
    let app = Router::new()
        .route("/v1/graphql", post(graphql))
        .with_state(schema())
        .layer(axum::extract::DefaultBodyLimit::max(8 * 1024 * 1024));
    axum::serve(listener, app).await.expect("HTTP server");
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn repeated_batch_mutations_preserve_indexes() {
        let (done, result) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let mut store = Store(RawStore::new(Global::new()).unwrap());
            for i in 1..=20 {
                let id = store.0.create().unwrap();
                store.0.update(id, i, i + 1).unwrap();
            }
            for _ in 0..4 {
                let ids = (1..=3)
                    .map(|i| {
                        let id = store.0.create().unwrap();
                        store.0.update(id, i, i).unwrap();
                        id
                    })
                    .collect::<Vec<_>>();
                for id in ids.into_iter().rev() {
                    store.0.delete(id).unwrap();
                }
            }
            for _ in 0..4 {
                for i in 1..=3 {
                    store.0.update(i, i, 1).unwrap();
                }
                for i in 1..=3 {
                    store.0.update(i, i, i + 1).unwrap();
                }
            }
            let rows = store.select(&Filter {
                from_id: Some(Comparison {
                    eq: Some(Bigint(1)),
                    ..Default::default()
                }),
                ..Default::default()
            });
            done.send(rows.iter().map(|row| row.id.0).collect::<Vec<_>>())
                .unwrap();
        });
        assert_eq!(
            result
                .recv_timeout(std::time::Duration::from_secs(5))
                .expect("Store hung during repeated create/delete/update"),
            vec![1]
        );
    }

    #[tokio::test]
    async fn partial_update_and_negative_identity() {
        let schema = schema();
        let inserted = schema
            .execute(
                "mutation { insert_links(objects: [{from_id: 1, to_id: 2}]) { affected_rows } }",
            )
            .await;
        assert!(inserted.errors.is_empty(), "{:?}", inserted.errors);
        let update = schema.execute("mutation { update_links(where: {id: {_eq: 1}}, _set: {to_id: 3}) { returning { from_id to_id } } }").await;
        assert!(update.errors.is_empty(), "{:?}", update.errors);
        assert_eq!(
            update.data.into_json().unwrap()["update_links"]["returning"][0],
            serde_json::json!({"from_id":1,"to_id":3})
        );
        let query = schema
            .execute("{ links(where: {id: {_eq: -1}}) { id } }")
            .await;
        assert!(query.errors.is_empty());
        assert_eq!(
            query.data.into_json().unwrap(),
            serde_json::json!({"links":[]})
        );
    }
}
