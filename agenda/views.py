"""
As telas do painel do profissional.

Tudo aqui e a parte de dentro do sistema, so pra quem esta logado. E o
outro lado do views_publico.py: la o cliente marca, aqui o profissional
acompanha, confirma, cancela e cadastra o que ele oferece.

Todas as consultas filtram por profissional. Isso nao e paranoia: sem
esse filtro, era so trocar o numero na URL pra mexer na agenda de outra
pessoa.
"""

from datetime import datetime, time
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import Avg, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from clientes.models import Cliente, Feedback
from contas.views import obter_profissional

from .models import (
    Agendamento,
    Bloqueio,
    DiaSemana,
    Disponibilidade,
    Pergunta,
    Servico,
)


def _inteiro(valor, padrao=0):
    """Converte o que veio do formulario em numero, sem quebrar.

    O campo da tela e type=number, mas isso so vale no navegador: quem
    montar o POST na mao manda o que quiser. Antes um "abc" na duracao
    estourava erro 500 no int().
    """
    try:
        return int(valor)
    except (TypeError, ValueError):
        return padrao


@login_required
def agendamentos(request):
    """A lista de agendamentos, com filtro por situacao e por periodo.

    E a tela que o profissional abre primeiro no dia a dia, entao ela
    ja vem mostrando o que esta por vir, e nao o historico.
    """
    profissional = obter_profissional(request)

    # Da pra ter conta criada sem ter terminado o perfil de
    # profissional. Nesse caso eu mando pra tela que pede pra
    # completar o cadastro, em vez de estourar erro.
    if profissional is None:
        return render(request, "painel/sem_perfil.html")

    situacao = request.GET.get("situacao", "")
    periodo = request.GET.get("periodo", "futuros")

    # O select_related traz cliente, servico e local na mesma consulta.
    # Sem ele o template faria uma consulta por linha da tabela, e com
    # 50 agendamentos na tela isso vira 150 idas ao banco.
    consulta = Agendamento.objects.filter(
        profissional=profissional
    ).select_related("cliente", "servico", "local")

    agora = timezone.localtime()
    if periodo == "futuros":
        # Do mais proximo pro mais distante: o que interessa e o
        # proximo atendimento.
        consulta = consulta.filter(inicio__gte=agora).order_by("inicio")
    elif periodo == "passados":
        # No historico a ordem se inverte - o mais recente primeiro.
        consulta = consulta.filter(inicio__lt=agora).order_by("-inicio")
    else:
        consulta = consulta.order_by("-inicio")

    # Situacao vazia significa "todas", entao so filtro se veio alguma.
    if situacao:
        consulta = consulta.filter(status=situacao)

    contexto = {
        "profissional": profissional,
        # Corto em 100 pra tela nao travar depois de uns meses de uso.
        # A fatia so entra na consulta agora, no fim - o Django monta
        # um LIMIT em vez de trazer tudo e jogar fora.
        "agendamentos": consulta[:100],
        "situacao": situacao,
        "periodo": periodo,
        "situacoes": Agendamento.Status.choices,
    }
    return render(request, "agenda/agendamentos.html", contexto)


@login_required
def mudar_situacao(request, pk, nova):
    """Muda a situacao de um agendamento direto pela lista.

    E por aqui que o profissional confirma, marca como concluido ou
    registra que o cliente faltou - sem precisar abrir outra tela.
    """
    profissional = obter_profissional(request)

    # O profissional entra no get_object_or_404 junto com o pk. Se o
    # agendamento for de outra pessoa, da 404 e acabou - nao chega nem
    # perto do save.
    agendamento = get_object_or_404(
        Agendamento, pk=pk, profissional=profissional
    )

    # A situacao vem da URL, entao pode vir qualquer coisa escrita a
    # mao. Confiro contra as opcoes que existem de verdade antes de
    # gravar.
    validas = dict(Agendamento.Status.choices)
    if nova not in validas:
        messages.error(request, "Situação inválida.")
        return redirect("agendamentos")

    agendamento.status = nova

    # Guardo a hora de quando confirmou e de quando cancelou. Sao esses
    # dois campos que me deixam calcular a taxa de falta e o tempo de
    # resposta depois.
    if nova == Agendamento.Status.CONFIRMADO:
        agendamento.confirmado_em = timezone.now()
    elif nova == Agendamento.Status.CANCELADO:
        agendamento.cancelado_em = timezone.now()

    try:
        agendamento.save()
    except IntegrityError:
        # Reabrir um agendamento cancelado bate na restricao de
        # sobreposicao quando outra pessoa ja pegou aquele horario. Antes
        # isso virava erro 500; agora e so um aviso.
        messages.error(
            request,
            "Não consegui reabrir: esse horário já foi ocupado por outro "
            "agendamento.",
        )
        return redirect("agendamentos")

    messages.success(
        request,
        f"Agendamento de {agendamento.cliente.nome} marcado como "
        f"{validas[nova].lower()}.",
    )

    # Volto pra pagina de onde ele clicou, pra nao perder o filtro que
    # estava aplicado na lista.
    #
    # Mas so se for endereco daqui. O HTTP_REFERER quem manda e o
    # navegador, entao antes bastava alguem publicar um link pro meu
    # sistema num site qualquer: a pessoa clicava, confirmava o
    # agendamento e era jogada de volta no site do sujeito, com cara
    # de que o redirecionamento partiu de mim.
    destino = request.META.get("HTTP_REFERER", "")
    if url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
        return redirect(destino)
    return redirect("agendamentos")


@login_required
def servicos(request):
    """O cadastro dos servicos que o profissional oferece.

    E aqui que o sistema deixa de ser so pra estetica. Cada um cadastra
    o que faz com a duracao que faz sentido pra ele - o sistema nao
    tem nenhuma lista de servicos pronta por dentro.
    """
    profissional = obter_profissional(request)
    if profissional is None:
        return render(request, "painel/sem_perfil.html")

    if request.method == "POST":
        nome = request.POST.get("nome", "").strip()[:120]
        duracao = _inteiro(request.POST.get("duracao_minutos"))
        intervalo = _inteiro(request.POST.get("intervalo_apos_minutos"))
        modalidade = request.POST.get("modalidade", Servico.Modalidade.PRESENCIAL)
        exige_perguntas = request.POST.get("exige_perguntas") == "on"

        # Preco em branco vira None de proposito: tem servico que so da
        # pra orcar depois de ver o caso. Valor invalido cai no mesmo
        # lugar, em vez de derrubar a pagina.
        preco = request.POST.get("preco", "").strip().replace(",", ".")
        try:
            preco = Decimal(preco) if preco else None
        except InvalidOperation:
            preco = None

        if not nome:
            messages.error(request, "Informe o nome do serviço.")
        elif duracao < 5:
            messages.error(request, "A duração mínima é de 5 minutos.")
        elif Servico.objects.filter(profissional=profissional, nome=nome).exists():
            # Quem garante de verdade que nao repete e a restricao do
            # banco. Confiro aqui so pra poder dar uma mensagem
            # decente em vez de deixar estourar erro na tela.
            messages.error(request, f'Você já tem um serviço chamado "{nome}".')
        else:
            Servico.objects.create(
                profissional=profissional,
                nome=nome,
                duracao_minutos=duracao,
                intervalo_apos_minutos=intervalo,
                preco=preco,
                modalidade=modalidade,
                exige_perguntas=exige_perguntas,
            )
            messages.success(request, f'Serviço "{nome}" cadastrado.')

        # Redireciono mesmo quando deu erro. Assim, se a pessoa apertar
        # F5 depois, o navegador nao reenvia o formulario e cadastra o
        # servico duas vezes.
        return redirect("servicos")

    contexto = {
        "profissional": profissional,
        # Trago tambem os desativados: eles aparecem na lista com a
        # opcao de ligar de novo.
        "servicos": Servico.objects.filter(profissional=profissional),
        "modalidades": Servico.Modalidade.choices,
    }
    return render(request, "agenda/servicos.html", contexto)


@login_required
def alternar_servico(request, pk):
    """Liga e desliga um servico.

    De proposito nao tem botao de apagar. Se o servico sumisse, os
    agendamentos antigos ficariam sem referencia e o historico se
    perderia - por isso o modelo usa PROTECT nessa chave. Desativar
    resolve: some da tela publica e continua existindo por tras.
    """
    profissional = obter_profissional(request)
    servico = get_object_or_404(Servico, pk=pk, profissional=profissional)
    servico.ativo = not servico.ativo

    # update_fields faz o UPDATE mexer so nessa coluna, sem reescrever
    # o resto da linha.
    servico.save(update_fields=["ativo"])

    estado = "ativado" if servico.ativo else "desativado"
    messages.success(request, f'Serviço "{servico.nome}" {estado}.')
    return redirect("servicos")


@login_required
def disponibilidade(request):
    """A grade da semana: os horarios em que o profissional atende.

    Aqui ele monta a rotina ("atendo terca das 8h as 12h"). E dessa
    grade que o horarios.py parte pra saber o que oferecer pro cliente.
    Folga e ferias sao outra coisa, ficam no Bloqueio.
    """
    profissional = obter_profissional(request)
    if profissional is None:
        return render(request, "painel/sem_perfil.html")

    if request.method == "POST":
        dia = _inteiro(request.POST.get("dia_semana"), 0)
        inicio = request.POST.get("hora_inicio")
        fim = request.POST.get("hora_fim")

        # Local em branco quer dizer atendimento online.
        #
        # Busco dentro dos locais DELE, e nao pelo id solto. O campo e
        # um select com os locais dele, mas quem monta o POST na mao
        # pode mandar o id do local de outro profissional - e a janela
        # ficava gravada apontando pro endereco de outra pessoa.
        local = None
        if request.POST.get("local"):
            local = profissional.locais.filter(
                pk=_inteiro(request.POST.get("local"))
            ).first()

        # Confiro o dia contra os sete que existem. Antes, um valor
        # estranho no POST caía como segunda-feira sem ninguem ver.
        if dia not in DiaSemana.values or not (inicio and fim):
            messages.error(request, "Preencha dia, início e fim.")
        elif fim <= inicio:
            # Da pra comparar como texto porque o input type=time
            # devolve sempre no formato HH:MM, com zero na frente.
            # "09:00" < "17:00" funciona na comparacao de string.
            messages.error(request, "O fim precisa ser depois do início.")
        else:
            Disponibilidade.objects.create(
                profissional=profissional,
                dia_semana=dia,
                hora_inicio=inicio,
                hora_fim=fim,
                local=local,
            )
            messages.success(request, "Janela de atendimento adicionada.")
        return redirect("disponibilidade")

    janelas = Disponibilidade.objects.filter(
        profissional=profissional
    ).select_related("local")

    # Monto a grade agrupando por dia da semana. Faco isso em Python
    # de proposito: sao poucas janelas e assim eu consigo mostrar os
    # sete dias na tela, inclusive os vazios. Se eu agrupasse no banco,
    # os dias sem atendimento simplesmente nao apareceriam.
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
    """Tira uma janela da grade da semana.

    Aqui pode apagar mesmo, sem medo: a janela e so a regra do que vai
    ser oferecido daqui pra frente. Os agendamentos que ja estao
    marcados nao dependem dela e continuam de pe.
    """
    profissional = obter_profissional(request)
    janela = get_object_or_404(Disponibilidade, pk=pk, profissional=profissional)
    janela.delete()
    messages.success(request, "Janela removida.")
    return redirect("disponibilidade")


@login_required
def clientes(request):
    """A carteira de clientes do profissional.

    Os clientes ficam separados por profissional: o mesmo telefone pode
    estar cadastrado em dois profissionais diferentes e sao dois
    cadastros, um pra cada. Quem atende nao tem por que enxergar a
    clientela de outra pessoa.
    """
    profissional = obter_profissional(request)
    if profissional is None:
        return render(request, "painel/sem_perfil.html")

    busca = request.GET.get("busca", "").strip()
    consulta = Cliente.objects.filter(profissional=profissional)
    if busca:
        # icontains ignora maiuscula e minuscula, entao "ana" acha
        # "Ana" e "Mariana".
        consulta = consulta.filter(nome__icontains=busca)

    contexto = {
        "profissional": profissional,
        # O prefetch traz os consentimentos de todo mundo numa consulta
        # so. A tela mostra quem autorizou receber mensagem, e sem isso
        # seria uma consulta por cliente da lista.
        "clientes": consulta.prefetch_related("consentimentos")[:200],
        "busca": busca,
    }
    return render(request, "agenda/clientes.html", contexto)


@login_required
def avaliacoes(request):
    """As notas que os clientes deram (RF13).

    Essa tela e o outro lado do formulario publico. Aqui o profissional
    ve a media, como as notas se distribuem e o que as pessoas
    escreveram.

    A taxa de resposta esta no resumo de proposito: media alta com tres
    respostas nao quer dizer grande coisa, e quem olha precisa enxergar
    isso.
    """
    profissional = obter_profissional(request)
    if profissional is None:
        return render(request, "painel/sem_perfil.html")

    consulta = (
        Feedback.objects.filter(agendamento__profissional=profissional)
        .select_related(
            "agendamento", "agendamento__cliente", "agendamento__servico"
        )
    )

    resumo = consulta.aggregate(media=Avg("nota"), total=Count("id"))
    total = resumo["total"] or 0

    # Distribuicao das notas, de 5 para 1. Faco uma consulta agrupada so
    # e completo com zero as notas que ninguem deu - senao o grafico
    # ficaria com buracos.
    contagem = {
        linha["nota"]: linha["quantos"]
        for linha in consulta.values("nota").annotate(quantos=Count("nota"))
    }
    distribuicao = [
        {
            "nota": n,
            "quantos": contagem.get(n, 0),
            "percentual": round(contagem.get(n, 0) * 100 / total) if total else 0,
        }
        for n in range(5, 0, -1)
    ]

    # Quantos atendimentos concluidos existem no total. E com isso que
    # eu calculo quantos por cento responderam.
    concluidos = Agendamento.objects.filter(
        profissional=profissional, status=Agendamento.Status.CONCLUIDO
    ).count()

    contexto = {
        "profissional": profissional,
        "avaliacoes": consulta[:100],
        "media": resumo["media"],
        "total": total,
        "concluidos": concluidos,
        # O min evita passar de 100%. Acontece quando o profissional
        # corrige a situacao de um atendimento que ja tinha nota: a nota
        # fica, o concluido sai da conta, e o resultado passava de 100.
        "taxa_resposta": min(round(total * 100 / concluidos), 100) if concluidos else 0,
        "distribuicao": distribuicao,
    }
    return render(request, "agenda/avaliacoes.html", contexto)


@login_required
def bloqueios(request):
    """Folgas, ferias e compromissos que tiram o horario da agenda.

    A disponibilidade e a rotina que se repete toda semana; o bloqueio e
    a excecao daquela data. Separei os dois porque sao coisas
    diferentes: "nao atendo domingo" e regra, "vou viajar na semana que
    vem" e evento.

    Ate agora isso so existia no admin do Django, o que na pratica
    significa que nao existia pro profissional.
    """
    profissional = obter_profissional(request)
    if profissional is None:
        return render(request, "painel/sem_perfil.html")

    fuso = timezone.get_current_timezone()

    if request.method == "POST":
        data_inicio = request.POST.get("data_inicio", "")
        data_fim = request.POST.get("data_fim", "") or data_inicio
        hora_inicio = request.POST.get("hora_inicio", "") or "00:00"
        hora_fim = request.POST.get("hora_fim", "") or "23:59"
        dia_todo = request.POST.get("dia_todo") == "on"
        motivo = request.POST.get("motivo", "").strip()[:120]

        # Dia todo e so um atalho: preenche as horas por mim, do comeco
        # ao fim do dia.
        if dia_todo:
            hora_inicio, hora_fim = "00:00", "23:59"

        erros = []
        inicio = fim = None

        if not data_inicio:
            erros.append("Informe a data de início.")
        else:
            try:
                inicio = timezone.make_aware(
                    datetime.combine(
                        datetime.strptime(data_inicio, "%Y-%m-%d").date(),
                        time.fromisoformat(hora_inicio),
                    ),
                    fuso,
                )
                fim = timezone.make_aware(
                    datetime.combine(
                        datetime.strptime(data_fim, "%Y-%m-%d").date(),
                        time.fromisoformat(hora_fim),
                    ),
                    fuso,
                )
            except ValueError:
                erros.append("Data ou hora em formato inválido.")

        # O banco tambem barra isso, mas prefiro avisar antes de tentar
        # gravar - a mensagem fica bem melhor.
        if inicio and fim and fim <= inicio:
            erros.append("O fim do bloqueio precisa ser depois do início.")

        if erros:
            for erro in erros:
                messages.error(request, erro)
            return redirect("bloqueios")

        Bloqueio.objects.create(
            profissional=profissional, inicio=inicio, fim=fim, motivo=motivo
        )

        # Bloquear NAO desmarca quem ja estava agendado. Se eu apagasse
        # os agendamentos aqui, o cliente ficaria sem saber. Entao eu
        # conto quantos caem dentro do periodo e aviso - a decisao de
        # cancelar e remarcar continua sendo do profissional.
        conflitos = Agendamento.objects.filter(
            profissional=profissional,
            status__in=Agendamento.STATUS_OCUPAM_AGENDA,
            inicio__lt=fim,
            fim__gt=inicio,
        ).count()

        if conflitos:
            messages.warning(
                request,
                f"Bloqueio criado, mas há {conflitos} agendamento(s) já "
                "marcado(s) nesse período. Eles continuam valendo — "
                "cancele ou remarque pela tela de agendamentos.",
            )
        else:
            messages.success(request, "Bloqueio cadastrado.")

        return redirect("bloqueios")

    agora = timezone.now()
    consulta = Bloqueio.objects.filter(profissional=profissional)

    contexto = {
        "profissional": profissional,
        # Separo em duas listas porque o que interessa no dia a dia e o
        # que ainda vai acontecer. O passado fica embaixo, so pra
        # consulta.
        "proximos": consulta.filter(fim__gte=agora).order_by("inicio"),
        "anteriores": consulta.filter(fim__lt=agora).order_by("-inicio")[:20],
        "hoje": timezone.localdate(),
    }
    return render(request, "agenda/bloqueios.html", contexto)


@login_required
def remover_bloqueio(request, pk):
    """Tira um bloqueio e devolve o periodo para a agenda."""
    profissional = obter_profissional(request)
    bloqueio = get_object_or_404(Bloqueio, pk=pk, profissional=profissional)
    bloqueio.delete()
    messages.success(request, "Bloqueio removido. O período voltou a ficar livre.")
    return redirect("bloqueios")


@login_required
def perguntas(request, pk):
    """Onde o profissional monta o questionario de um servico.

    Cada servico tem o seu. O tatuador pergunta sobre alergia a tinta no
    servico de tatuagem e nao pergunta nada no de orcamento - sao
    listas separadas.
    """
    profissional = obter_profissional(request)
    if profissional is None:
        return render(request, "painel/sem_perfil.html")

    servico = get_object_or_404(Servico, pk=pk, profissional=profissional)

    if request.method == "POST":
        acao = request.POST.get("acao")

        if acao == "alternar":
            # Liga e desliga o questionario inteiro sem apagar as
            # perguntas ja escritas.
            servico.exige_perguntas = not servico.exige_perguntas
            servico.save(update_fields=["exige_perguntas"])
            estado = "ligado" if servico.exige_perguntas else "desligado"
            messages.success(request, f"Questionário {estado}.")
            return redirect("perguntas", pk=servico.pk)

        if acao == "remover":
            Pergunta.objects.filter(
                pk=_inteiro(request.POST.get("pergunta"), 0), servico=servico
            ).delete()
            messages.success(request, "Pergunta removida.")
            return redirect("perguntas", pk=servico.pk)

        texto = request.POST.get("texto", "").strip()[:200]
        tipo = request.POST.get("tipo", Pergunta.Tipo.SIM_NAO)
        alerta_se = request.POST.get("alerta_se", "")
        obrigatoria = request.POST.get("obrigatoria") == "on"

        if len(texto) < 5:
            messages.error(request, "Escreva a pergunta com pelo menos 5 letras.")
        else:
            # Alerta so faz sentido em pergunta de sim ou nao. Em
            # resposta escrita nao tem como comparar.
            if tipo != Pergunta.Tipo.SIM_NAO:
                alerta_se = ""

            # A nova entra no fim da fila.
            ultima = servico.perguntas.order_by("-ordem").first()
            Pergunta.objects.create(
                servico=servico,
                texto=texto,
                tipo=tipo,
                alerta_se=alerta_se,
                obrigatoria=obrigatoria,
                ordem=(ultima.ordem + 1) if ultima else 0,
            )

            # Cadastrou a primeira pergunta? Ligo o questionario
            # sozinho - ninguem escreve pergunta pra deixar desligada.
            if not servico.exige_perguntas:
                servico.exige_perguntas = True
                servico.save(update_fields=["exige_perguntas"])
                messages.info(
                    request,
                    "Questionário ligado automaticamente para este serviço.",
                )
            messages.success(request, "Pergunta adicionada.")

        return redirect("perguntas", pk=servico.pk)

    contexto = {
        "profissional": profissional,
        "servico": servico,
        "perguntas": servico.perguntas.all(),
        "tipos": Pergunta.Tipo.choices,
        "alertas": Pergunta.Alerta.choices,
    }
    return render(request, "agenda/perguntas.html", contexto)


@login_required
def respostas(request, pk):
    """As respostas de um agendamento, com os botoes de aceitar ou recusar.

    E o fecho do ciclo: o cliente respondeu, e agora o profissional le e
    decide. Recusar aqui e cancelar o agendamento com um motivo - o
    cliente recebe o aviso como em qualquer outro cancelamento.
    """
    profissional = obter_profissional(request)
    agendamento = get_object_or_404(
        Agendamento.objects.select_related("cliente", "servico", "local"),
        pk=pk,
        profissional=profissional,
    )

    if request.method == "POST":
        acao = request.POST.get("acao")

        if acao == "aceitar":
            agendamento.status = Agendamento.Status.CONFIRMADO
            agendamento.confirmado_em = timezone.now()
            agendamento.save(update_fields=["status", "confirmado_em"])
            messages.success(
                request, f"Agendamento de {agendamento.cliente.nome} confirmado."
            )

        elif acao == "recusar":
            motivo = request.POST.get("motivo", "").strip()
            agendamento.status = Agendamento.Status.CANCELADO
            agendamento.cancelado_em = timezone.now()
            agendamento.motivo_cancelamento = (
                motivo[:160] or "Não atende aos pré-requisitos do serviço"
            )
            agendamento.save(
                update_fields=["status", "cancelado_em", "motivo_cancelamento"]
            )
            messages.warning(
                request, f"Agendamento de {agendamento.cliente.nome} recusado."
            )

        return redirect("agendamentos")

    contexto = {
        "profissional": profissional,
        "agendamento": agendamento,
        "respostas": agendamento.respostas.all(),
    }
    return render(request, "agenda/respostas.html", contexto)