from django.urls import path

from . import views, views_publico

urlpatterns = [
    # --- Painel do profissional -------------------------------------
    path("agendamentos/", views.agendamentos, name="agendamentos"),
    path("agendamentos/<int:pk>/situacao/<str:nova>/", views.mudar_situacao, name="mudar_situacao"),
    # O profissional le o que o cliente respondeu e decide se aceita.
    path("agendamentos/<int:pk>/respostas/", views.respostas, name="respostas"),

    path("servicos/", views.servicos, name="servicos"),
    path("servicos/<int:pk>/alternar/", views.alternar_servico, name="alternar_servico"),
    # Onde o profissional monta o questionario daquele servico.
    path("servicos/<int:pk>/perguntas/", views.perguntas, name="perguntas"),

    path("disponibilidade/", views.disponibilidade, name="disponibilidade"),
    path("disponibilidade/<int:pk>/remover/", views.remover_janela, name="remover_janela"),

    path("bloqueios/", views.bloqueios, name="bloqueios"),
    path("bloqueios/<int:pk>/remover/", views.remover_bloqueio, name="remover_bloqueio"),

    path("clientes/", views.clientes, name="clientes"),
    path("avaliacoes/", views.avaliacoes, name="avaliacoes"),

    # --- Fluxo publico, sem login -----------------------------------
    # Os quatro passos do cliente: escolhe o servico, escolhe o horario,
    # confirma e recebe o comprovante.
    path("agendar/<slug:slug>/", views_publico.escolher_servico, name="publico_servico"),
    path("agendar/<slug:slug>/<int:servico_id>/", views_publico.escolher_horario, name="publico_horario"),
    path("agendar/<slug:slug>/<int:servico_id>/confirmar/", views_publico.confirmar, name="publico_confirmar"),
    path("comprovante/<uuid:codigo>/", views_publico.sucesso, name="publico_sucesso"),

    # Cancelar e avaliar usam o UUID do agendamento, nao o id. Com o id
    # daria pra trocar o numero na URL e mexer no agendamento dos
    # outros; UUID ninguem adivinha.
    path("comprovante/<uuid:codigo>/cancelar/", views_publico.cancelar_pelo_cliente, name="publico_cancelar"),
    path("avaliar/<uuid:codigo>/", views_publico.avaliar, name="publico_avaliar"),
]