using System.Net;
using DotnetProjects.Data;
using DotnetProjects.Models;
using DotnetProjects.Services;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;

namespace DotnetProjects.Tests;

public class ProjectsTests
{
    private static GitHubRepo Raw() =>
        new("NizardV/demo", "d", "C#", false, false, false, 2, DateTime.UtcNow.AddDays(-5));

    private static (AppDbContext db, SqliteConnection conn) NewDb()
    {
        var conn = new SqliteConnection("Data Source=:memory:");
        conn.Open();
        var db = new AppDbContext(new DbContextOptionsBuilder<AppDbContext>().UseSqlite(conn).Options);
        db.Database.EnsureCreated();
        return (db, conn);
    }

    [Fact]
    public async Task Sync_est_idempotent()
    {
        var (db, conn) = NewDb();
        using var _ = conn;
        var svc = new ProjectsService(db, new HttpClient(), new Microsoft.Extensions.Configuration.ConfigurationBuilder().Build());
        await svc.SyncAsync([Raw()]);
        await svc.SyncAsync([Raw()]);
        Assert.Equal(1, await db.Repos.CountAsync());
    }

    [Fact]
    public void Verdict_inactif()
    {
        var repo = new TrackedRepo { FullName = "x/y", PushedAt = DateTime.UtcNow.AddDays(-200) };
        Assert.Equal(Verdict.ARelancer, ProjectsService.ComputeVerdict(repo));
    }

    [Fact]
    public async Task Liste_exige_une_connexion()
    {
        await using var factory = new WebApplicationFactory<Program>();
        var client = factory.CreateClient(new() { AllowAutoRedirect = false });
        var resp = await client.GetAsync("/projects");
        Assert.Equal(HttpStatusCode.Redirect, resp.StatusCode);
    }
}
