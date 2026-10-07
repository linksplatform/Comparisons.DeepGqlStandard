using GraphQL;
using GraphQL.SystemTextJson;
using GraphQL.Types;
using Platform.Data;
using Platform.Data.Doublets;
using Platform.Data.Doublets.Memory.United.Generic;
using Platform.Memory;

var builder = WebApplication.CreateBuilder(args);
builder.Logging.ClearProviders();
if (Environment.GetEnvironmentVariable("BENCHMARK_TRACE") == "1")
{
    builder.Logging.AddConsole();
}
builder.WebHost.UseUrls(Environment.GetEnvironmentVariable("LISTEN_ADDRESS") ?? "http://127.0.0.1:8002");
using var store = new LinksStore();
using var schema = Schema.For(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "links.graphql")), config =>
{
    config.Types.For("query_root").FieldFor("links").Resolver = new GraphQL.Resolvers.FuncFieldResolver<object>(context =>
        store.Query(context.GetArgument<Filter?>("where"), context.GetArgument<List<OrderBy>?>("order_by"),
            context.GetArgument<int?>("limit"), context.GetArgument<int?>("offset")));
    config.Types.For("mutation_root").FieldFor("insert_links").Resolver = new GraphQL.Resolvers.FuncFieldResolver<object>(context =>
        store.Insert(context.GetArgument<List<Endpoints>>("objects")));
    config.Types.For("mutation_root").FieldFor("update_links").Resolver = new GraphQL.Resolvers.FuncFieldResolver<object>(context =>
        store.Update(context.GetArgument<Filter>("where"), context.GetArgument<Endpoints?>("_set")));
    config.Types.For("mutation_root").FieldFor("delete_links").Resolver = new GraphQL.Resolvers.FuncFieldResolver<object>(context =>
        store.Delete(context.GetArgument<Filter>("where")));
});
schema.RegisterType(new BigintGraphType());
schema.Initialize();
var serializer = new GraphQLSerializer();
var executer = new DocumentExecuter();
var app = builder.Build();
app.MapPost("/v1/graphql", async (HttpContext context) =>
{
    var request = await serializer.ReadAsync<GraphQL.Transport.GraphQLRequest>(context.Request.Body, context.RequestAborted);
    var result = await executer.ExecuteAsync(options =>
    {
        options.Schema = schema;
        options.Query = request!.Query;
        options.Variables = request.Variables;
        options.OperationName = request.OperationName;
        options.CancellationToken = context.RequestAborted;
    });
    context.Response.ContentType = "application/json";
    await serializer.WriteAsync(context.Response.Body, result, context.RequestAborted);
});
Console.Error.WriteLine("C# Doublets GraphQL started");
await app.RunAsync();

public sealed class BigintGraphType : LongGraphType
{
    public BigintGraphType() { Name = "bigint"; }
}

public sealed record Row(long id, long from_id, long to_id);
public sealed record MutationResponse(int affected_rows, List<Row> returning);
public sealed class Endpoints
{
    public long? from_id { get; set; }
    public long? to_id { get; set; }
}
public sealed class OrderBy { public string? id { get; set; } }
public sealed class Comparison
{
    public long? _eq { get; set; }
    public List<long>? _in { get; set; }
    public long? _gte { get; set; }
    public long? _lte { get; set; }
    public bool Matches(long value) => (!_eq.HasValue || _eq == value)
        && (_in == null || _in.Contains(value)) && (!_gte.HasValue || value >= _gte)
        && (!_lte.HasValue || value <= _lte);
}
public sealed class Filter
{
    public Comparison? id { get; set; }
    public Comparison? from_id { get; set; }
    public Comparison? to_id { get; set; }
    public List<Filter>? _and { get; set; }
    public List<Filter>? _or { get; set; }
    public Filter? _not { get; set; }
    public bool Matches(Row row) => (id?.Matches(row.id) ?? true)
        && (from_id?.Matches(row.from_id) ?? true) && (to_id?.Matches(row.to_id) ?? true)
        && (_and?.All(f => f.Matches(row)) ?? true) && (_or?.Any(f => f.Matches(row)) ?? true)
        && !(_not?.Matches(row) ?? false);
}

/// <summary>Data.Doublets.Gql benchmark adapter: native equality indexes with remaining predicates evaluated on matched rows.</summary>
public sealed class LinksStore : IDisposable
{
    private readonly ILinks<ulong> links = new UnitedMemoryLinks<ulong>(new HeapResizableDirectMemory());
    private readonly object gate = new();

    private List<Row> Select(Filter? filter)
    {
        filter ??= new Filter();
        var equalities = new[] { filter.id?._eq, filter.from_id?._eq, filter.to_id?._eq };
        if (equalities.Any(value => value < 0)) { return []; }
        var query = equalities.Select(value => value.HasValue ? (ulong)value.Value : links.Constants.Any).ToArray();
        var rows = new List<Row>();
        var queries = filter.id?._eq == null && filter.id?._in != null
            ? filter.id._in.Where(id => id >= 0).Distinct().Select(id => new[] { (ulong)id, query[1], query[2] }).ToList()
            : new List<ulong[]> { query };
        foreach (var candidate in queries)
        {
            links.Each(link =>
            {
                var row = new Row((long)link![0], (long)link[1], (long)link[2]);
                if (filter.Matches(row)) { rows.Add(row); }
                return links.Constants.Continue;
            }, candidate);
        }
        return rows.OrderBy(row => row.id).ToList();
    }

    public List<Row> Query(Filter? filter, List<OrderBy>? order, int? limit, int? offset)
    {
        if (limit < 0 || offset < 0) { throw new ExecutionError("limit and offset must be nonnegative"); }
        lock (gate)
        {
            var rows = Select(filter);
            if (order?.FirstOrDefault(item => item.id != null)?.id == "desc") { rows.Reverse(); }
            return rows.Skip(offset ?? 0).Take(limit ?? int.MaxValue).ToList();
        }
    }
    private static ulong Endpoint(long? value, long fallback = 0)
    {
        var endpoint = value ?? fallback;
        if (endpoint < 0) { throw new ExecutionError("Doublets endpoints must be nonnegative"); }
        return (ulong)endpoint;
    }
    public MutationResponse Insert(List<Endpoints> objects)
    {
        var pairs = objects.Select(item => (Source: Endpoint(item.from_id), Target: Endpoint(item.to_id))).ToList();
        lock (gate)
        {
            var rows = new List<Row>();
            foreach (var pair in pairs)
            {
                var id = links.Create();
                links.Update(id, pair.Source, pair.Target);
                rows.Add(new Row((long)id, (long)pair.Source, (long)pair.Target));
            }
            return new MutationResponse(rows.Count, rows);
        }
    }
    public MutationResponse Update(Filter filter, Endpoints? set)
    {
        if (set == null) { throw new ExecutionError("_set is required for update"); }
        lock (gate)
        {
            var updates = Select(filter).Select(row => (row.id, Source: Endpoint(set.from_id, row.from_id), Target: Endpoint(set.to_id, row.to_id))).ToList();
            var rows = new List<Row>();
            foreach (var item in updates)
            {
                links.Update((ulong)item.id, item.Source, item.Target);
                rows.Add(new Row(item.id, (long)item.Source, (long)item.Target));
            }
            return new MutationResponse(rows.Count, rows);
        }
    }
    public MutationResponse Delete(Filter filter)
    {
        lock (gate)
        {
            var rows = Select(filter);
            foreach (var row in rows.AsEnumerable().Reverse()) { links.Delete((ulong)row.id, handler: null); }
            return new MutationResponse(rows.Count, rows);
        }
    }
    public void Dispose() => (links as IDisposable)?.Dispose();
}
