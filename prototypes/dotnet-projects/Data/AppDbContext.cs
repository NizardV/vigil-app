using DotnetProjects.Models;
using Microsoft.EntityFrameworkCore;

namespace DotnetProjects.Data;

public class AppDbContext(DbContextOptions<AppDbContext> options) : DbContext(options)
{
    public DbSet<TrackedRepo> Repos => Set<TrackedRepo>();

    protected override void OnModelCreating(ModelBuilder b) =>
        b.Entity<TrackedRepo>().HasIndex(r => r.FullName).IsUnique();
}
