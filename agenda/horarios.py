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
grade no mesmo instante e escolher o mesmo horário. Quem impede a gravação
duplicada é a restrição de exclusão definida em ``Agendamento.Meta``, no
banco de dados.
"""

from datetime import date as tipo_data
from datetime import datetime, time, timedelta

from django.utils import timezone

from .models import Agendamento, Bloqueio, Disponibilidade


def _tornar_aware(data, hora):
    """Combina data e hora locais em um datetime com fuso horário."""
    return timezone.make_aware(
        datetime.combine(data, hora), timezone.get_current_timezone()
    )


def _ha_sobreposicao(inicio_a, fim_a, intervalos):
    """Indica se [inicio_a, fim_a) cruza algum intervalo da lista.

    Dois intervalos se sobrepõem quando um começa antes de o outro terminar
    e termina depois de o outro começar. A comparação é semiaberta: um
    atendimento que termina às 15h00 não conflita com outro que começa
    exatamente às 15h00.
    """
    for inicio_b, fim_b in intervalos:
        if inicio_a < fim_b and fim_a > inicio_b:
            return True
    return False


def _intervalos_ocupados(profissional, inicio_dia, fim_dia, ignorar_agendamento=None):
    """Reúne agendamentos e bloqueios que tocam o período informado."""
    agendamentos = Agendamento.objects.filter(
        profissional=profissional,
        status__in=Agendamento.STATUS_OCUPAM_AGENDA,
        inicio__lt=fim_dia,
        fim__gt=inicio_dia,
    )
    if ignorar_agendamento is not None:
        agendamentos = agendamentos.exclude(pk=ignorar_agendamento)

    bloqueios = Bloqueio.objects.filter(
        profissional=profissional,
        inicio__lt=fim_dia,
        fim__gt=inicio_dia,
    )

    ocupados = list(agendamentos.values_list("inicio", "fim"))
    ocupados += list(bloqueios.values_list("inicio", "fim"))
    return ocupados


def horarios_disponiveis(profissional, servico, data, local=None, agora=None):
    """Lista os horários de início ofertáveis para um serviço em uma data.

    Args:
        profissional: instância de ``Profissional``.
        servico: instância de ``Servico``; define a duração ocupada.
        data: ``datetime.date`` consultada.
        local: filtra as janelas de um local específico. ``None`` considera
            todos os locais do profissional.
        agora: permite injetar o instante atual nos testes. ``None`` usa
            o relógio do sistema.

    Returns:
        Lista de ``datetime`` com fuso horário, em ordem crescente.
    """
    if agora is None:
        agora = timezone.now()

    if not profissional.ativo or not servico.ativo:
        return []

    # Limites de antecedência configurados pelo profissional.
    limite_minimo = agora + timedelta(hours=profissional.antecedencia_minima_horas)
    limite_maximo = agora + timedelta(days=profissional.antecedencia_maxima_dias)

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

    inicio_dia = _tornar_aware(data, time.min)
    fim_dia = inicio_dia + timedelta(days=1)
    ocupados = _intervalos_ocupados(profissional, inicio_dia, fim_dia)

    duracao = servico.duracao_total
    passo = timedelta(minutes=profissional.granularidade_minutos)

    disponiveis = []
    for janela in janelas:
        abertura = _tornar_aware(data, janela.hora_inicio)
        fechamento = _tornar_aware(data, janela.hora_fim)

        candidato = abertura
        while candidato + duracao <= fechamento:
            termino = candidato + duracao

            dentro_do_prazo = limite_minimo <= candidato <= limite_maximo
            livre = not _ha_sobreposicao(candidato, termino, ocupados)

            if dentro_do_prazo and livre:
                disponiveis.append(candidato)

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


def proximos_dias_com_horarios(profissional, servico, a_partir_de=None, dias=30, local=None):
    """Percorre os próximos dias e devolve apenas os que têm horário livre.

    Alimenta o calendário da página pública de agendamento: em vez de
    mostrar um mês inteiro e deixar o cliente descobrir por tentativa,
    apresenta só os dias que de fato têm vaga.

    Returns:
        Lista de tuplas ``(date, [datetime, ...])``, em ordem cronológica.
    """
    if a_partir_de is None:
        a_partir_de = timezone.localdate()
    if isinstance(a_partir_de, datetime):
        a_partir_de = a_partir_de.date()

    resultado = []
    for deslocamento in range(dias):
        dia = a_partir_de + timedelta(days=deslocamento)
        horarios = horarios_disponiveis(profissional, servico, dia, local=local)
        if horarios:
            resultado.append((dia, horarios))
    return resultado


def primeiro_horario_livre(profissional, servico, a_partir_de=None, dias=60, local=None):
    """Devolve o próximo horário livre, ou ``None`` se não houver.

    Atalho para a chamada de ação "próximo horário disponível".
    """
    dias_livres = proximos_dias_com_horarios(
        profissional, servico, a_partir_de=a_partir_de, dias=dias, local=local
    )
    if not dias_livres:
        return None
    return dias_livres[0][1][0]


def taxa_de_falta(profissional, inicio=None, fim=None):
    """Calcula o percentual de não comparecimento no período.

    Indicador central para a avaliação do sistema: compara faltas com o
    total de atendimentos que chegaram ao seu desfecho (concluídos ou
    faltas), ignorando cancelamentos, que são um fenômeno distinto.

    Returns:
        ``dict`` com ``concluidos``, ``faltas``, ``total`` e ``percentual``.
        O percentual é ``None`` quando ainda não há histórico.
    """
    consulta = Agendamento.objects.filter(
        profissional=profissional,
        status__in=[Agendamento.Status.CONCLUIDO, Agendamento.Status.FALTOU],
    )
    if inicio is not None:
        consulta = consulta.filter(inicio__gte=inicio)
    if fim is not None:
        consulta = consulta.filter(inicio__lt=fim)

    faltas = consulta.filter(status=Agendamento.Status.FALTOU).count()
    concluidos = consulta.filter(status=Agendamento.Status.CONCLUIDO).count()
    total = faltas + concluidos

    return {
        "concluidos": concluidos,
        "faltas": faltas,
        "total": total,
        "percentual": round(faltas * 100 / total, 1) if total else None,
    }