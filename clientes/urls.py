"""
Rotas do app ``clientes``: a area de quem contrata servico.
"""

from django.urls import path

from . import views

urlpatterns = [
    # A vitrine com todos os prestadores.
    path("explorar/", views.explorar, name="explorar"),

    # Os agendamentos da pessoa, de todos os prestadores juntos.
    path("meus-agendamentos/", views.meus_agendamentos, name="meus_agendamentos"),
]