"""
Webhook do WhatsApp e tela de acompanhamento das notificações.

O webhook fecha o ciclo: quando o cliente toca em "Confirmar presença" ou
"Preciso cancelar", a Meta envia um evento e o agendamento é atualizado
sozinho, sem o profissional intervir.
"""

import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from agenda.models import Agendamento
from contas.views import obter_profissional

from . import envio, mensagens
from .models import Notificacao
from .canais import obter_canal      

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook(request):
    """Recebe eventos da Meta.

    GET é a verificação inicial (devolve o desafio).
    POST traz mensagens e status de entrega.

    csrf_exempt porque quem chama é a Meta, que não tem o token CSRF.
    A autenticidade vem do token combinado no cadastro.
    """
    if request.method == "GET":
        return _verificar_assinatura(request)

    try:
        dados = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"erro": "corpo inválido"}, status=400)

    try:
        _processar_evento(dados)
    except Exception as erro:
        # Responder com erro faria a Meta reenviar o evento indefinidamente.
        logger.exception("Erro ao processar webhook: %s", erro)

    return HttpResponse("EVENT_RECEIVED", status=200)


def _verificar_assinatura(request):
    modo = request.GET.get("hub.mode")
    token = request.GET.get("hub.verify_token")
    desafio = request.GET.get("hub.challenge", "")

    if modo == "subscribe" and token == settings.WHATSAPP_VERIFY_TOKEN:
        return HttpResponse(desafio, content_type="text/plain")

    return HttpResponseForbidden("Token de verificação inválido.")


def _processar_evento(dados):
    for entrada in dados.get("entry", []):
        for alteracao in entrada.get("changes", []):
            valor = alteracao.get("value", {})

            for status in valor.get("statuses", []):
                _atualizar_entrega(status)

            for mensagem in valor.get("messages", []):
                _tratar_mensagem(mensagem)


def _atualizar_entrega(status):
    id_mensagem = status.get("id")
    situacao = status.get("status")
    if not id_mensagem:
        return

    equivalencia = {
        "delivered": Notificacao.Situacao.ENTREGUE,
        "read": Notificacao.Situacao.LIDA,
        "failed": Notificacao.Situacao.FALHOU,
    }
    nova = equivalencia.get(situacao)
    if nova is None:
        return

    Notificacao.objects.filter(id_mensagem=id_mensagem).update(situacao=nova)


def _tratar_mensagem(mensagem):
    telefone = mensagem.get("from", "")
    if not telefone:
        return
    telefone_e164 = telefone if telefone.startswith("+") else f"+{telefone}"

    acao = _identificar_acao(mensagem)
    if acao is None:
        return

    if acao == "descadastrar":
        _descadastrar(telefone_e164)
        return

    agendamento = _agendamento_mais_proximo(telefone_e164)
    if agendamento is None:
        logger.info("Resposta de %s sem agendamento ativo.", telefone_e164)
        return

    if acao == "confirmar":
        agendamento.status = Agendamento.Status.CONFIRMADO
        agendamento.confirmado_em = timezone.now()
        agendamento.save(update_fields=["status", "confirmado_em"])
        resposta = (
            f"Presença confirmada para {agendamento.inicio:%d/%m às %H:%M}. "
            "Até lá!"
        )
    else:
        agendamento.status = Agendamento.Status.CANCELADO
        agendamento.cancelado_em = timezone.now()
        agendamento.motivo_cancelamento = "Cancelado pelo cliente via WhatsApp"
        agendamento.save(
            update_fields=["status", "cancelado_em", "motivo_cancelamento"]
        )
        envio.cancelar_pendentes(agendamento)
        envio.avisar_cancelamento_pelo_cliente(agendamento)
        resposta = (
            f"Agendamento de {agendamento.inicio:%d/%m às %H:%M} cancelado. "
            "Quando quiser, é só marcar outro horário."
        )

    # A resposta do cliente abriu a janela de 24h: texto livre é permitido
    # aqui, e não consome template nem gera custo.
    obter_canal().enviar_texto(telefone_e164, resposta)


def _identificar_acao(mensagem):
    """Reconhece tanto o toque no botão quanto a palavra digitada."""
    tipo = mensagem.get("type")

    if tipo == "button":
        texto = (mensagem.get("button", {}).get("text") or "").lower()
    elif tipo == "interactive":
        interativo = mensagem.get("interactive", {})
        texto = (
            interativo.get("button_reply", {}).get("title")
            or interativo.get("list_reply", {}).get("title")
            or ""
        ).lower()
    elif tipo == "text":
        texto = (mensagem.get("text", {}).get("body") or "").strip().lower()
    else:
        return None

    if any(p in texto for p in mensagens.PALAVRAS_DESCADASTRO):
        return "descadastrar"
    if "confirmar" in texto or texto in mensagens.PALAVRAS_CONFIRMAR:
        return "confirmar"
    if "cancelar" in texto or texto in mensagens.PALAVRAS_CANCELAR:
        return "cancelar"
    return None


def _agendamento_mais_proximo(telefone):
    return (
        Agendamento.objects.filter(
            cliente__telefone=telefone,
            status__in=Agendamento.STATUS_OCUPAM_AGENDA,
            inicio__gte=timezone.now(),
        )
        .select_related("cliente", "servico", "profissional")
        .order_by("inicio")
        .first()
    )


def _descadastrar(telefone):
    """Revoga o consentimento — LGPD e política da Meta."""
    from clientes.models import Cliente

    for cliente in Cliente.objects.filter(telefone=telefone):
        for consentimento in cliente.consentimentos.filter(revogado_em__isnull=True):
            consentimento.revogar()

    obter_canal().enviar_texto(
        telefone,
        "Pronto, você não vai mais receber mensagens automáticas. "
        "Seus agendamentos continuam valendo.",
    )


@login_required
def historico(request):
    """Lista as notificações do profissional logado."""
    profissional = obter_profissional(request)
    if profissional is None:
        return render(request, "painel/sem_perfil.html")

    situacao = request.GET.get("situacao", "")

    consulta = Notificacao.objects.filter(
        agendamento__profissional=profissional
    ).select_related("agendamento", "agendamento__cliente", "agendamento__servico")

    if situacao:
        consulta = consulta.filter(situacao=situacao)

    notificacoes = list(consulta[:100])

    # O link de envio manual aparece para tudo que ainda não saiu: é o
    # caminho garantido, que não depende de canal nem de serviço externo.
    manuais = {Notificacao.Situacao.PENDENTE, Notificacao.Situacao.FALHOU}
    for n in notificacoes:
        if n.situacao in manuais:
            n.link_manual = envio.link_manual(n)

    contexto = {
        "profissional": profissional,
        "notificacoes": notificacoes,
        "situacao": situacao,
        "situacoes": Notificacao.Situacao.choices,
        "canal_ativo": obter_canal().nome,
        "modo_simulacao": obter_canal().nome == "simulado",
        "pendentes": Notificacao.objects.filter(
            agendamento__profissional=profissional,
            situacao=Notificacao.Situacao.PENDENTE,
        ).count(),
    }
    return render(request, "notificacoes/historico.html", contexto)