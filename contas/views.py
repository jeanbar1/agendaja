from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from agenda.horarios import taxa_de_falta
from agenda.models import Agendamento
from clientes.models import Cliente


def obter_profissional(request):
    """Devolve o profissional do usuário logado, ou ``None``."""
    return getattr(request.user, "profissional", None)


@login_required
def painel(request):
    """Visão do dia: o que acontece hoje e os indicadores do mês."""
    profissional = obter_profissional(request)
    if profissional is None:
        return render(request, "painel/sem_perfil.html")

    agora = timezone.localtime()
    hoje = agora.date()
    inicio_hoje = timezone.make_aware(
        timezone.datetime.combine(hoje, timezone.datetime.min.time()),
        timezone.get_current_timezone(),
    )
    fim_hoje = inicio_hoje + timedelta(days=1)

    agendamentos_hoje = (
        Agendamento.objects.filter(
            profissional=profissional,
            inicio__gte=inicio_hoje,
            inicio__lt=fim_hoje,
        )
        .select_related("cliente", "servico", "local")
        .order_by("inicio")
    )

    proximos = (
        Agendamento.objects.filter(
            profissional=profissional,
            inicio__gte=fim_hoje,
            status__in=Agendamento.STATUS_OCUPAM_AGENDA,
        )
        .select_related("cliente", "servico")
        .order_by("inicio")[:8]
    )

    inicio_mes = inicio_hoje.replace(day=1)
    indicadores = taxa_de_falta(profissional, inicio=inicio_mes)

    contexto = {
        "profissional": profissional,
        "hoje": hoje,
        "agendamentos_hoje": agendamentos_hoje,
        "proximos": proximos,
        "total_hoje": agendamentos_hoje.count(),
        "confirmados_hoje": agendamentos_hoje.filter(
            status=Agendamento.Status.CONFIRMADO
        ).count(),
        "aguardando": Agendamento.objects.filter(
            profissional=profissional,
            status=Agendamento.Status.SOLICITADO,
            inicio__gte=agora,
        ).count(),
        "total_clientes": Cliente.objects.filter(profissional=profissional).count(),
        "indicadores": indicadores,
        "link_publico": request.build_absolute_uri(f"/agendar/{profissional.slug}/"),
    }
    return render(request, "painel/painel.html", contexto)