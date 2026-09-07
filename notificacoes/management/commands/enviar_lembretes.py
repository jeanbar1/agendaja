"""
Processa a fila de notificações pendentes.

Executado periodicamente pelo Agendador de Tarefas do Windows.
Optou-se por comando agendado em vez de Celery com Redis: o Redis não roda
nativamente no Windows e a fila tem volume baixo — a complexidade de um
broker não se justificaria.

Uso:
    python manage.py enviar_lembretes
    python manage.py enviar_lembretes --simular
    python manage.py enviar_lembretes --limite 20
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from notificacoes import envio
from notificacoes.models import Notificacao
from notificacoes.canais import obter_canal    # era: from notificacoes.whatsapp import ...


class Command(BaseCommand):
    help = "Envia as notificações pendentes cuja hora já chegou."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limite", type=int, default=100,
            help="Máximo de mensagens por execução (padrão: 100).",
        )
        parser.add_argument(
            "--simular", action="store_true",
            help="Mostra o que seria enviado, sem enviar nem alterar nada.",
        )

    def handle(self, *args, **opcoes):
        agora = timezone.localtime()
        self.stdout.write(f"Execução em {agora:%d/%m/%Y %H:%M:%S}")

        canal = obter_canal()
        self.stdout.write(f"Canal: {canal.nome}")
        if canal.nome == "simulado":
            self.stdout.write(
                self.style.WARNING(
                    "Modo de simulação: as mensagens são registradas, "
                    "mas não saem de fato."
                )
            )

        if opcoes["simular"]:
            self._apenas_listar(opcoes["limite"])
            return

        resultado = envio.enviar_pendentes(limite=opcoes["limite"], canal=canal)

        self.stdout.write(self.style.SUCCESS(
            f"Enviadas: {resultado['enviadas']} | "
            f"Falhas: {resultado['falhas']} | "
            f"Puladas: {resultado['puladas']}"
        ))

    def _apenas_listar(self, limite):
        pendentes = (
            Notificacao.objects.filter(
                situacao=Notificacao.Situacao.PENDENTE,
                agendada_para__lte=timezone.now(),
            )
            .select_related("agendamento__cliente")
            .order_by("agendada_para")[:limite]
        )

        if not pendentes:
            self.stdout.write("Nada pendente no momento.")
            return

        self.stdout.write(f"{len(pendentes)} mensagem(ns) seriam enviadas:\n")
        for n in pendentes:
            self.stdout.write(
                f"  [{n.get_tipo_display()}] {n.agendamento.cliente.nome} "
                f"({n.telefone}) — venceu em {n.agendada_para:%d/%m %H:%M}"
            )