from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from clientes.models import Cliente
from contas.views import obter_profissional

from .models import Agendamento, DiaSemana, Disponibilidade, Servico


@login_required
def agendamentos(request):
    """Lista de agendamentos com filtro por situação e período."""
    profissional = obter_profissional(request)
    if profissional is None:
        return render(request, "painel/sem_perfil.html")

    situacao = request.GET.get("situacao", "")
    periodo = request.GET.get("periodo", "futuros")

    consulta = Agendamento.objects.filter(
        profissional=profissional
    ).select_related("cliente", "servico", "local")

    agora = timezone.localtime()
    if periodo == "futuros":
        consulta = consulta.filter(inicio__gte=agora).order_by("inicio")
    elif periodo == "passados":
        consulta = consulta.filter(inicio__lt=agora).order_by("-inicio")
    else:
        consulta = consulta.order_by("-inicio")

    if situacao:
        consulta = consulta.filter(status=situacao)

    contexto = {
        "profissional": profissional,
        "agendamentos": consulta[:100],
        "situacao": situacao,
        "periodo": periodo,
        "situacoes": Agendamento.Status.choices,
    }
    return render(request, "agenda/agendamentos.html", contexto)


@login_required
def mudar_situacao(request, pk, nova):
    """Altera a situação de um agendamento a partir da lista."""
    profissional = obter_profissional(request)
    agendamento = get_object_or_404(
        Agendamento, pk=pk, profissional=profissional
    )

    validas = dict(Agendamento.Status.choices)
    if nova not in validas:
        messages.error(request, "Situação inválida.")
        return redirect("agendamentos")

    agendamento.status = nova
    if nova == Agendamento.Status.CONFIRMADO:
        agendamento.confirmado_em = timezone.now()
    elif nova == Agendamento.Status.CANCELADO:
        agendamento.cancelado_em = timezone.now()
    agendamento.save()

    messages.success(
        request,
        f"Agendamento de {agendamento.cliente.nome} marcado como "
        f"{validas[nova].lower()}.",
    )
    return redirect(request.META.get("HTTP_REFERER", "agendamentos"))


@login_required
def servicos(request):
    """Cadastro de serviços do profissional."""
    profissional = obter_profissional(request)
    if profissional is None:
        return render(request, "painel/sem_perfil.html")

    if request.method == "POST":
        nome = request.POST.get("nome", "").strip()
        duracao = request.POST.get("duracao_minutos") or 0
        intervalo = request.POST.get("intervalo_apos_minutos") or 0
        preco = request.POST.get("preco") or None
        modalidade = request.POST.get("modalidade", Servico.Modalidade.PRESENCIAL)

        if not nome:
            messages.error(request, "Informe o nome do serviço.")
        elif int(duracao) < 5:
            messages.error(request, "A duração mínima é de 5 minutos.")
        elif Servico.objects.filter(profissional=profissional, nome=nome).exists():
            messages.error(request, f'Você já tem um serviço chamado "{nome}".')
        else:
            Servico.objects.create(
                profissional=profissional,
                nome=nome,
                duracao_minutos=int(duracao),
                intervalo_apos_minutos=int(intervalo),
                preco=preco or None,
                modalidade=modalidade,
            )
            messages.success(request, f'Serviço "{nome}" cadastrado.')
        return redirect("servicos")

    contexto = {
        "profissional": profissional,
        "servicos": Servico.objects.filter(profissional=profissional),
        "modalidades": Servico.Modalidade.choices,
    }
    return render(request, "agenda/servicos.html", contexto)


@login_required
def alternar_servico(request, pk):
    """Ativa ou desativa um serviço."""
    profissional = obter_profissional(request)
    servico = get_object_or_404(Servico, pk=pk, profissional=profissional)
    servico.ativo = not servico.ativo
    servico.save(update_fields=["ativo"])
    estado = "ativado" if servico.ativo else "desativado"
    messages.success(request, f'Serviço "{servico.nome}" {estado}.')
    return redirect("servicos")


@login_required
def disponibilidade(request):
    """Grade semanal de atendimento."""
    profissional = obter_profissional(request)
    if profissional is None:
        return render(request, "painel/sem_perfil.html")

    if request.method == "POST":
        dia = request.POST.get("dia_semana")
        inicio = request.POST.get("hora_inicio")
        fim = request.POST.get("hora_fim")
        local_id = request.POST.get("local") or None

        if not (dia and inicio and fim):
            messages.error(request, "Preencha dia, início e fim.")
        elif fim <= inicio:
            messages.error(request, "O fim precisa ser depois do início.")
        else:
            Disponibilidade.objects.create(
                profissional=profissional,
                dia_semana=int(dia),
                hora_inicio=inicio,
                hora_fim=fim,
                local_id=local_id,
            )
            messages.success(request, "Janela de atendimento adicionada.")
        return redirect("disponibilidade")

    janelas = Disponibilidade.objects.filter(
        profissional=profissional
    ).select_related("local")

    # Agrupa por dia da semana para exibir a grade
    grade = []
    for valor, rotulo in DiaSemana.choices:
        grade.append(
            {
                "valor": valor,
                "rotulo": rotulo,
                "janelas": [j for j in janelas if j.dia_semana == valor],
            }
        )

    contexto = {
        "profissional": profissional,
        "grade": grade,
        "dias": DiaSemana.choices,
        "locais": profissional.locais.filter(ativo=True),
    }
    return render(request, "agenda/disponibilidade.html", contexto)


@login_required
def remover_janela(request, pk):
    profissional = obter_profissional(request)
    janela = get_object_or_404(Disponibilidade, pk=pk, profissional=profissional)
    janela.delete()
    messages.success(request, "Janela removida.")
    return redirect("disponibilidade")


@login_required
def clientes(request):
    """Carteira de clientes do profissional."""
    profissional = obter_profissional(request)
    if profissional is None:
        return render(request, "painel/sem_perfil.html")

    busca = request.GET.get("busca", "").strip()
    consulta = Cliente.objects.filter(profissional=profissional)
    if busca:
        consulta = consulta.filter(nome__icontains=busca)

    contexto = {
        "profissional": profissional,
        "clientes": consulta.prefetch_related("consentimentos")[:200],
        "busca": busca,
    }
    return render(request, "agenda/clientes.html", contexto)