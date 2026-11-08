using System.Net.Http.Json;
using System.Text.Json.Serialization;
using DotnetProjects.Data;
using DotnetProjects.Models;
using Microsoft.EntityFrameworkCore;

namespace DotnetProjects.Services;

public record GitHubRepo(
    [property: JsonPropertyName("full_name")] string FullName,
    [property: JsonPropertyName("description")] string? Description,
    [property: JsonPropertyName("language")] string? Language,
    [property: JsonPropertyName("private")] bool Private,
    [property: JsonPropertyName("archived")] bool Archived,
    [property: JsonPropertyName("fork")] bool Fork,
    [property: JsonPropertyName("open_issues_count")] int OpenIssuesCount,
    [property: JsonPropertyName("pushed_at")] DateTime? PushedAt);

/// <summary>Logique metier du module projects (seule porte d'entree pour les autres modules).</summary>
public class ProjectsService(AppDbContext db, HttpClient http, IConfiguration config)
{
    public const int InactivityDays = 90;

    public async Task<List<GitHubRepo>> FetchReposAsync(CancellationToken ct = default)
    {
        var owner = config["GitHub:Owner"] ?? "NizardV";
        var repos = new List<GitHubRepo>();
        for (var page = 1; ; page++)
        {
            var batch = await http.GetFromJsonAsync<List<GitHubRepo>>(
                $"users/{owner}/repos?per_page=100&page={page}", ct) ?? [];
            if (batch.Count == 0) return repos;
            repos.AddRange(batch.Where(r => !r.Fork));
        }
    }

    public async Task<int> SyncAsync(IEnumerable<GitHubRepo> raw, CancellationToken ct = default)
    {
        var count = 0;
        foreach (var r in raw)
        {
            var entity = await db.Repos.FirstOrDefaultAsync(x => x.FullName == r.FullName, ct);
            if (entity is null) db.Repos.Add(entity = new TrackedRepo { FullName = r.FullName });
            entity.Description = r.Description ?? "";
            entity.Language = r.Language ?? "";
            entity.IsPrivate = r.Private;
            entity.IsArchived = r.Archived;
            entity.OpenIssues = r.OpenIssuesCount;
            entity.PushedAt = r.PushedAt;
            entity.SyncedAt = DateTime.UtcNow;
            count++;
        }
        await db.SaveChangesAsync(ct);
        return count;
    }

    public async Task<int> RunSyncAsync(CancellationToken ct = default) =>
        await SyncAsync(await FetchReposAsync(ct), ct);

    public static Verdict ComputeVerdict(TrackedRepo repo)
    {
        if (repo.IsArchived) return Verdict.AArchiver;
        if (repo.PushedAt is null) return Verdict.ARelancer;
        return DateTime.UtcNow - repo.PushedAt.Value > TimeSpan.FromDays(InactivityDays)
            ? Verdict.ARelancer : Verdict.Actif;
    }
}
