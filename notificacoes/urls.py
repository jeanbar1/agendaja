from django.urls import path

from . import views

urlpatterns = [
    # Endereço cadastrado no painel da Meta.
    path("webhook/whatsapp/", views.webhook, name="webhook_whatsapp"),

    # Acompanhamento pelo profissional.
    path("notificacoes/", views.historico, name="notificacoes"),
]