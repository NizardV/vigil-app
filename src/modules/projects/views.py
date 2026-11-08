from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import TrackedRepo
from .services import run_sync


@login_required
def repo_list(request):
    return render(request, "projects/repo_list.html", {"repos": TrackedRepo.objects.all()})


@login_required
@require_POST  # CSRF vérifié automatiquement par Django
def repo_sync(request):
    run_sync()
    return redirect("repo_list")
