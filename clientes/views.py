"""
As telas de quem contrata servico.

O cliente com conta tem duas coisas que o cliente sem conta nao tem:
uma vitrine com todos os prestadores, em vez de precisar do link de cada
um, e a lista dos proprios agendamentos reunida num lugar so.

O agendamento em si continua sendo o mesmo fluxo publico de sempre - eu
nao dupliquei nada. A conta so serve pra descobrir o profissional e pra
chegar no formulario com os campos ja preenchidos.
"""

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Min, Q
from django.shortcuts import redirect, render
from django.utils import timezone

from agenda.models import Agendamento, Servico
from contas.models import AreaAtuacao, Profissional


def obter_perfil_cliente(request):
    """Devolve o perfil de cliente do usuario logado, ou None."""
    return getattr(request.user, "perfil_cliente", None)


@login_required
def explorar(request):
    """A vitrine: todos os prestadores, com busca por nome e por area.

    E a diferenca de ter conta. Sem ela, o cliente so chega num
    profissional se alguem mandar o link dele; com ela, ele procura.
    """
    perfil = obter_perfil_cliente(request)
    if perfil is None:
        return redirect("painel")

    busca = request.GET.get("busca", "").strip()
    area_escolhida = request.GET.get("area", "").strip()

    # So aparece quem esta ativo E tem pelo menos um servico ativo.
    # Profissional sem servico nenhum levaria o cliente a uma pagina
    # vazia, o que e pior do que nao aparecer.
    consulta = (
        Profissional.objects.filter(ativo=True, servicos__ativo=True)
        .select_related("area")
        .annotate(
            quantos_servicos=Count("servicos", filter=Q(servicos__ativo=True)),
            menor_preco=Min("servicos__preco", filter=Q(servicos__ativo=True)),
        )
        .distinct()
    )

    if busca:
        consulta = consulta.filter(
            Q(nome_publico__icontains=busca) | Q(area__nome__icontains=busca)
        )
    if area_escolhida:
        consulta = consulta.filter(area__slug=area_escolhida)

    profissionais = list(consulta.order_by("nome_publico")[:60])

    # Penduro os servicos em cada um pra tela poder mostrar uma previa
    # sem fazer uma consulta por profissional.
    servicos = Servico.objects.filter(
        profissional__in=profissionais, ativo=True
    ).order_by("duracao_minutos")
    por_profissional = {}
    for servico in servicos:
        por_profissional.setdefault(servico.profissional_id, []).append(servico)
    for profissional in profissionais:
        profissional.previa_servicos = por_profissional.get(profissional.pk, [])[:3]

    # So ofereco no filtro as areas que tem alguem atendendo de fato.
    #
    # As tres condicoes sao as mesmas da consulta la em cima, e tem que
    # ser: antes eu so exigia profissional ativo, entao uma area cujo
    # profissional nao tinha nenhum servico ativo aparecia na lista e
    # levava o cliente pra uma tela vazia.
    areas = (
        AreaAtuacao.objects.filter(
            ativo=True,
            profissionais__ativo=True,
            profissionais__servicos__ativo=True,
        )
        .distinct()
        .order_by("nome")
    )

    contexto = {
        "perfil": perfil,
        "profissionais": profissionais,
        "busca": busca,
        "area_escolhida": area_escolhida,
        "areas": areas,
    }
    return render(request, "clientes/explorar.html", contexto)


@login_required
def meus_agendamentos(request):
    """Os agendamentos da pessoa, juntando todos os prestadores.

    A busca e pelo telefone da conta. E ele que liga essa conta aos
    cadastros que existem dentro de cada profissional - cada um tem o
    seu, mas todos com o mesmo numero.
    """
    perfil = obter_perfil_cliente(request)
    if perfil is None:
        return redirect("painel")

    consulta = Agendamento.objects.filter(
        cliente__telefone=perfil.telefone
    ).select_related("profissional", "servico", "local", "cliente")

    agora = timezone.now()

    contexto = {
        "perfil": perfil,
        "proximos": consulta.filter(
            inicio__gte=agora, status__in=Agendamento.STATUS_OCUPAM_AGENDA
        ).order_by("inicio"),
        "anteriores": consulta.filter(
            Q(inicio__lt=agora) | ~Q(status__in=Agendamento.STATUS_OCUPAM_AGENDA)
        ).order_by("-inicio")[:30],
    }
    return render(request, "clientes/meus_agendamentos.html", contexto)