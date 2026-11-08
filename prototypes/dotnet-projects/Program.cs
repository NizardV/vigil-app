using DotnetProjects.Data;
using DotnetProjects.Services;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddDbContext<AppDbContext>(o =>
    o.UseSqlite(builder.Configuration.GetConnectionString("Default") ?? "Data Source=projects.db"));

builder.Services.AddHttpClient<ProjectsService>(c =>
{
    c.BaseAddress = new Uri("https://api.github.com/");
    c.DefaultRequestHeaders.UserAgent.ParseAdd("vigil-prototype");
    c.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
    var token = builder.Configuration["GitHub:Token"];
    if (!string.IsNullOrEmpty(token)) c.DefaultRequestHeaders.Authorization = new("Bearer", token);
});

builder.Services.AddHostedService<SyncWorker>();
builder.Services.AddAuthentication("Cookies").AddCookie("Cookies", o => o.LoginPath = "/login");
builder.Services.AddAuthorization();
builder.Services.AddRazorPages(o => o.Conventions.AuthorizePage("/Projects"));

var app = builder.Build();

using (var scope = app.Services.CreateScope())
    scope.ServiceProvider.GetRequiredService<AppDbContext>().Database.Migrate();

app.UseAuthentication();
app.UseAuthorization();
app.MapRazorPages();
app.MapGet("/health", () => Results.Ok(new { status = "ok" }));

app.Run();

public partial class Program;
