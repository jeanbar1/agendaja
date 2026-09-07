"""
Regras de criação e disparo das notificações.

O envio em si é delegado ao canal configurado (notificacoes/canais.py);
aqui ficam apenas as regras de quando, para quem e o quê.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone

from agenda.models import Agendamento

from . import mensagens
from .canais import ErroEnvio, obter_canal
from .models import Notificacao

logger = logging.getLogger(__name__)

MAXIMO_TENTATIVAS = 3

# Cada tipo declara: o template, a função que monta os parâmetros e quem
# recebe. Concentrar isso aqui evita espalhar condicionais pelo código.
CONFIGURACAO = {
    Notificacao.Tipo.CONFIRMACAO: (
        mensagens.CONFIRMACAO,
        mensagens.parametros_confirmacao,
        Notificacao.Destinatario.CLIENTE,
    ),
    Notificacao.Tipo.LEMBRETE: (
        mensagens.LEMBRETE,
        mensagens.parametros_lembrete,
        Notificacao.Destinatario.CLIENTE,
    ),
    Notificacao.Tipo.CANCELAMENTO: (
        mensagens.CANCELAMENTO,
        mensagens.parametros_cancelamento,
        Notificacao.Destinatario.CLIENTE,
    ),
    Notificacao.Tipo.FEEDBACK: (
        mensagens.FEEDBACK,
        mensagens.parametros_feedback,
        Notificacao.Destinatario.CLIENTE,
    ),
    Notificacao.Tipo.NOVO_AGENDAMENTO: (
        mensagens.NOVO_AGENDAMENTO,
        mensagens.parametros_novo_agendamento,
        Notificacao.Destinatario.PROFISSIONAL,
    ),
    Notificacao.Tipo.CANCELAMENTO_CLIENTE: (
        mensagens.CANCELAMENTO_CLIENTE,
        mensagens.parametros_cancelamento_cliente,
        Notificacao.Destinatario.PROFISSIONAL,
    ),
}


def _telefone_do_destinatario(agendamento, destinatario):
    if destinatario == Notificacao.Destinatario.PROFISSIONAL:
        return agendamento.profissional.telefone
    return agendamento.cliente.telefone


def _pode_notificar(agendamento, destinatario):
    """Verifica se o envio é permitido.

    O consentimento é exigido apenas para o cliente: ele é o titular dos
    dados protegidos pela LGPD e o destinatário que a política da Meta
    protege. O profissional recebe avisos sobre a própria agenda, no
    telefone que ele mesmo cadastrou — não há terceiro envolvido.
    """
    if destinatario == Notificacao.Destinatario.PROFISSIONAL:
        return bool(agendamento.profissional.telefone)
    return agendamento.cliente.pode_receber_mensagem


def criar_notificacao(agendamento, tipo, agendada_para=None):
    template, montar, destinatario = CONFIGURACAO[tipo]

    if not _pode_notificar(agendamento, destinatario):
        logger.info(
            "Notificação %s não criada: destinatário %s indisponível.",
            tipo, destinatario,
        )
        return None

    parametros = montar(agendamento)

    if agendada_para is None:
        agendada_para = timezone.now()

    try:
        return Notificacao.objects.create(
            agendamento=agendamento,
            tipo=tipo,
            destinatario=destinatario,
            telefone=_telefone_do_destinatario(agendamento, destinatario),
            template=template.nome,
            conteudo=mensagens.texto_equivalente(template, parametros),
            agendada_para=agendada_para,
        )
    except IntegrityError:
        return None


def programar_do_agendamento(agendamento):
    """Confirmação e aviso ao profissional (imediatos) + lembrete."""
    criadas = []

    for tipo in (
        Notificacao.Tipo.CONFIRMACAO,
        Notificacao.Tipo.NOVO_AGENDAMENTO,
    ):
        notificacao = criar_notificacao(agendamento, tipo)
        if notificacao:
            criadas.append(notificacao)

    horas = getattr(settings, "LEMBRETE_ANTECEDENCIA_HORAS", 24)
    momento_lembrete = agendamento.inicio - timedelta(hours=horas)

    # Agendamento em cima da hora: o lembrete viraria duplicata.
    if momento_lembrete > timezone.now():
        lembrete = criar_notificacao(
            agendamento, Notificacao.Tipo.LEMBRETE, agendada_para=momento_lembrete
        )
        if lembrete:
            criadas.append(lembrete)

    return criadas


def avisar_cancelamento_pelo_cliente(agendamento):
    """Avisa o profissional de que o cliente desmarcou.

    Sem isso ele só descobriria o horário vago ao abrir o sistema.
    """
    return criar_notificacao(agendamento, Notificacao.Tipo.CANCELAMENTO_CLIENTE)


def cancelar_pendentes(agendamento, apenas_do_cliente=True):
    """Cancela notificações ainda não enviadas.

    Por padrão preserva os avisos dirigidos ao profissional, que continuam
    fazendo sentido mesmo depois do cancelamento.
    """
    pendentes = agendamento.notificacoes.filter(
        situacao=Notificacao.Situacao.PENDENTE
    )
    if apenas_do_cliente:
        pendentes = pendentes.filter(destinatario=Notificacao.Destinatario.CLIENTE)
    return pendentes.update(situacao=Notificacao.Situacao.CANCELADA)


def enviar(notificacao, canal=None):
    canal = canal or obter_canal()
    template, montar, _ = CONFIGURACAO[notificacao.tipo]
    parametros = montar(notificacao.agendamento)

    try:
        id_mensagem = canal.enviar(
            notificacao.telefone,
            template.nome,
            parametros,
            notificacao.conteudo,
        )
    except ErroEnvio as erro:
        notificacao.marcar_falha(erro)
        logger.warning("Falha ao enviar notificação %s: %s", notificacao.pk, erro)
        return False

    notificacao.marcar_enviada(canal.nome, id_mensagem)
    return True


def enviar_pendentes(limite=100, canal=None):
    canal = canal or obter_canal()
    agora = timezone.now()

    fila = (
        Notificacao.objects.filter(
            situacao=Notificacao.Situacao.PENDENTE,
            agendada_para__lte=agora,
            tentativas__lt=MAXIMO_TENTATIVAS,
        )
        .select_related(
            "agendamento", "agendamento__cliente", "agendamento__servico",
            "agendamento__profissional", "agendamento__local",
        )
        .order_by("agendada_para")[:limite]
    )

    resultado = {"enviadas": 0, "falhas": 0, "puladas": 0}

    for notificacao in fila:
        agendamento = notificacao.agendamento

        if (
            notificacao.tipo == Notificacao.Tipo.LEMBRETE
            and agendamento.status not in Agendamento.STATUS_OCUPAM_AGENDA
        ):
            notificacao.cancelar()
            resultado["puladas"] += 1
            continue

        if enviar(notificacao, canal=canal):
            resultado["enviadas"] += 1
        else:
            resultado["falhas"] += 1

    return resultado


def link_manual(notificacao):
    """Link wa.me para envio manual.

    Contingência que não depende de canal, credencial nem serviço externo.
    """
    return mensagens.link_wa_me(notificacao.telefone, notificacao.conteudo)