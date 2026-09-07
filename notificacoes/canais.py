"""
Canais de envio de notificação.

O sistema não depende de um provedor específico de WhatsApp. Esta camada
define um contrato comum e três implementações; a escolha é feita por
configuração, em ``WHATSAPP_CANAL``, sem alterar nenhuma regra de negócio.

A separação existe por uma razão prática. A API oficial da Meta exige
verificação de negócio com CNPJ para uso em produção — requisito que boa
parte dos profissionais autônomos, público-alvo deste trabalho, não atende.
Desacoplar o canal permite operar com uma alternativa no protótipo e migrar
para a via oficial trocando uma linha de configuração.

Canais disponíveis:

``simulado``
    Registra o envio sem sair da máquina. Padrão quando não há credenciais.
    Permite desenvolver e demonstrar o sistema completo sem depender de
    serviço externo.

``meta``
    API oficial (Cloud API). Envia templates previamente aprovados.

``evolution``
    Evolution API, executada em contêiner local. Envia texto livre, sem
    exigir aprovação de template nem verificação de negócio.
"""

import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TEMPO_LIMITE = 15  # segundos


class ErroEnvio(Exception):
    """Falha ao entregar a mensagem ao provedor."""


# ---------------------------------------------------------------------------
# Contrato
# ---------------------------------------------------------------------------

class CanalNotificacao:
    """Contrato comum a todos os canais.

    O método de envio recebe **as duas formas** da mensagem: o template com
    seus parâmetros, usado pela via oficial, e o texto já montado, usado
    pelos canais que enviam mensagem livre. Cada implementação usa o que
    lhe serve, e quem chama não precisa saber qual é qual.
    """

    nome = "abstrato"

    @property
    def disponivel(self):
        """Indica se o canal tem configuração suficiente para operar."""
        raise NotImplementedError

    def enviar(self, telefone, template, parametros, texto):
        """Envia a mensagem.

        Returns:
            Identificador da mensagem no provedor.

        Raises:
            ErroEnvio: quando o provedor recusa o envio.
        """
        raise NotImplementedError

    def enviar_texto(self, telefone, texto):
        """Envia texto livre, sem template.

        Usado nas respostas automáticas do webhook.
        """
        raise NotImplementedError

    @staticmethod
    def _so_digitos(telefone):
        return telefone.lstrip("+")


# ---------------------------------------------------------------------------
# Simulado
# ---------------------------------------------------------------------------

class CanalSimulado(CanalNotificacao):
    """Registra o envio no log, sem contatar serviço nenhum.

    Não é apenas um recurso de desenvolvimento: também é a contingência
    para o caso de o provedor estar fora do ar, garantindo que o sistema
    continue registrando corretamente o que deveria ter sido enviado.
    """

    nome = "simulado"

    @property
    def disponivel(self):
        return True

    def enviar(self, telefone, template, parametros, texto):
        logger.info(
            "[SIMULADO] para=%s template=%s\n%s",
            telefone, template, texto,
        )
        return f"simulado-{template}-{self._so_digitos(telefone)}"

    def enviar_texto(self, telefone, texto):
        logger.info("[SIMULADO] texto para %s: %s", telefone, texto[:120])
        return f"simulado-texto-{self._so_digitos(telefone)}"


# ---------------------------------------------------------------------------
# Meta Cloud API (oficial)
# ---------------------------------------------------------------------------

class CanalMeta(CanalNotificacao):
    """API oficial do WhatsApp Business.

    Mensagem iniciada pela empresa só é aceita no formato de template
    previamente aprovado pela Meta. Texto livre funciona apenas dentro da
    janela de 24 horas aberta por uma mensagem do cliente.
    """

    nome = "meta"

    def __init__(self, token=None, phone_number_id=None, versao=None):
        self.token = token if token is not None else settings.WHATSAPP_TOKEN
        self.phone_number_id = (
            phone_number_id
            if phone_number_id is not None
            else settings.WHATSAPP_PHONE_NUMBER_ID
        )
        self.versao = versao or settings.WHATSAPP_API_VERSION

    @property
    def disponivel(self):
        return bool(self.token and self.phone_number_id)

    @property
    def _url(self):
        return (
            f"https://graph.facebook.com/{self.versao}/"
            f"{self.phone_number_id}/messages"
        )

    @property
    def _cabecalhos(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def enviar(self, telefone, template, parametros, texto):
        # Template sem variáveis não pode receber o bloco de parâmetros:
        # a Meta recusa com "template param count mismatch".
        modelo = {"name": template, "language": {"code": "pt_BR"}}
        if parametros:
            modelo["components"] = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(v)} for v in parametros
                    ],
                }
            ]

        return self._postar({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self._so_digitos(telefone),
            "type": "template",
            "template": modelo,
        })

    def enviar_texto(self, telefone, texto):
        return self._postar({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self._so_digitos(telefone),
            "type": "text",
            "text": {"preview_url": False, "body": texto},
        })

    def _postar(self, corpo):
        try:
            resposta = requests.post(
                self._url,
                headers=self._cabecalhos,
                data=json.dumps(corpo),
                timeout=TEMPO_LIMITE,
            )
        except requests.RequestException as erro:
            raise ErroEnvio(f"Falha de rede ao contatar a Meta: {erro}") from erro

        if resposta.status_code >= 400:
            raise ErroEnvio(self._descrever_erro(resposta))

        dados = resposta.json()
        mensagens = dados.get("messages") or []
        if not mensagens:
            raise ErroEnvio(f"Resposta sem identificador: {dados}")
        return mensagens[0].get("id", "")

    @staticmethod
    def _descrever_erro(resposta):
        """Extrai a mensagem útil do JSON aninhado que a Meta devolve."""
        try:
            dados = resposta.json()
        except ValueError:
            return f"HTTP {resposta.status_code}: {resposta.text[:300]}"

        erro = dados.get("error", {})
        partes = [
            f"HTTP {resposta.status_code}",
            erro.get("message", ""),
            erro.get("error_user_msg", ""),
            f"(código {erro.get('code')})" if erro.get("code") else "",
        ]
        return " ".join(p for p in partes if p).strip()


# ---------------------------------------------------------------------------
# Evolution API (não oficial)
# ---------------------------------------------------------------------------

class CanalEvolution(CanalNotificacao):
    """Evolution API, executada em contêiner local.

    Envia texto livre a partir do próprio número do profissional, vinculado
    por leitura de QR Code. Dispensa aprovação de template, verificação de
    negócio e CNPJ.

    Limitação a declarar no trabalho: a Evolution opera sobre a interface
    web do WhatsApp, o que contraria os termos de uso do serviço e implica
    risco de bloqueio do número. É adequada ao protótipo; para operação
    contínua, o canal oficial é o recomendado — e por isso os dois estão
    implementados.
    """

    nome = "evolution"

    def __init__(self, url=None, instancia=None, chave=None):
        self.url = (url or settings.EVOLUTION_URL or "").rstrip("/")
        self.instancia = instancia or settings.EVOLUTION_INSTANCIA
        self.chave = chave or settings.EVOLUTION_API_KEY

    @property
    def disponivel(self):
        return bool(self.url and self.instancia and self.chave)

    @property
    def _cabecalhos(self):
        return {"apikey": self.chave, "Content-Type": "application/json"}

    def enviar(self, telefone, template, parametros, texto):
        # A Evolution não conhece templates: envia o texto já montado.
        return self.enviar_texto(telefone, texto)

    def enviar_texto(self, telefone, texto):
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
            raise ErroEnvio(
                f"Não consegui falar com a Evolution em {self.url}. "
                f"O contêiner está no ar? Detalhe: {erro}"
            ) from erro

        if resposta.status_code >= 400:
            raise ErroEnvio(f"HTTP {resposta.status_code}: {resposta.text[:300]}")

        try:
            dados = resposta.json()
        except ValueError:
            return ""

        # A Evolution devolve o identificador em key.id.
        return (dados.get("key") or {}).get("id", "")

    # -- Administração da instância -----------------------------------------
    # Uma "instância" é uma sessão de WhatsApp vinculada a um número. Estes
    # métodos existem para o comando `manage.py whatsapp`, que cuida do
    # ciclo de vida da sessão sem exigir que se monte requisição na mão.

    def _pedir(self, metodo, caminho, corpo=None):
        endereco = f"{self.url}{caminho}"
        try:
            resposta = requests.request(
                metodo,
                endereco,
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

        return resposta.status_code, dados

    def estado_da_conexao(self):
        """Consulta se a instância está conectada ao WhatsApp.

        Sem isso, o profissional só descobre que a sessão caiu quando uma
        mensagem falha.
        """
        try:
            codigo, dados = self._pedir(
                "GET", f"/instance/connectionState/{self.instancia}"
            )
        except ErroEnvio as erro:
            return {"existe": False, "conectado": False, "detalhe": str(erro)}

        if codigo == 404:
            return {
                "existe": False,
                "conectado": False,
                "detalhe": "instância não encontrada",
            }

        estado = (dados.get("instance") or {}).get("state", "")
        return {
            "existe": True,
            "conectado": estado == "open",
            "detalhe": estado or "desconhecido",
        }

    def criar_instancia(self):
        """Cria a instância, se ainda não existir."""
        codigo, dados = self._pedir(
            "POST",
            "/instance/create",
            {
                "instanceName": self.instancia,
                "integration": "WHATSAPP-BAILEYS",
                "qrcode": True,
            },
        )
        if codigo >= 400:
            mensagem = dados.get("response", {}).get("message") or dados
            raise ErroEnvio(f"HTTP {codigo}: {mensagem}")
        return dados

    def obter_qrcode(self):
        """Pede o QR Code para vincular o número.

        Returns:
            ``dict`` com ``code`` (conteúdo do QR, para desenhar no
            terminal) e ``pairingCode`` (alternativa por código numérico,
            digitada no aplicativo).
        """
        codigo, dados = self._pedir(
            "GET", f"/instance/connect/{self.instancia}"
        )
        if codigo >= 400:
            mensagem = dados.get("response", {}).get("message") or dados
            raise ErroEnvio(f"HTTP {codigo}: {mensagem}")
        return dados

    def desconectar(self):
        """Encerra a sessão sem apagar a instância."""
        return self._pedir("DELETE", f"/instance/logout/{self.instancia}")


# ---------------------------------------------------------------------------
# Seleção do canal
# ---------------------------------------------------------------------------

CANAIS = {
    CanalSimulado.nome: CanalSimulado,
    CanalMeta.nome: CanalMeta,
    CanalEvolution.nome: CanalEvolution,
}


def obter_canal(nome=None):
    """Devolve o canal configurado, com queda para o simulado.

    Se o canal escolhido estiver sem configuração completa, o sistema não
    falha: registra o aviso e opera em modo simulado. Isso evita que uma
    variável de ambiente esquecida derrube o agendamento — que é a função
    essencial — por causa da mensagem, que é acessória.
    """
    nome = nome or getattr(settings, "WHATSAPP_CANAL", CanalSimulado.nome)
    classe = CANAIS.get(nome)

    if classe is None:
        logger.warning("Canal '%s' não existe; usando o simulado.", nome)
        return CanalSimulado()

    canal = classe()
    if not canal.disponivel:
        logger.warning(
            "Canal '%s' sem configuração completa; usando o simulado.", nome
        )
        return CanalSimulado()

    return canal
