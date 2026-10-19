"""
O envio de mensagem pelo WhatsApp.

Quem entrega a mensagem e a Evolution API, rodando em contêiner aqui na
maquina. Ela manda pelo proprio numero do profissional, vinculado lendo
um QR Code igual ao WhatsApp Web: nao precisa aprovar template, nao
precisa verificacao de negocio e nao precisa CNPJ.

Esse ultimo ponto foi o que decidiu a escolha. A API oficial da Meta
exige CNPJ pra rodar em producao, e boa parte dos profissionais
autonomos - que sao justamente o publico desse trabalho - nao tem. Se eu
amarrasse o sistema na Meta, ele so serviria pra quem ja e empresa.

A limitacao, que eu declaro no trabalho e nao escondo: a Evolution
funciona por cima da interface web do WhatsApp, o que vai contra os
termos de uso deles e pode dar bloqueio no numero. Ela serve pro
prototipo; pra rodar de verdade, o certo e o canal oficial.

Cada profissional tem a propria sessao. O cliente marcou com uma pessoa
especifica, entao a mensagem tem que sair do numero dela - nao de um
numero generico do sistema.
"""

import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Sem timeout, uma requisicao travada segura o processo pra sempre. Como
# o envio roda dentro de um comando agendado, isso deixaria o comando
# pendurado sem nunca terminar.
TEMPO_LIMITE = 15  # segundos


class ErroEnvio(Exception):
    """Deu errado na hora de entregar a mensagem pro provedor."""


class CanalEvolution:
    """A conversa com a Evolution API de um profissional.

    Uma instancia dessa classe aponta pra sessao de WhatsApp de UM
    profissional. Quem monta ela e o do_profissional, ou o obter_canal
    la embaixo.
    """

    nome = "evolution"

    def __init__(self, url=None, instancia=None, chave=None):
        # O rstrip("/") evita a barra dobrada quando alguem escreve a
        # URL terminando em / no .env.
        self.url = (url or settings.EVOLUTION_URL or "").rstrip("/")

        # A instancia e por profissional, entao ela NAO vem do .env:
        # quem sabe qual e o dono da mensagem tem que informar. Sem
        # instancia o canal se declara indisponivel e o envio nao sai -
        # que e melhor do que mandar pelo numero errado.
        self.instancia = instancia or ""
        self.chave = chave or settings.EVOLUTION_API_KEY

    @classmethod
    def do_profissional(cls, profissional):
        """Monta o canal ja apontando pra sessao desse profissional."""
        return cls(instancia=profissional.garantir_instancia_whatsapp())

    @property
    def disponivel(self):
        return bool(self.url and self.instancia and self.chave)

    @property
    def _cabecalhos(self):
        return {"apikey": self.chave, "Content-Type": "application/json"}

    @staticmethod
    def _so_digitos(telefone):
        # Eu guardo o telefone com o + na frente (+5577999998888), mas a
        # Evolution quer so os numeros. Tiro o sinal na hora de enviar
        # em vez de guardar de outro jeito.
        return telefone.lstrip("+")

    def enviar(self, telefone, texto):
        """Manda o texto e devolve o identificador da mensagem."""
        endereco = f"{self.url}/message/sendText/{self.instancia}"
        corpo = {"number": self._so_digitos(telefone), "text": texto}

        try:
            resposta = requests.post(
                endereco,
                headers=self._cabecalhos,
                data=json.dumps(corpo),
                timeout=TEMPO_LIMITE,
            )
        except requests.RequestException as erro:
            # Erro mais comum aqui e o contêiner estar parado, entao ja
            # deixo a dica na propria mensagem em vez de me perguntar
            # depois o que aconteceu.
            raise ErroEnvio(
                f"Não consegui falar com a Evolution em {self.url}. "
                f"O contêiner está no ar? Detalhe: {erro}"
            ) from erro

        if resposta.status_code >= 400:
            raise ErroEnvio(f"HTTP {resposta.status_code}: {resposta.text[:300]}")

        try:
            dados = resposta.json()
        except ValueError:
            # Mandou e nao devolveu JSON. Como o envio deu certo, sigo
            # em frente sem identificador em vez de dar erro.
            return ""

        return (dados.get("key") or {}).get("id", "")

    # -- Administração da sessão --------------------------------------------
    # "Instancia" aqui e uma sessao de WhatsApp ligada a um numero. Os
    # metodos abaixo sao pra tela de conexao e pro comando
    # `manage.py whatsapp`, que cuidam de conectar, ver o estado e
    # desconectar.

    def _pedir(self, metodo, caminho, corpo=None):
        # Junta o tratamento de erro num lugar so. Aceita qualquer verbo
        # HTTP porque a administracao usa GET, POST e DELETE.
        try:
            resposta = requests.request(
                metodo,
                f"{self.url}{caminho}",
                headers=self._cabecalhos,
                data=json.dumps(corpo) if corpo is not None else None,
                timeout=TEMPO_LIMITE,
            )
        except requests.RequestException as erro:
            raise ErroEnvio(
                f"Não consegui falar com a Evolution em {self.url}. "
                f"O contêiner está no ar? Detalhe: {erro}"
            ) from erro

        try:
            dados = resposta.json()
        except ValueError:
            dados = {}

        # Devolvo o codigo junto porque quem chama precisa distinguir
        # "nao existe" (404) de "existe mas esta desconectada".
        return resposta.status_code, dados

    @staticmethod
    def _mensagem_de_erro(dados):
        """Tira a explicacao de dentro da resposta de erro da Evolution.

        Ela devolve o motivo em response.message, mas nem sempre: tem
        caso em que "response" vem como texto ou como lista. O isinstance
        evita que o tratamento do erro vire outro erro.
        """
        resposta = dados.get("response")
        if isinstance(resposta, dict) and resposta.get("message"):
            return resposta["message"]
        return dados or "sem detalhe"

    def estado_da_conexao(self):
        """Diz se a sessao esta conectada no WhatsApp.

        Sem isso, o profissional so ia descobrir que a sessao caiu
        quando uma mensagem falhasse - ou seja, quando o cliente ja nao
        tivesse recebido o lembrete.
        """
        try:
            codigo, dados = self._pedir(
                "GET", f"/instance/connectionState/{self.instancia}"
            )
        except ErroEnvio as erro:
            # Contêiner fora do ar. Nao deixo o erro subir: devolvo o
            # mesmo formato de sempre, com o motivo dentro.
            return {"existe": False, "conectado": False, "detalhe": str(erro)}

        if codigo == 404:
            return {
                "existe": False,
                "conectado": False,
                "detalhe": "sessão não encontrada",
            }

        # "open" e o unico estado que significa conectado de verdade. Os
        # outros ("connecting", "close") sao sessao caindo ou ja caida.
        estado = (dados.get("instance") or {}).get("state", "")
        return {
            "existe": True,
            "conectado": estado == "open",
            "detalhe": estado or "desconhecido",
        }

    def criar_instancia(self):
        """Cria a sessao, quando ela ainda nao existe."""
        codigo, dados = self._pedir(
            "POST",
            "/instance/create",
            {
                "instanceName": self.instancia,
                # BAILEYS e a integracao que usa QR Code. E o que faz
                # funcionar com numero comum, sem conta de empresa.
                "integration": "WHATSAPP-BAILEYS",
                "qrcode": True,
            },
        )
        if codigo >= 400:
            raise ErroEnvio(f"HTTP {codigo}: {self._mensagem_de_erro(dados)}")
        return dados

    def obter_qrcode(self):
        """Pede o QR Code pra vincular o numero.

        Volta um dicionario com "code", que e o conteudo do QR pra
        desenhar na tela, e "pairingCode", que e a alternativa por
        codigo numerico digitado no aplicativo - util quando a camera
        nao colabora.
        """
        codigo, dados = self._pedir("GET", f"/instance/connect/{self.instancia}")
        if codigo >= 400:
            raise ErroEnvio(f"HTTP {codigo}: {self._mensagem_de_erro(dados)}")
        return dados

    def desconectar(self):
        """Encerra a sessao sem apagar a instancia.

        Uso logout e nao delete de proposito: assim da pra reconectar
        depois lendo o QR de novo, sem ter que criar tudo outra vez.
        """
        return self._pedir("DELETE", f"/instance/logout/{self.instancia}")


def obter_canal(profissional):
    """Devolve o canal desse profissional, ou None se ele nao pode enviar.

    Devolver None em vez de levantar erro e de proposito: agendar e a
    funcao principal do sistema, avisar e consequencia. Se o contêiner
    estiver parado ou o profissional ainda nao tiver lido o QR Code, o
    agendamento grava do mesmo jeito e a mensagem fica PENDENTE no
    banco - o comando agendado tenta de novo depois, quando a sessao
    voltar. Nada se perde.
    """
    if profissional is None:
        logger.warning(
            "Envio pedido sem profissional. Cada prestador tem a própria "
            "sessão, então o envio precisa saber de quem é a mensagem."
        )
        return None

    canal = CanalEvolution.do_profissional(profissional)
    if not canal.disponivel:
        logger.warning(
            "Sessão de WhatsApp de %s indisponível; a mensagem fica pendente.",
            profissional,
        )
        return None

    return canal