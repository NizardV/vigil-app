from django.urls import path

from . import views

urlpatterns = [path("discord/interactions/", views.interactions, name="watch_interactions")]
