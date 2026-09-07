"""
Contrato entre os templates aprovados na Meta e o código que os dispara.

A API do WhatsApp identifica as variáveis pela POSIÇÃO, não pelo nome:
o que segue como primeiro parâmetro preenche o {{1}} do template. Se a
ordem divergir do que foi submetido, o cliente recebe a data no lugar do nome.

Este módulo é o único lugar onde essa ordem é definida. Alterou o template no
WhatsApp Manager, altere aqui — e vice-versa.
"""

from dataclasses import dataclass

IDIOMA = "pt_BR"


@dataclass(frozen=True)
class TemplateWhatsApp:
    """Descreve um template aprovado na Meta."""

    nome: str
    categoria: str
    variaveis: tuple
    botoes: tuple = ()

    @property
    def total_variaveis(self):
        return len(self.variaveis)


# --- Dirigidos ao cliente ----------------------------------------------------

CONFIRMACAO = TemplateWhatsApp(
    nome="confirmacao_agendamento",
    categoria="UTILITY",
    variaveis=("nome", "servico", "data", "horario", "profissional"),
)

LEMBRETE = TemplateWhatsApp(
    nome="lembrete_agendamento",
    categoria="UTILITY",
    variaveis=("nome", "servico", "data", "horario", "local"),
    # A ordem dos botões espelha a submissão na Meta. O webhook identifica
    # a resposta pelo índice, então inverter aqui inverte o comportamento.
    botoes=("Confirmar presença", "Preciso cancelar"),
)

CANCELAMENTO = TemplateWhatsApp(
    nome="cancelamento_agendamento",
    categoria="UTILITY",
    variaveis=("nome", "servico", "data", "horario"),
)

FEEDBACK = TemplateWhatsApp(
    nome="solicitacao_feedback",
    categoria="UTILITY",
    variaveis=("nome", "servico"),
)


# --- Dirigidos ao profissional -----------------------------------------------
# Chegam pelo mesmo canal, mas com destinatário diferente. Pela via oficial
# exigem template próprio aprovado; pelos demais canais são texto livre.

NOVO_AGENDAMENTO = TemplateWhatsApp(
    nome="novo_agendamento",
    categoria="UTILITY",
    variaveis=("cliente", "servico", "data", "horario", "telefone"),
)

CANCELAMENTO_CLIENTE = TemplateWhatsApp(
    nome="cancelamento_pelo_cliente",
    categoria="UTILITY",
    variaveis=("cliente", "servico", "data", "horario"),
)


TODOS = (
    CONFIRMACAO,
    LEMBRETE,
    CANCELAMENTO,
    FEEDBACK,
    NOVO_AGENDAMENTO,
    CANCELAMENTO_CLIENTE,
)


# Índices dos botões de resposta rápida do template de lembrete.
BOTAO_CONFIRMAR = 0
BOTAO_CANCELAR = 1

# Palavras que o cliente pode digitar em vez de tocar no botão.
PALAVRAS_CONFIRMAR = {"confirmo", "confirmar", "sim", "ok", "1"}
PALAVRAS_CANCELAR = {"cancelar", "cancela", "nao", "não", "2"}
PALAVRAS_DESCADASTRO = {"sair", "parar", "stop", "descadastrar"}


def _formatar_data(momento):
    return momento.strftime("%d/%m/%Y")


def _formatar_hora(momento):
    return momento.strftime("%H:%M")


def parametros_confirmacao(agendamento):
    """Monta os parâmetros de confirmacao_agendamento, em ordem."""
    inicio = agendamento.inicio
    return [
        agendamento.cliente.nome,
        agendamento.servico.nome,
        _formatar_data(inicio),
        _formatar_hora(inicio),
        agendamento.profissional.nome_publico,
    ]


def parametros_lembrete(agendamento):
    """Monta os parâmetros de lembrete_agendamento, em ordem."""
    inicio = agendamento.inicio
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
    """Monta os parâmetros de cancelamento_agendamento, em ordem."""
    inicio = agendamento.inicio
    return [
        agendamento.cliente.nome,
        agendamento.servico.nome,
        _formatar_data(inicio),
        _formatar_hora(inicio),
    ]


def parametros_feedback(agendamento):
    """Monta os parâmetros de solicitacao_feedback, em ordem."""
    return [
        agendamento.cliente.nome,
        agendamento.servico.nome,
    ]


def parametros_novo_agendamento(agendamento):
    """Aviso ao profissional de que alguém marcou um horário."""
    inicio = agendamento.inicio
    return [
        agendamento.cliente.nome,
        agendamento.servico.nome,
        _formatar_data(inicio),
        _formatar_hora(inicio),
        agendamento.cliente.telefone,
    ]


def parametros_cancelamento_cliente(agendamento):
    """Aviso ao profissional de que o cliente desmarcou."""
    inicio = agendamento.inicio
    return [
        agendamento.cliente.nome,
        agendamento.servico.nome,
        _formatar_data(inicio),
        _formatar_hora(inicio),
    ]


def texto_equivalente(template, parametros):
    """Reconstrói o texto da mensagem para registro e auditoria.

    A Meta não devolve o conteúdo renderizado; guardar essa reconstrução
    permite saber depois exatamente o que foi enviado a cada destinatário —
    útil para depuração e para demonstrar conformidade com a LGPD.

    Nos canais que enviam texto livre, é este texto que sai de fato.
    """
    modelos = {
        CONFIRMACAO.nome: (
            "Olá, {0}! Seu agendamento foi registrado.\n\n"
            "Serviço: {1}\nData: {2}\nHorário: {3}\nProfissional: {4}\n\n"
            "Se precisar remarcar, é só responder esta mensagem."
        ),
        LEMBRETE.nome: (
            "Olá, {0}! Passando para lembrar do seu horário.\n\n"
            "Serviço: {1}\nData: {2} às {3}\nLocal: {4}\n\n"
            "Podemos confirmar sua presença?"
        ),
        CANCELAMENTO.nome: (
            "Olá, {0}. Seu agendamento de {1}, marcado para {2} às {3}, "
            "foi cancelado.\n\n"
            "Para escolher um novo horário, use o botão abaixo."
        ),
        FEEDBACK.nome: (
            "Olá, {0}! Como foi seu atendimento de {1}?\n\n"
            "Sua avaliação leva menos de um minuto e ajuda o profissional "
            "a melhorar o serviço."
        ),
        NOVO_AGENDAMENTO.nome: (
            "Novo agendamento na sua agenda.\n\n"
            "Cliente: {0}\nServiço: {1}\nData: {2} às {3}\nContato: {4}"
        ),
        CANCELAMENTO_CLIENTE.nome: (
            "{0} cancelou o agendamento de {1}, que estava marcado para "
            "{2} às {3}.\n\n"
            "O horário voltou a ficar disponível."
        ),
    }
    modelo = modelos.get(template.nome)
    if modelo is None:
        return " | ".join(str(p) for p in parametros)
    return modelo.format(*parametros)


def link_wa_me(telefone_e164, texto):
    """Gera o link de contingência wa.me.

    O profissional abre o link e envia a mensagem com um toque. Não depende
    de canal, credencial nem serviço externo — é a garantia de que a
    demonstração funcione mesmo se tudo o mais estiver fora do ar.
    """
    from urllib.parse import quote

    numero = telefone_e164.lstrip("+")
    return f"https://wa.me/{numero}?text={quote(texto)}"