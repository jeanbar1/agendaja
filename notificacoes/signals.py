"""
Reage as mudancas no agendamento programando ou cancelando notificacoes.

Usar sinal em vez de chamar direto na view garante que o agendamento
criado pelo admin, por script ou por uma futura API tambem gere as
notificacoes - sem depender de alguem lembrar de chamar a funcao em cada
lugar.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from agenda.models import Agendamento

from . import envio

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Agendamento, dispatch_uid="agendamento_notificacoes")
def ao_salvar_agendamento(sender, instance, created, **kwargs):
    try:
        if created:
            envio.programar_do_agendamento(instance)
            return

        encerrados = [
            Agendamento.Status.CANCELADO,
            Agendamento.Status.CONCLUIDO,
            Agendamento.Status.FALTOU,
        ]
        if instance.status in encerrados:
            envio.cancelar_pendentes(instance)

        # Cancelou: o cliente precisa ser avisado.
        #
        # Esse aviso tem que nascer DEPOIS do cancelar_pendentes acima,
        # pelo mesmo motivo do pedido de avaliacao: ele vai pro cliente,
        # e o cancelar_pendentes derruba tudo que vai pro cliente e
        # ainda nao saiu. Criado antes, ele morreria na mesma hora.
        if instance.status == Agendamento.Status.CANCELADO:
            envio.avisar_cancelamento(instance)

        # Atendimento concluido: peco a avaliacao (RF13).
        #
        # A ordem aqui importa. O cancelar_pendentes acima derruba as
        # mensagens do cliente que ainda nao sairam, entao o pedido de
        # avaliacao tem que ser criado DEPOIS dele - senao ele nasceria
        # e morreria na mesma hora.
        #
        # So peco avaliacao de atendimento concluido. Quem cancelou ou
        # faltou nao tem o que avaliar.
        if instance.status == Agendamento.Status.CONCLUIDO:
            envio.pedir_avaliacao(instance)

    except Exception as erro:
        # Falha ao notificar nunca pode impedir o agendamento de ser
        # salvo: o compromisso com o cliente vale mais que a mensagem.
        logger.exception("Erro ao programar notificações: %s", erro)