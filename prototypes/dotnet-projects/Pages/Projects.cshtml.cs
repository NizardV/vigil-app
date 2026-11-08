using DotnetProjects.Data;
using DotnetProjects.Models;
using DotnetProjects.Services;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace DotnetProjects.Pages;

// Antiforgery (CSRF) et [Authorize] appliques par convention dans Program.cs
public class ProjectsModel(AppDbContext db, ProjectsService service) : PageModel
{
    public List<TrackedRepo> Repos { get; private set; } = [];

    public async Task OnGetAsync() =>
        Repos = await db.Repos.OrderByDescending(r => r.PushedAt).ToListAsync();

    public async Task<IActionResult> OnPostSyncAsync()
    {
        await service.RunSyncAsync();
        return RedirectToPage();
    }
}
