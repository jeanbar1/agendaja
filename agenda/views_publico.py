"""
As telas publicas de agendamento.

Aqui e onde o cliente marca o horario sozinho. Nenhuma dessas telas
pede login: ele digita nome e telefone e pronto. Fiz assim de proposito
porque no questionario o pessoal reclamou de aplicativo que obriga a
criar conta - se tivesse cadastro, boa parte ia desistir no meio e
voltar a mandar mensagem no WhatsApp, que e justamente o problema que eu
quero resolver.

Esse tambem e o caminho que vou mostrar na apresentacao.
"""

from datetime import datetime

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from clientes.models import Cliente, ConsentimentoOptIn, Feedback
from contas.models import Profissional

from .horarios import horarios_disponiveis, proximos_dias_com_horarios
from .models import Agendamento, Resposta, Servico

# O texto do aceite fica numa constante porque ele precisa ser
# exatamente o mesmo em dois lugares: o que aparece na tela pro cliente
# e o que fica gravado no ConsentimentoOptIn. Se eu escrevesse os dois
# na mao, uma hora eu mudava um e esquecia o outro - e ai o registro do
# consentimento nao provaria mais nada.
TEXTO_CONSENTIMENTO = (
    "Autorizo o envio de mensagens sobre este agendamento pelo WhatsApp. "
    "Posso cancelar o envio a qualquer momento respondendo SAIR."
)


def _perguntas_do_servico(servico):
    """As perguntas ativas do servico, ou lista vazia.

    Fica numa funcao porque duas partes da mesma view precisam: a que
    monta o formulario e a que valida o envio. Se o servico nao exige
    questionario, devolvo lista vazia e todo o resto do codigo segue
    sem saber que esse recurso existe.
    """
    if not servico.exige_perguntas:
        return []
    return list(servico.perguntas.filter(ativa=True))


def escolher_servico(request, slug):
    """Passo 1: o cliente escolhe o servico.

    O slug e o link que o profissional divulga (o /joao no fim da URL).
    So passa quem esta com ativo=True: se o profissional sair do
    sistema, o link dele para de funcionar sozinho, sem eu ter que
    apagar nada.
    """
    profissional = get_object_or_404(Profissional, slug=slug, ativo=True)
    servicos = Servico.objects.filter(profissional=profissional, ativo=True)

    return render(
        request,
        "publico/escolher_servico.html",
        {"profissional": profissional, "servicos": servicos},
    )


def escolher_horario(request, slug, servico_id):
    """Passo 2: o cliente escolhe o dia e o horario.

    Essa view nao calcula nada. Quem sabe quais horarios estao livres e
    o horarios.py - aqui eu so pego o resultado pronto e passo pro
    template.
    """
    profissional = get_object_or_404(Profissional, slug=slug, ativo=True)

    # Filtrar pelo profissional aqui nao e enfeite: sem isso, era so
    # trocar o numero na URL pra agendar um servico que e de outra
    # pessoa.
    servico = get_object_or_404(
        Servico, pk=servico_id, profissional=profissional, ativo=True
    )

    # Ate quantos dias pra frente a agenda abre e o proprio profissional
    # quem decide. Tem gente que libera o mes todo e tem gente que so
    # quer mostrar a semana.
    dias = proximos_dias_com_horarios(
        profissional, servico, dias=profissional.antecedencia_maxima_dias
    )

    # Qual dia mostrar: o que veio na URL, quando o cliente clica em
    # outro dia da lista, ou o primeiro dia com vaga quando ele acabou
    # de chegar. Se a data vier escrita errada eu ignoro em silencio -
    # nao faz sentido jogar erro na cara do cliente por causa disso.
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
            # Corto em 30 pra faixa de dias nao virar uma lista enorme
            # no celular, mesmo que o profissional tenha liberado mais.
            "dias": dias[:30],
            "dia_selecionado": selecionado,
            "horarios": horarios,
        },
    )


def confirmar(request, slug, servico_id):
    """Passo 3: os dados do cliente e a gravacao do agendamento.

    Essa view faz duas coisas: no GET ela mostra o formulario e no POST
    ela valida e grava. Juntei as duas pra nao ter que ficar carregando
    o horario escolhido de uma view pra outra por sessao.
    """
    profissional = get_object_or_404(Profissional, slug=slug, ativo=True)
    servico = get_object_or_404(
        Servico, pk=servico_id, profissional=profissional, ativo=True
    )

    # O horario vem da tela anterior. Se a pessoa entrou direto nessa
    # URL, sem passar pela grade, nao tem o que confirmar - mando de
    # volta pro passo 2.
    inicio_texto = request.POST.get("inicio") or request.GET.get("inicio")
    if not inicio_texto:
        messages.error(request, "Escolha um horário para continuar.")
        return redirect("publico_horario", slug=slug, servico_id=servico.pk)

    # O que chega do formulario e texto puro, sem fuso nenhum. Se eu
    # gravasse desse jeito, o Django ia reclamar e o horario podia ficar
    # 3 horas errado no banco.
    #
    # O try nao e paranoia: esse valor vem da URL, entao qualquer pessoa
    # pode digitar o que quiser ali. Sem ele, um "?inicio=abc" derrubava
    # a pagina com erro 500.
    try:
        inicio = datetime.fromisoformat(inicio_texto)
    except ValueError:
        messages.error(request, "Escolha um horário para continuar.")
        return redirect("publico_horario", slug=slug, servico_id=servico.pk)

    if timezone.is_naive(inicio):
        inicio = timezone.make_aware(inicio, timezone.get_current_timezone())

    # Primeira passada: ainda nao veio nada preenchido, entao so mostro
    # o formulario com o resumo do que ele escolheu ate aqui.
    if request.method == "GET" or "nome" not in request.POST:
        # Se quem esta marcando tem conta de cliente, ja preencho nome e
        # telefone. O caminho sem conta continua igual - so encurta pra
        # quem se cadastrou.
        perfil = getattr(request.user, "perfil_cliente", None)

        return render(
            request,
            "publico/confirmar.html",
            {
                "profissional": profissional,
                "servico": servico,
                "inicio": inicio,
                "fim": inicio + servico.duracao_total,
                "nome": perfil.nome if perfil else "",
                "telefone": perfil.telefone if perfil else "",
                "perguntas": _perguntas_do_servico(servico),
                "texto_consentimento": TEXTO_CONSENTIMENTO,
            },
        )

    nome = request.POST.get("nome", "").strip()
    telefone = request.POST.get("telefone", "").strip()
    aceite = request.POST.get("consentimento") == "on"

    # Corto a observacao no tamanho maximo em vez de recusar. E campo
    # livre e opcional: ninguem vai querer refazer o formulario porque
    # escreveu demais.
    observacoes = request.POST.get("observacoes", "").strip()[:500]

    # Junto todos os erros numa lista em vez de parar no primeiro. Assim
    # o cliente corrige tudo de uma vez e nao fica tentando enviar tres
    # vezes seguidas.
    erros = []
    if len(nome) < 3:
        erros.append("Informe seu nome completo.")
    elif len(nome) > 120:
        # O campo do banco tem esse limite. Sem conferir aqui, um nome
        # gigante estourava erro 500 na hora de gravar.
        erros.append("O nome ficou grande demais. Use no máximo 120 letras.")

    # Ninguem digita telefone no formato que o WhatsApp usa. O pessoal
    # escreve (77) 99999-8888, 77999998888, +55 77 99999-8888... Entao
    # eu jogo fora tudo que nao e numero e monto o formato certo olhando
    # o tamanho: 10 ou 11 digitos e numero daqui sem o codigo do pais,
    # 12 ou 13 comecando com 55 ja veio completo. Tem que ficar nesse
    # padrao porque e assim que o envio manda a mensagem.
    somente_digitos = "".join(c for c in telefone if c.isdigit())
    if len(somente_digitos) in (10, 11):
        telefone_e164 = f"+55{somente_digitos}"
    elif len(somente_digitos) in (12, 13) and somente_digitos.startswith("55"):
        telefone_e164 = f"+{somente_digitos}"
    else:
        telefone_e164 = ""
        erros.append("Informe um telefone válido com DDD, como (77) 99999-8888.")

    # Sem o aceite eu nao gravo o agendamento. Todo agendamento gera
    # mensagem no WhatsApp depois, e mandar mensagem sem autorizacao e
    # exatamente o que a LGPD nao deixa.
    if not aceite:
        erros.append("É necessário autorizar o envio de mensagens para agendar.")

    # O horario tem que ser um dos que o sistema realmente oferece.
    #
    # Sem essa conferencia dava pra montar o formulario na mao e marcar
    # as 3h da manha de um domingo: a trava do banco so impede dois
    # agendamentos em cima um do outro, ela nao sabe que horas o
    # profissional atende. Quem sabe isso e o horarios.py.
    #
    # De quebra isso cobre o caso do horario ter sido tomado enquanto o
    # cliente preenchia os dados - ele volta pra grade com um aviso em
    # vez de bater no erro do banco.
    #
    # O localtime antes do .date() importa: o inicio esta em UTC, e um
    # horario das 21h aqui ja e do dia seguinte la.
    livres = horarios_disponiveis(
        profissional, servico, timezone.localtime(inicio).date()
    )
    if inicio not in livres:
        erros.append("Esse horário não está mais disponível. Escolha outro.")

    # As perguntas que o profissional montou pra este servico. Se nao
    # tiver nenhuma, isso vem vazio e o fluxo segue igual ao de sempre.
    perguntas = _perguntas_do_servico(servico)
    respondidas = []
    for pergunta in perguntas:
        valor = request.POST.get(f"pergunta_{pergunta.pk}", "").strip()[:1000]
        if pergunta.obrigatoria and not valor:
            erros.append(f'Responda: "{pergunta.texto}"')
        respondidas.append((pergunta, valor))

    # Deu erro em alguma coisa: devolvo a mesma tela ja com o que ele
    # tinha digitado. Odeio formulario que apaga tudo quando erra um
    # campo so.
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
                "perguntas": perguntas,
                "respostas_enviadas": {p.pk: v for p, v in respondidas},
                "texto_consentimento": TEXTO_CONSENTIMENTO,
            },
        )

    # Pego o local principal do profissional. Quem atende online ou a
    # domicilio nao tem local cadastrado, e ai fica None mesmo - o
    # campo aceita nulo justamente por causa disso.
    local = profissional.locais.filter(principal=True).first()

    try:
        # Daqui pra baixo e tudo ou nada. Sao tres gravacoes seguidas
        # (cliente, consentimento e agendamento) e nao pode sobrar
        # cliente cadastrado sem agendamento se travar no meio do
        # caminho.
        with transaction.atomic():
            # O cliente e identificado pelo telefone dentro de cada
            # profissional. Se ele ja marcou antes com essa pessoa, eu
            # reaproveito o cadastro em vez de criar outro igual.
            cliente, _ = Cliente.objects.get_or_create(
                profissional=profissional,
                telefone=telefone_e164,
                defaults={"nome": nome},
            )

            # So registro o aceite se ele ainda nao tiver um valendo.
            # Nao adianta empilhar registro identico toda vez que a
            # pessoa agenda de novo.
            if not cliente.consentimentos.filter(revogado_em__isnull=True).exists():
                ConsentimentoOptIn.objects.create(
                    cliente=cliente,
                    origem=ConsentimentoOptIn.Origem.AGENDAMENTO_ONLINE,
                    texto_apresentado=TEXTO_CONSENTIMENTO,
                )

            # Nao passo o fim aqui: o proprio Agendamento calcula pela
            # duracao do servico quando salva.
            agendamento = Agendamento.objects.create(
                profissional=profissional,
                cliente=cliente,
                servico=servico,
                local=local,
                inicio=inicio,
                observacoes=observacoes,
            )

            # Gravo o texto da pergunta e a regra de alerta junto da
            # resposta. Se o profissional editar a pergunta depois, o
            # registro continua mostrando o que a pessoa leu de fato.
            Resposta.objects.bulk_create([
                Resposta(
                    agendamento=agendamento,
                    pergunta=pergunta,
                    texto_pergunta=pergunta.texto,
                    resposta=valor,
                    alerta_se=pergunta.alerta_se,
                )
                for pergunta, valor in respondidas
                if valor
            ])
    except IntegrityError:
        # Aqui e aquela restricao do models.py entrando em acao. Cai
        # nesse ponto quando duas pessoas estavam com a mesma tela
        # aberta e as duas clicaram em confirmar quase junto: uma grava
        # e a outra bate no banco. Em vez de estourar erro 500, eu
        # devolvo pra grade avisando o que aconteceu.
        messages.error(
            request,
            "Esse horário acabou de ser preenchido. Escolha outro, por favor.",
        )
        return redirect("publico_horario", slug=slug, servico_id=servico.pk)

    return redirect("publico_sucesso", codigo=agendamento.codigo)


def sucesso(request, codigo):
    """Passo 4: o comprovante com o codigo do agendamento.

    Uso o codigo (UUID) na URL em vez do id. Com o id o cliente podia
    trocar o numero e ver o agendamento dos outros; com UUID nao tem
    como adivinhar.
    """
    # O select_related evita uma consulta separada pra cada dado que o
    # template mostra na tela.
    agendamento = get_object_or_404(
        Agendamento.objects.select_related(
            "profissional", "servico", "cliente", "local"
        ),
        codigo=codigo,
    )
    return render(request, "publico/sucesso.html", {"agendamento": agendamento})


def cancelar_pelo_cliente(request, codigo):
    """O cliente cancelando sozinho, pelo link do comprovante.

    Essa e a parte que ataca a falta direto. No questionario todo mundo
    falou que o cliente some sem avisar, e quase sempre e porque avisar
    da trabalho. Se cancelar for facil - um link, sem login - pelo menos
    o horario volta a ficar livre a tempo de encaixar outra pessoa.
    """
    agendamento = get_object_or_404(
        Agendamento.objects.select_related("profissional", "servico", "cliente"),
        codigo=codigo,
    )

    # Cancelar so por POST, nunca por GET. Se fosse por GET, o proprio
    # WhatsApp cancelaria o agendamento sozinho na hora de gerar a
    # previa do link.
    if request.method == "POST":
        # So cancelo o que ainda esta de pe. Se ja foi cancelado ou
        # concluido, nao mexo em nada.
        if agendamento.status in Agendamento.STATUS_OCUPAM_AGENDA:
            agendamento.status = Agendamento.Status.CANCELADO
            agendamento.cancelado_em = timezone.now()
            agendamento.motivo_cancelamento = "Cancelado pelo cliente"
            agendamento.save()

            # Esse import fica aqui dentro de proposito: o modulo de
            # notificacoes importa o agenda.models, entao se eu botasse
            # esse import la em cima os dois apps ficariam se puxando um
            # pro outro. Aqui dentro ele so carrega quando alguem
            # cancela de verdade.
            from notificacoes import envio as envio_notificacoes

            # Aviso o profissional na hora. Sem isso ele so ia descobrir
            # o horario vago quando abrisse o sistema, e podia perder a
            # chance de encaixar outro cliente naquele lugar.
            envio_notificacoes.avisar_cancelamento_pelo_cliente(agendamento)

            messages.success(request, "Agendamento cancelado.")
        return redirect("publico_sucesso", codigo=codigo)

    return render(request, "publico/cancelar.html", {"agendamento": agendamento})


def avaliar(request, codigo):
    """O cliente dando a nota do atendimento (RF13).

    Chega aqui pelo link que vai na mensagem de WhatsApp, depois que o
    profissional marca o atendimento como concluido. Sem login e sem
    cadastro, igual ao resto do fluxo publico - o codigo do agendamento
    ja identifica quem e.

    Esse requisito foi o unico que os 11 respondentes do questionario
    pediram sem excecao.
    """
    agendamento = get_object_or_404(
        Agendamento.objects.select_related("profissional", "servico", "cliente"),
        codigo=codigo,
    )

    # Se ja avaliou, mostro a nota que ele deu em vez de deixar
    # responder de novo. O modelo e OneToOne, entao um segundo envio
    # daria erro de banco - prefiro tratar aqui e dar uma tela decente.
    avaliacao = Feedback.objects.filter(agendamento=agendamento).first()

    # So faz sentido avaliar o que aconteceu. Cancelado, faltou ou ainda
    # por vir nao tem o que opinar.
    concluido = agendamento.status == Agendamento.Status.CONCLUIDO

    if request.method == "POST" and concluido and avaliacao is None:
        nota = request.POST.get("nota", "")
        comentario = request.POST.get("comentario", "").strip()

        # A nota vem de um radio, mas da pra forjar o POST. Confiro
        # contra as opcoes que existem de verdade.
        notas_validas = [str(n) for n, _ in Feedback._meta.get_field("nota").choices]

        if nota not in notas_validas:
            messages.error(request, "Escolha uma nota de 1 a 5.")
        else:
            avaliacao = Feedback.objects.create(
                agendamento=agendamento,
                nota=int(nota),
                comentario=comentario[:1000],
            )
            messages.success(request, "Obrigado! Sua avaliação foi registrada.")
            return redirect("publico_avaliar", codigo=codigo)

    return render(
        request,
        "publico/avaliar.html",
        {
            "agendamento": agendamento,
            "profissional": agendamento.profissional,
            "avaliacao": avaliacao,
            "concluido": concluido,
            "notas": Feedback._meta.get_field("nota").choices,
        },
    )