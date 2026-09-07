from django.urls import path

from . import views, views_publico

urlpatterns = [
    # Área do profissional (login obrigatório)
    path("agendamentos/", views.agendamentos, name="agendamentos"),
    path("agendamentos/<int:pk>/situacao/<str:nova>/", views.mudar_situacao, name="mudar_situacao"),
    path("servicos/", views.servicos, name="servicos"),
    path("servicos/<int:pk>/alternar/", views.alternar_servico, name="alternar_servico"),
    path("disponibilidade/", views.disponibilidade, name="disponibilidade"),
    path("disponibilidade/<int:pk>/remover/", views.remover_janela, name="remover_janela"),
    path("clientes/", views.clientes, name="clientes"),

    # Fluxo público (sem login)
    path("agendar/<slug:slug>/", views_publico.escolher_servico, name="publico_servico"),
    path("agendar/<slug:slug>/<int:servico_id>/", views_publico.escolher_horario, name="publico_horario"),
    path("agendar/<slug:slug>/<int:servico_id>/confirmar/", views_publico.confirmar, name="publico_confirmar"),
    path("comprovante/<uuid:codigo>/", views_publico.sucesso, name="publico_sucesso"),
    path("comprovante/<uuid:codigo>/cancelar/", views_publico.cancelar_pelo_cliente, name="publico_cancelar"),
]