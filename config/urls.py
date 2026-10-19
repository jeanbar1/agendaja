"""
Roteamento principal do projeto.

Cada app declara as proprias rotas no urls.py dele e aqui eu so incluo.
A vantagem e que mexer nas telas da agenda nao obriga a abrir a
configuracao do projeto.
"""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    path("admin/", admin.site.urls),

    # --- PWA (RNF02) ---
    #
    # Os tres precisam ficar na raiz, e nao em /static/. O service
    # worker so controla a pasta em que ele esta e as de baixo - se
    # ficasse em /static/js/sw.js, controlaria so o /static/js/.
    path(
        "manifest.webmanifest",
        TemplateView.as_view(
            template_name="manifest.webmanifest",
            content_type="application/manifest+json",
        ),
        name="manifest",
    ),
    path(
        "sw.js",
        TemplateView.as_view(
            template_name="sw.js",
                        content_type="application/javascript; charset=utf-8",
        ),
        name="service_worker",
    ),
    path(
        "offline/",
        TemplateView.as_view(template_name="publico/offline.html"),
        name="offline",
    ),

    # Autenticacao e area do profissional
    path("", include("contas.urls")),

    # Agenda, cadastros e fluxo publico de agendamento
    path("", include("agenda.urls")),

    # Webhook do WhatsApp e historico de notificacoes
    path("", include("notificacoes.urls")),


    path("conta/", include("allauth.urls")),

        # Area de quem contrata servico
    path("", include("clientes.urls")),
    
]