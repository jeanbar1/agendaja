"""
O texto de cada mensagem que o sistema manda.

Cada mensagem tem um nome e um texto com espacos numerados - {0}, {1} e
assim por diante. Os valores que entram nesses espacos vem das funcoes
parametros_* la embaixo, e entram POR POSICAO: o primeiro item da lista
cai no {0}, o segundo no {1}.

E por isso que a ordem importa. Se eu trocar a ordem da lista sem trocar
o texto, o cliente recebe a data no lugar do nome e o sistema nao percebe
nada de errado - pra ele os dois sao texto do mesmo jeito.

Deixei o texto e a lista de valores no mesmo arquivo justamente pra isso
ficar conferivel de olho: o texto esta em cima, a funcao que preenche
esta embaixo, e da pra ler os dois lado a lado.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Mensagem:
    """Uma mensagem do sistema: o nome dela e o texto que sai.

    O nome fica gravado em cada Notificacao. Serve pra eu conseguir
    filtrar o historico depois por tipo de mensagem, e pra saber qual
    redacao gerou aquele registro.

    Uso frozen=True pra ninguem conseguir mudar o texto em tempo de
    execucao. O que foi enviado ja foi - a redacao nao muda por baixo.
    """

    nome: str
    texto: str

    def montar(self, parametros):
        """Preenche os espacos numerados com os valores, na ordem."""
        return self.texto.format(*parametros)


# --- Dirigidas ao cliente ----------------------------------------------------

CONFIRMACAO = Mensagem(
    nome="confirmacao_agendamento",
    texto=(
        "Olá, {0}! Seu agendamento foi registrado.\n\n"
        "Serviço: {1}\nData: {2}\nHorário: {3}\nProfissional: {4}\n\n"
        "Se precisar remarcar, é só responder esta mensagem."
    ),
)

LEMBRETE = Mensagem(
    nome="lembrete_agendamento",
    texto=(
        "Olá, {0}! Passando para lembrar do seu horário.\n\n"
        "Serviço: {1}\nData: {2} às {3}\nLocal: {4}\n\n"
        "Podemos confirmar sua presença? Responda CONFIRMAR para "
        "confirmar ou CANCELAR se não puder vir."
    ),
)

CANCELAMENTO = Mensagem(
    nome="cancelamento_agendamento",
    texto=(
        "Olá, {0}. Seu agendamento de {1}, marcado para {2} às {3}, "
        "foi cancelado.\n\n"
        "Para escolher um novo horário, é só acessar o link de "
        "agendamento de novo."
    ),
)

FEEDBACK = Mensagem(
    nome="solicitacao_feedback",
    texto=(
        "Olá, {0}! Como foi seu atendimento de {1}?\n\n"
        "Sua avaliação leva menos de um minuto e ajuda bastante:\n"
        "{2}"
    ),
)


# --- Dirigidas ao profissional -----------------------------------------------
# Saem pela mesma sessao de WhatsApp, so muda quem recebe.

NOVO_AGENDAMENTO = Mensagem(
    nome="novo_agendamento",
    texto=(
        "Novo agendamento na sua agenda.\n\n"
        "Cliente: {0}\nServiço: {1}\nData: {2} às {3}\nContato: {4}"
    ),
)

CANCELAMENTO_CLIENTE = Mensagem(
    nome="cancelamento_pelo_cliente",
    texto=(
        "{0} cancelou o agendamento de {1}, que estava marcado para "
        "{2} às {3}.\n\n"
        "O horário voltou a ficar disponível."
    ),
)


# Nem todo mundo escreve a palavra inteira, e no celular quase ninguem
# acentua - por isso "nao" aparece sem acento junto com "não".
PALAVRAS_CONFIRMAR = {"confirmo", "confirmar", "sim", "ok", "1"}
PALAVRAS_CANCELAR = {"cancelar", "cancela", "nao", "não", "2"}
PALAVRAS_DESCADASTRO = {"sair", "parar", "stop", "descadastrar"}


def _formatar_data(momento):
    # Formato brasileiro. A pessoa que le a mensagem nao quer 2026-08-14.
    return momento.strftime("%d/%m/%Y")


def _formatar_hora(momento):
    return momento.strftime("%H:%M")


def parametros_confirmacao(agendamento):
    """Os valores de CONFIRMACAO, na ordem dos espacos do texto."""
    inicio = agendamento.inicio
    return [
        agendamento.cliente.nome,
        agendamento.servico.nome,
        _formatar_data(inicio),
        _formatar_hora(inicio),
        agendamento.profissional.nome_publico,
    ]


def parametros_lembrete(agendamento):
    """Os valores de LEMBRETE, na ordem dos espacos do texto."""
    inicio = agendamento.inicio

    # O local e o unico que tem regra. Quando existe local cadastrado eu
    # mando nome e endereco juntos, pra pessoa saber pra onde ir. Quando
    # nao existe (atendimento online ou a domicilio), uso a modalidade
    # do servico no lugar - assim o campo nunca sai vazio na mensagem.
    if agendamento.local:
        local = agendamento.local.nome
        if agendamento.local.endereco:
            local = f"{local}, {agendamento.local.endereco}"
    else:
        local = agendamento.servico.get_modalidade_display()

    return [
        agendamento.cliente.nome,
        agendamento.servico.nome,
        _formatar_data(inicio),
        _formatar_hora(inicio),
        local,
    ]


def parametros_cancelamento(agendamento):
    """Os valores de CANCELAMENTO, na ordem dos espacos do texto."""
    inicio = agendamento.inicio
    return [
        agendamento.cliente.nome,
        agendamento.servico.nome,
        _formatar_data(inicio),
        _formatar_hora(inicio),
    ]


def parametros_feedback(agendamento):
    """Os valores de FEEDBACK, na ordem dos espacos do texto.

    Esse e o unico que monta um link. Uso o codigo (UUID) do
    agendamento, entao o cliente nao precisa de login nem de senha pra
    avaliar - so clicar.
    """
    # Importo aqui dentro pra nao carregar configuracao do Django no
    # topo de um modulo que e so texto.
    from django.conf import settings
    from django.urls import reverse

    caminho = reverse("publico_avaliar", args=[agendamento.codigo])

    return [
        agendamento.cliente.nome,
        agendamento.servico.nome,
        f"{settings.URL_BASE}{caminho}",
    ]


def parametros_novo_agendamento(agendamento):
    """Aviso pro profissional de que alguem marcou um horario.

    Aqui eu mando o telefone do cliente junto de proposito: assim ele
    consegue chamar a pessoa direto do WhatsApp, sem ter que abrir o
    sistema pra procurar o contato.
    """
    inicio = agendamento.inicio
    return [
        agendamento.cliente.nome,
        agendamento.servico.nome,
        _formatar_data(inicio),
        _formatar_hora(inicio),
        agendamento.cliente.telefone,
    ]


def parametros_cancelamento_cliente(agendamento):
    """Aviso pro profissional de que o cliente desmarcou."""
    inicio = agendamento.inicio
    return [
        agendamento.cliente.nome,
        agendamento.servico.nome,
        _formatar_data(inicio),
        _formatar_hora(inicio),
    ]


def link_wa_me(telefone_e164, texto):
    """Monta o link wa.me, que e o plano de emergencia.

    O profissional abre o link e a conversa ja aparece com o texto
    escrito - e so apertar enviar. Nao depende do contêiner, de
    credencial nem de servico de fora.

    E a garantia de que a demonstracao da defesa funciona mesmo se a
    Evolution estiver fora do ar ou a internet do IF falhar.
    """
    # Importo aqui dentro porque so essa funcao usa. Nao vale poluir o
    # topo do arquivo por causa de uma linha.
    from urllib.parse import quote

    numero = telefone_e164.lstrip("+")

    # O quote escapa acento, espaco e quebra de linha. Sem ele, o link
    # quebra no primeiro espaco do texto.
    return f"https://wa.me/{numero}?text={quote(texto)}"