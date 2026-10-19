"""
As regras de quando avisar, quem avisar e o que dizer.

Quem manda a mensagem de fato e o canal (notificacoes/canais.py). Aqui
fica so a decisao: criar o aviso, marcar a hora, escolher o destinatario
e cuidar da fila.

Essa separacao e de proposito. Esse arquivo nao sabe nada sobre a
Evolution: se um dia eu trocar de provedor de WhatsApp, quem muda e o
canais.py e nada aqui.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from agenda.models import Agendamento

from . import mensagens
from .canais import ErroEnvio, obter_canal
from .models import Notificacao

logger = logging.getLogger(__name__)

# Trava de seguranca pra uma mensagem nao ficar tentando pra sempre. Se
# o numero estiver errado, nao adianta insistir infinitamente.
#
# O valor mora no modelo porque o marcar_falha tambem precisa dele. Se
# eu escrevesse o numero aqui tambem, uma hora eu mudava um e esquecia o
# outro.
MAXIMO_TENTATIVAS = Notificacao.MAXIMO_TENTATIVAS

# Essa tabela e o coracao do arquivo. Cada tipo de aviso declara tres
# coisas: qual template usar, qual funcao monta os parametros e quem
# recebe.
#
# Fiz como dicionario em vez de if/elif espalhado pelo codigo. Assim,
# criar um tipo novo de aviso e acrescentar uma linha aqui - nao e sair
# cacando condicional em cinco lugares diferentes.
#
# Repare que quatro avisos vao pro cliente e dois vao pro profissional.
# Foi o questionario que mostrou isso: o profissional tambem quer ser
# avisado, principalmente quando o cliente desmarca.
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
    # O telefone e copiado pra dentro da notificacao no momento em que
    # ela e criada. Se o cliente trocar de numero depois, o historico
    # continua mostrando pra onde a mensagem realmente foi.
    if destinatario == Notificacao.Destinatario.PROFISSIONAL:
        return agendamento.profissional.telefone
    return agendamento.cliente.telefone


def _pode_notificar(agendamento, destinatario):
    """Confere se pode mandar mensagem pra essa pessoa.

    O consentimento so vale pro cliente. Ele e o titular dos dados que a
    LGPD protege e e o destinatario que a politica da Meta protege.

    Pro profissional eu nao peco consentimento porque nao tem terceiro
    envolvido: ele recebe aviso sobre a propria agenda, no telefone que
    ele mesmo cadastrou no sistema. So confiro se o numero existe.
    """
    if destinatario == Notificacao.Destinatario.PROFISSIONAL:
        return bool(agendamento.profissional.telefone)
    return agendamento.cliente.pode_receber_mensagem


def criar_notificacao(agendamento, tipo, agendada_para=None):
    template, montar, destinatario = CONFIGURACAO[tipo]

    if not _pode_notificar(agendamento, destinatario):
        # Sem consentimento ou sem telefone eu simplesmente nao crio.
        # Registro no log pra depois eu saber por que aquele cliente nao
        # recebeu nada.
        logger.info(
            "Notificação %s não criada: destinatário %s indisponível.",
            tipo, destinatario,
        )
        return None

    parametros = montar(agendamento)

    # Sem hora marcada quer dizer "manda agora". Guardo essa informacao
    # numa variavel porque logo abaixo eu preciso saber a diferenca: o
    # que e pra agora sai na hora, o que tem hora marcada (o lembrete)
    # fica na fila esperando.
    imediata = agendada_para is None
    if imediata:
        agendada_para = timezone.now()

    try:
        notificacao = Notificacao.objects.create(
            agendamento=agendamento,
            tipo=tipo,
            destinatario=destinatario,
            telefone=_telefone_do_destinatario(agendamento, destinatario),
            template=template.nome,
            # Gravo o texto ja montado agora, na criacao. Assim o
            # historico guarda exatamente o que ia ser enviado, mesmo
            # que eu mude a redacao do template depois.
            conteudo=mensagens.texto_equivalente(template, parametros),
            agendada_para=agendada_para,
        )
    except IntegrityError:
        # Bateu na restricao de unico por agendamento + tipo. Quer dizer
        # que esse aviso ja existe, entao nao e erro - e o banco
        # impedindo mensagem repetida. Devolvo None e sigo.
        return None

    if imediata:
        _mandar_ao_gravar(notificacao)

    return notificacao


def _mandar_ao_gravar(notificacao):
    """Manda a mensagem assim que o agendamento estiver gravado.

    Antes, a confirmacao nascia pendente e ficava esperando o comando
    agendado passar. Na pratica isso significava o cliente terminar de
    marcar o horario e nao receber nada, as vezes por varios minutos -
    justamente no momento em que ele esta em duvida se deu certo.

    Duas decisoes importantes aqui:

    O on_commit faz a mensagem sair depois que a transacao fecha, nao
    dentro dela. Se eu mandasse no meio, e o agendamento desse erro logo
    em seguida e fosse desfeito, o cliente ficaria com um WhatsApp
    confirmando um horario que nao existe. Mensagem enviada nao volta
    atras; gravacao no banco volta.

    E o erro e engolido de proposito. A mensagem continua PENDENTE no
    banco se nao sair, entao o comando agendado tenta de novo depois. A
    fila nao deixou de existir - ela virou a rede de seguranca em vez de
    ser o caminho normal.
    """
    if not getattr(settings, "ENVIO_IMEDIATO", True):
        return

    identificador = notificacao.pk

    def mandar():
        # Busco de novo em vez de usar o objeto que eu ja tenho na mao.
        # Entre a criacao e o commit pode ter acontecido coisa - o
        # proprio cancelamento derruba as pendentes - e ai essa mensagem
        # nao deve mais sair.
        try:
            pendente = Notificacao.objects.select_related(
                "agendamento",
                "agendamento__cliente",
                "agendamento__servico",
                "agendamento__profissional",
                "agendamento__local",
            ).get(pk=identificador, situacao=Notificacao.Situacao.PENDENTE)
        except Notificacao.DoesNotExist:
            return

        try:
            enviar(pendente)
        except Exception as erro:
            # Falha ao avisar nunca pode derrubar o agendamento. O
            # compromisso com o cliente vale mais que a mensagem.
            logger.exception("Erro no envio imediato: %s", erro)

    transaction.on_commit(mandar)


def programar_do_agendamento(agendamento):
    """Cria os avisos de um agendamento novo.

    Sao tres: a confirmacao pro cliente e o aviso pro profissional, que
    saem na hora, e o lembrete, que fica guardado pra sair mais perto do
    atendimento.
    """
    criadas = []

    # Esses dois saem imediatamente, sem hora marcada.
    for tipo in (
        Notificacao.Tipo.CONFIRMACAO,
        Notificacao.Tipo.NOVO_AGENDAMENTO,
    ):
        notificacao = criar_notificacao(agendamento, tipo)
        if notificacao:
            criadas.append(notificacao)

    # Quantas horas antes o lembrete sai fica na configuracao. Deixei 24
    # como padrao porque foi o que o pessoal indicou no questionario.
    horas = getattr(settings, "LEMBRETE_ANTECEDENCIA_HORAS", 24)
    momento_lembrete = agendamento.inicio - timedelta(hours=horas)

    # Se marcaram pra daqui a duas horas, o lembrete de 24h cairia no
    # passado e sairia junto com a confirmacao - duas mensagens quase
    # iguais em seguida. Nesse caso eu simplesmente nao crio.
    if momento_lembrete > timezone.now():
        lembrete = criar_notificacao(
            agendamento, Notificacao.Tipo.LEMBRETE, agendada_para=momento_lembrete
        )
        if lembrete:
            criadas.append(lembrete)

    return criadas


def avisar_cancelamento_pelo_cliente(agendamento):
    """Avisa o profissional que o cliente desmarcou.

    E chamado la na view publica de cancelamento. Sem isso o
    profissional so ia descobrir o horario vago quando abrisse o
    sistema - e ate la ja teria perdido a chance de encaixar alguem.
    """
    return criar_notificacao(agendamento, Notificacao.Tipo.CANCELAMENTO_CLIENTE)


def pedir_avaliacao(agendamento):
    """Pede a avaliacao do cliente depois do atendimento (RF13).

    E chamada pelo sinal, quando o profissional marca o agendamento
    como concluido. Fiz assim em vez de chamar na view porque a
    conclusao pode vir de tres lugares diferentes - o painel, o admin e
    o comando - e eu nao queria depender de lembrar de chamar em cada
    um.

    A mensagem leva o link com o codigo do agendamento, entao o cliente
    avalia sem login e sem cadastro, igual ao resto do sistema.
    """
    return criar_notificacao(agendamento, Notificacao.Tipo.FEEDBACK)


def cancelar_pendentes(agendamento, apenas_do_cliente=True):
    """Cancela os avisos que ainda nao sairam.

    Serve pra nao mandar lembrete de um atendimento que ja foi
    desmarcado - seria constrangedor o cliente receber "seu horario e
    amanha" depois de ter cancelado.

    Por padrao eu poupo os avisos que vao pro profissional, porque
    esses continuam fazendo sentido mesmo depois do cancelamento: ele
    precisa justamente saber que desmarcaram.
    """
    pendentes = agendamento.notificacoes.filter(
        situacao=Notificacao.Situacao.PENDENTE
    )
    if apenas_do_cliente:
        pendentes = pendentes.filter(destinatario=Notificacao.Destinatario.CLIENTE)

    # update() faz tudo num UPDATE so, sem carregar objeto por objeto.
    return pendentes.update(situacao=Notificacao.Situacao.CANCELADA)


def enviar(notificacao, canal=None):
    # O canal sai do profissional dono do agendamento. Cada prestador
    # tem o proprio WhatsApp conectado, e a mensagem tem que sair pelo
    # numero dele - o cliente marcou com ele, nao com o sistema.
    canal = canal or obter_canal(notificacao.agendamento.profissional)

    if canal is None:
        # Sessao nao conectada ou contêiner parado. Registro a falha e
        # saio: a notificacao continua PENDENTE enquanto nao estourar o
        # limite de tentativas, entao o comando agendado tenta de novo
        # depois - quando o WhatsApp voltar, a mensagem sai sozinha.
        notificacao.marcar_falha("WhatsApp do profissional não está conectado.")
        return False

    try:
        id_mensagem = canal.enviar(notificacao.telefone, notificacao.conteudo)
    except ErroEnvio as erro:
        # Nao deixo o erro subir. Se subisse, uma mensagem com problema
        # derrubaria o comando inteiro e as outras da fila nem seriam
        # tentadas.
        notificacao.marcar_falha(erro)
        logger.warning("Falha ao enviar notificação %s: %s", notificacao.pk, erro)
        return False

    # Guardo o identificador que o provedor devolveu. E com ele que o
    # webhook depois consegue casar o evento de "entregue" ou "lido"
    # com a mensagem certa aqui do banco.
    notificacao.marcar_enviada(canal.nome, id_mensagem)
    return True


def enviar_pendentes(limite=100, canal=None):
    """Roda a fila: pega o que ja venceu e manda.

    E essa funcao que o comando `manage.py enviar_lembretes` chama.
    Foi ela que me deixou tirar o Celery e o Redis do projeto - um
    comando agendado no cron resolve o mesmo problema com uma peca a
    menos pra instalar e explicar.
    """
    # Aqui estava o erro que eu consertei: antes eu montava UM canal e
    # usava ele pra fila inteira. Com sessao por prestador isso ficou
    # errado - a mensagem do cliente do Marcos sairia pelo WhatsApp de
    # quem estivesse configurado no .env.
    #
    # Agora eu guardo um canal por profissional neste dicionario. Fica
    # um por prestador, e nao um por mensagem: se ele tem dez lembretes
    # pra sair, os dez usam o mesmo objeto.
    #
    # O parametro canal continua existindo, mas so pra teste: quando eu
    # passo um canal na mao, ele vale pra todas e o dicionario nem entra
    # em acao.
    canais_por_profissional = {}
    agora = timezone.now()

    fila = (
        Notificacao.objects.filter(
            situacao=Notificacao.Situacao.PENDENTE,
            # Só o que ja passou da hora marcada. O lembrete de amanha
            # fica quieto ate chegar a vez dele.
            agendada_para__lte=agora,
            tentativas__lt=MAXIMO_TENTATIVAS,
        )
        # Esse select_related e importante: pra cada mensagem eu preciso
        # do cliente, do servico, do profissional e do local pra montar
        # o texto. Sem ele seriam cinco consultas por mensagem.
        .select_related(
            "agendamento", "agendamento__cliente", "agendamento__servico",
            "agendamento__profissional", "agendamento__local",
        )
        # Mais antiga primeiro, pra ninguem furar a fila.
        .order_by("agendada_para")[:limite]
    )

    resultado = {"enviadas": 0, "falhas": 0, "puladas": 0}

    for notificacao in fila:
        agendamento = notificacao.agendamento

        # Ultima conferida antes de mandar o lembrete: se o agendamento
        # foi cancelado ou ja aconteceu, o lembrete perdeu o sentido.
        # Isso pega o caso do cancelamento ter sido feito direto no
        # painel, sem passar pelo cancelar_pendentes.
        if (
            notificacao.tipo == Notificacao.Tipo.LEMBRETE
            and agendamento.status not in Agendamento.STATUS_OCUPAM_AGENDA
        ):
            notificacao.cancelar()
            resultado["puladas"] += 1
            continue

        # Pego o canal desse profissional, ou monto na primeira vez que
        # aparecer alguem dele na fila.
        if canal is not None:
            canal_do_envio = canal
        else:
            profissional = agendamento.profissional
            if profissional.pk not in canais_por_profissional:
                canais_por_profissional[profissional.pk] = obter_canal(
                    profissional
                )
            canal_do_envio = canais_por_profissional[profissional.pk]

        # O canal pode ser None quando a sessao daquele profissional nao
        # esta conectada. Passo assim mesmo: o enviar trata e deixa a
        # mensagem pendente pra proxima rodada.
        if enviar(notificacao, canal=canal_do_envio):
            resultado["enviadas"] += 1
        else:
            resultado["falhas"] += 1

    # Devolvo o resumo pro comando conseguir imprimir quantas sairam,
    # quantas falharam e quantas foram descartadas.
    return resultado


def link_manual(notificacao):
    """Monta um link wa.me pro profissional mandar na mao.

    E o plano B do plano B. Nao depende de canal, de credencial nem de
    servico externo: abre a conversa no WhatsApp com o texto ja
    escrito, e o profissional so aperta enviar.

    Deixei isso porque a demonstracao da defesa nao pode depender de o
    contêiner estar no ar.
    """
    return mensagens.link_wa_me(notificacao.telefone, notificacao.conteudo)