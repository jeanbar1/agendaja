"""
Fluxo público de agendamento.

Estas telas não exigem login: o cliente agenda informando apenas nome e
telefone (RNF04). É o caminho que aparece na demonstração da defesa.
"""

from datetime import datetime

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from clientes.models import Cliente, ConsentimentoOptIn
from contas.models import Profissional

from .horarios import horarios_disponiveis, proximos_dias_com_horarios
from .models import Agendamento, Servico

TEXTO_CONSENTIMENTO = (
    "Autorizo o envio de mensagens sobre este agendamento pelo WhatsApp. "
    "Posso cancelar o envio a qualquer momento respondendo SAIR."
)


def escolher_servico(request, slug):
    """Passo 1: o cliente escolhe o serviço."""
    profissional = get_object_or_404(Profissional, slug=slug, ativo=True)
    servicos = Servico.objects.filter(profissional=profissional, ativo=True)

    return render(
        request,
        "publico/escolher_servico.html",
        {"profissional": profissional, "servicos": servicos},
    )


def escolher_horario(request, slug, servico_id):
    """Passo 2: o cliente escolhe dia e horário."""
    profissional = get_object_or_404(Profissional, slug=slug, ativo=True)
    servico = get_object_or_404(
        Servico, pk=servico_id, profissional=profissional, ativo=True
    )

    dias = proximos_dias_com_horarios(
        profissional, servico, dias=profissional.antecedencia_maxima_dias
    )

    # Dia selecionado: o informado na URL ou o primeiro com vaga.
    parametro = request.GET.get("dia")
    selecionado = None
    if parametro:
        try:
            selecionado = datetime.strptime(parametro, "%Y-%m-%d").date()
        except ValueError:
            selecionado = None
    if selecionado is None and dias:
        selecionado = dias[0][0]

    horarios = []
    if selecionado:
        horarios = horarios_disponiveis(profissional, servico, selecionado)

    return render(
        request,
        "publico/escolher_horario.html",
        {
            "profissional": profissional,
            "servico": servico,
            "dias": dias[:30],
            "dia_selecionado": selecionado,
            "horarios": horarios,
        },
    )


def confirmar(request, slug, servico_id):
    """Passo 3: dados do cliente e gravação do agendamento."""
    profissional = get_object_or_404(Profissional, slug=slug, ativo=True)
    servico = get_object_or_404(
        Servico, pk=servico_id, profissional=profissional, ativo=True
    )

    inicio_texto = request.POST.get("inicio") or request.GET.get("inicio")
    if not inicio_texto:
        messages.error(request, "Escolha um horário para continuar.")
        return redirect("publico_horario", slug=slug, servico_id=servico.pk)

    inicio = datetime.fromisoformat(inicio_texto)
    if timezone.is_naive(inicio):
        inicio = timezone.make_aware(inicio, timezone.get_current_timezone())

    # Etapa de coleta dos dados
    if request.method == "GET" or "nome" not in request.POST:
        return render(
            request,
            "publico/confirmar.html",
            {
                "profissional": profissional,
                "servico": servico,
                "inicio": inicio,
                "fim": inicio + servico.duracao_total,
                "texto_consentimento": TEXTO_CONSENTIMENTO,
            },
        )

    nome = request.POST.get("nome", "").strip()
    telefone = request.POST.get("telefone", "").strip()
    observacoes = request.POST.get("observacoes", "").strip()
    aceite = request.POST.get("consentimento") == "on"

    erros = []
    if len(nome) < 3:
        erros.append("Informe seu nome completo.")

    # Normaliza o telefone para E.164, aceitando o formato que o brasileiro digita.
    somente_digitos = "".join(c for c in telefone if c.isdigit())
    if len(somente_digitos) in (10, 11):
        telefone_e164 = f"+55{somente_digitos}"
    elif len(somente_digitos) in (12, 13) and somente_digitos.startswith("55"):
        telefone_e164 = f"+{somente_digitos}"
    else:
        telefone_e164 = ""
        erros.append("Informe um telefone válido com DDD, como (77) 99999-8888.")

    if not aceite:
        erros.append("É necessário autorizar o envio de mensagens para agendar.")

    if erros:
        for erro in erros:
            messages.error(request, erro)
        return render(
            request,
            "publico/confirmar.html",
            {
                "profissional": profissional,
                "servico": servico,
                "inicio": inicio,
                "fim": inicio + servico.duracao_total,
                "nome": nome,
                "telefone": telefone,
                "observacoes": observacoes,
                "texto_consentimento": TEXTO_CONSENTIMENTO,
            },
        )

    local = profissional.locais.filter(principal=True).first()

    try:
        with transaction.atomic():
            cliente, criado = Cliente.objects.get_or_create(
                profissional=profissional,
                telefone=telefone_e164,
                defaults={"nome": nome},
            )
            if not cliente.consentimentos.filter(revogado_em__isnull=True).exists():
                ConsentimentoOptIn.objects.create(
                    cliente=cliente,
                    origem=ConsentimentoOptIn.Origem.AGENDAMENTO_ONLINE,
                    texto_apresentado=TEXTO_CONSENTIMENTO,
                )

            agendamento = Agendamento.objects.create(
                profissional=profissional,
                cliente=cliente,
                servico=servico,
                local=local,
                inicio=inicio,
                observacoes=observacoes,
            )
    except IntegrityError:
        # A restrição de exclusão do banco recusou: alguém pegou o horário
        # entre a exibição da grade e o envio do formulário.
        messages.error(
            request,
            "Esse horário acabou de ser preenchido. Escolha outro, por favor.",
        )
        return redirect("publico_horario", slug=slug, servico_id=servico.pk)

    return redirect("publico_sucesso", codigo=agendamento.codigo)


def sucesso(request, codigo):
    """Passo 4: comprovante com o código do agendamento."""
    agendamento = get_object_or_404(
        Agendamento.objects.select_related("profissional", "servico", "cliente", "local"),
        codigo=codigo,
    )
    return render(request, "publico/sucesso.html", {"agendamento": agendamento})


def cancelar_pelo_cliente(request, codigo):
    """Cancelamento pelo próprio cliente, via link do comprovante."""
    agendamento = get_object_or_404(
        Agendamento.objects.select_related("profissional", "servico", "cliente"),
        codigo=codigo,
    )

    if request.method == "POST":
        if agendamento.status in Agendamento.STATUS_OCUPAM_AGENDA:
            agendamento.status = Agendamento.Status.CANCELADO
            agendamento.cancelado_em = timezone.now()
            agendamento.motivo_cancelamento = "Cancelado pelo cliente"
            agendamento.save()
            # Avisa o profissional: sem isso ele só descobriria o horário
            # vago ao abrir o sistema.
            from notificacoes import envio as envio_notificacoes
            envio_notificacoes.avisar_cancelamento_pelo_cliente(agendamento)
            messages.success(request, "Agendamento cancelado.")
        return redirect("publico_sucesso", codigo=codigo)

    return render(request, "publico/cancelar.html", {"agendamento": agendamento})