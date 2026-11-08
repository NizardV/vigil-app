from django.urls import path

from . import views

urlpatterns = [
    path("", views.repo_list, name="repo_list"),
    path("sync/", views.repo_sync, name="repo_sync"),
]
