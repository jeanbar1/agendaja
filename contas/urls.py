"""
Rotas do app ``contas``: autenticacao e painel do profissional.
"""

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    # A vitrine do sistema. Antes isso aqui era um redirecionamento
    # pro painel; virou pagina propria pra quem chega pelo link
    # entender o que e antes de ver a tela de login.
    path("", views.home, name="inicio"),

    path(
        "entrar/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            # Quem ja entrou nao tem o que fazer na tela de login: mando
            # direto pra area dele. Antes o botao "Entrar" do menu do
            # navegador devolvia o formulario de novo, como se o login
            # nao tivesse funcionado.
            redirect_authenticated_user=True,
            # A tela de login e do Django, nao minha, entao passo o
            # aviso por aqui. E so isso que o template precisa pra
            # decidir se mostra o botao do Google.
            extra_context={"google_ativo": settings.LOGIN_COM_GOOGLE},
        ),
        name="login",
    ),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),

    # Serve pros dois casos: cadastro do zero e quem entrou pelo Google
    # e precisa completar o perfil.
    path("cadastrar/", views.cadastro, name="cadastro"),

    path("painel/", views.painel, name="painel"),
]