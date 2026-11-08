namespace DotnetProjects.Services;

/// <summary>Planificateur integre : relance la synchro toutes les 24 h (equivalent d'un beat).</summary>
public class SyncWorker(IServiceScopeFactory scopes, ILogger<SyncWorker> log) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromHours(24));
        do
        {
            try
            {
                using var scope = scopes.CreateScope();
                var n = await scope.ServiceProvider.GetRequiredService<ProjectsService>().RunSyncAsync(ct);
                log.LogInformation("Synchro planifiee : {Count} repos", n);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                log.LogError(ex, "Echec de la synchro planifiee");
            }
        } while (await timer.WaitForNextTickAsync(ct));
    }
}
