"""
Reage a mudanças no agendamento programando ou cancelando notificações.

Usar sinal em vez de chamar direto na view garante que o agendamento criado
pelo admin, por script ou por futura API também gere as notificações.
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

    except Exception as erro:
        # Falha ao notificar nunca pode impedir o agendamento de ser salvo.
        logger.exception("Erro ao programar notificações: %s", erro)