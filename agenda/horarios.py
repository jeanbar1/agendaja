"""
Motor de horários livres.

Dado um profissional, um serviço e uma data, devolve os horários que podem
ser efetivamente ofertados ao cliente. Um horário só é ofertado quando
satisfaz, ao mesmo tempo:

1. Cai dentro de uma janela de disponibilidade recorrente do profissional
   para aquele dia da semana;
2. Comporta a duração total do serviço (atendimento + intervalo posterior)
   sem ultrapassar o fim da janela;
3. Não se sobrepõe a nenhum agendamento que ocupe a agenda;
4. Não se sobrepõe a nenhum bloqueio (folga, férias, compromisso);
5. Respeita a antecedência mínima e máxima configuradas pelo profissional.

Este módulo é a camada de consulta. Ele evita oferecer horário indisponível,
mas não é a garantia de integridade: dois clientes podem consultar a mesma
grade no mesmo instante e escolher o mesmo horário. Quem impede de fato a
sobreposição é a restrição de exclusão no banco.
"""

from datetime import datetime, time, timedelta

from django.db.models import Count, Q
from django.utils import timezone

from .models import Agendamento, Bloqueio, Disponibilidade


def _tornar_aware(data, hora):
    """Combina data e hora locais em um datetime com fuso horário."""
    return timezone.make_aware(
        datetime.combine(data, hora), timezone.get_current_timezone()
    )


def _data_inicial(a_partir_de, agora):
    """Normaliza o ponto de partida das buscas por dia.

    Aceita date, datetime ou None para começar de hoje, porque as três
    formas aparecem em quem chama.
    """
    if a_partir_de is None:
        return timezone.localtime(agora).date()
    if isinstance(a_partir_de, datetime):
        return a_partir_de.date()
    return a_partir_de


def _ha_sobreposicao(inicio_a, fim_a, intervalos):
    """Indica se [inicio_a, fim_a) cruza algum intervalo da lista.

    Dois intervalos se sobrepõem quando um começa antes de o outro terminar
    e termina depois de o outro começar. A comparação é semiaberta: um
    atendimento que termina às 15h00 não conflita com outro que começa
    exatamente às 15h00.
    """
    return any(
        inicio_a < fim_b and fim_a > inicio_b for inicio_b, fim_b in intervalos
    )


def _intervalos_ocupados(profissional, inicio, fim, ignorar_agendamento=None):
    """Reúne agendamentos e bloqueios que tocam o período informado."""
    agendamentos = Agendamento.objects.filter(
        profissional=profissional,
        status__in=Agendamento.STATUS_OCUPAM_AGENDA,
        inicio__lt=fim,
        fim__gt=inicio,
    )
    if ignorar_agendamento is not None:
        agendamentos = agendamentos.exclude(pk=ignorar_agendamento)

    bloqueios = Bloqueio.objects.filter(
        profissional=profissional,
        inicio__lt=fim,
        fim__gt=inicio,
    )

    return list(agendamentos.values_list("inicio", "fim")) + list(
        bloqueios.values_list("inicio", "fim")
    )


def horarios_disponiveis(profissional, servico, data, local=None, agora=None):
    """Lista os horários de início ofertáveis para um serviço em uma data."""
    if agora is None:
        agora = timezone.now()

    if not profissional.ativo or not servico.ativo:
        return []

    janelas = Disponibilidade.objects.filter(
        profissional=profissional,
        dia_semana=data.isoweekday(),
        ativo=True,
    )
    if local is not None:
        janelas = janelas.filter(local=local)

    janelas = list(janelas.order_by("hora_inicio"))
    if not janelas:
        return []

    limite_minimo = agora + timedelta(hours=profissional.antecedencia_minima_horas)
    limite_maximo = agora + timedelta(days=profissional.antecedencia_maxima_dias)

    inicio_dia = _tornar_aware(data, time.min)
    ocupados = _intervalos_ocupados(
        profissional, inicio_dia, inicio_dia + timedelta(days=1)
    )

    duracao = servico.duracao_total
    passo = timedelta(minutes=profissional.granularidade_minutos)

    # Conjunto, e não lista: quem atende em mais de um lugar no mesmo dia
    # tem janelas que se cruzam (salão até as 12h, domicílio a partir das
    # 10h), e o mesmo horário caía duas vezes na grade do cliente.
    disponiveis = set()

    for janela in janelas:
        fechamento = _tornar_aware(data, janela.hora_fim)
        candidato = _tornar_aware(data, janela.hora_inicio)

        while candidato + duracao <= fechamento:
            dentro_do_prazo = limite_minimo <= candidato <= limite_maximo
            if dentro_do_prazo and not _ha_sobreposicao(
                candidato, candidato + duracao, ocupados
            ):
                disponiveis.add(candidato)
            candidato += passo

    return sorted(disponiveis)


def horario_esta_livre(profissional, inicio, fim, ignorar_agendamento=None):
    """Verifica se um período específico está livre na agenda.

    Usada antes de gravar, para devolver uma mensagem amigável em vez de
    deixar o erro de integridade do banco chegar ao usuário. A garantia
    real continua sendo a restrição de exclusão.
    """
    ocupados = _intervalos_ocupados(
        profissional, inicio, fim, ignorar_agendamento=ignorar_agendamento
    )
    return not _ha_sobreposicao(inicio, fim, ocupados)


def proximos_dias_com_horarios(
    profissional, servico, a_partir_de=None, dias=30, local=None, agora=None
):
    """Percorre os próximos dias e devolve apenas os que têm horário livre.

    Alimenta o calendário da página pública de agendamento: em vez de
    mostrar um mês inteiro e deixar o cliente descobrir por tentativa,
    apresenta só os dias que de fato têm vaga.

    O ``agora`` existe para os testes poderem fixar o relógio. Sem ele,
    um teste escrito hoje com datas fixas quebra sozinho daqui a um mês
    — já aconteceu comigo.
    """
    if agora is None:
        agora = timezone.now()
    inicio = _data_inicial(a_partir_de, agora)

    resultado = []
    for deslocamento in range(dias):
        dia = inicio + timedelta(days=deslocamento)
        horarios = horarios_disponiveis(
            profissional, servico, dia, local=local, agora=agora
        )
        if horarios:
            resultado.append((dia, horarios))
    return resultado


def primeiro_horario_livre(
    profissional, servico, a_partir_de=None, dias=60, local=None, agora=None
):
    """Devolve o próximo horário livre, ou None se não houver.

    Atalho para a chamada de ação "próximo horário disponível". Para na
    primeira vaga que encontrar: antes eu montava a agenda dos 60 dias
    inteiros para no fim usar só o primeiro horário da lista.
    """
    if agora is None:
        agora = timezone.now()
    inicio = _data_inicial(a_partir_de, agora)

    for deslocamento in range(dias):
        dia = inicio + timedelta(days=deslocamento)
        horarios = horarios_disponiveis(
            profissional, servico, dia, local=local, agora=agora
        )
        if horarios:
            return horarios[0]
    return None


def taxa_de_falta(profissional, inicio=None, fim=None):
    """Calcula o percentual de não comparecimento no período.

    Indicador central para a avaliação do sistema: compara faltas com o
    total de atendimentos que chegaram ao seu desfecho (concluídos ou
    faltas), ignorando cancelamentos, que são um fenômeno distinto.
    """
    consulta = Agendamento.objects.filter(
        profissional=profissional,
        status__in=[Agendamento.Status.CONCLUIDO, Agendamento.Status.FALTOU],
    )
    if inicio is not None:
        consulta = consulta.filter(inicio__gte=inicio)
    if fim is not None:
        consulta = consulta.filter(inicio__lt=fim)

    # As duas contagens saem na mesma consulta, em vez de uma ida ao banco
    # para cada uma.
    totais = consulta.aggregate(
        faltas=Count("pk", filter=Q(status=Agendamento.Status.FALTOU)),
        concluidos=Count("pk", filter=Q(status=Agendamento.Status.CONCLUIDO)),
    )
    faltas = totais["faltas"]
    concluidos = totais["concluidos"]
    total = faltas + concluidos

    return {
        "concluidos": concluidos,
        "faltas": faltas,
        "total": total,
        "percentual": round(faltas * 100 / total, 1) if total else None,
    }