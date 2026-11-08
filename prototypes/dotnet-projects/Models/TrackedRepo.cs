namespace DotnetProjects.Models;

public enum Verdict { Actif, ARelancer, AArchiver }

public class TrackedRepo
{
    public int Id { get; set; }
    public string FullName { get; set; } = "";
    public string Description { get; set; } = "";
    public string Language { get; set; } = "";
    public bool IsPrivate { get; set; }
    public bool IsArchived { get; set; }
    public int OpenIssues { get; set; }
    public DateTime? PushedAt { get; set; }
    public string NotionPageUrl { get; set; } = "";
    public DateTime SyncedAt { get; set; }
}
