from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("contas.urls")),
    path("", include("agenda.urls")),
    path("", include("notificacoes.urls")),   # nova
]