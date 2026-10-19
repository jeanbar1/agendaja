from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Notificacao(models.Model):
    """Uma mensagem que foi enviada, ou que ainda vai ser.

    Essa tabela e fila e historico ao mesmo tempo. A notificacao nasce
    pendente com uma data de disparo, e o comando enviar_lembretes passa
    de tempos em tempos pegando o que ja venceu.

    Fazer as duas coisas na mesma tabela foi de proposito: assim eu nao
    preciso de Celery, Redis nem nada rodando por tras. A fila e uma
    consulta no banco, e o historico ja fica pronto de graca.

    O conteudo da mensagem fica gravado aqui, e nao remontado depois. Se
    eu mudar a redacao de uma mensagem daqui a seis meses, o historico
    continua mostrando o que a pessoa recebeu de fato naquele dia - e
    isso vale tanto pra achar problema quanto pra responder a LGPD.

    A redacao em si fica no notificacoes/mensagens.py, nao aqui. Esse
    modelo guarda o resultado, nao a regra de como escrever.
    """

    MAXIMO_TENTATIVAS = 3

    class Tipo(models.TextChoices):
        # Vao pro cliente
        CONFIRMACAO = "confirmacao", _("Confirmação do agendamento")
        LEMBRETE = "lembrete", _("Lembrete")
        CANCELAMENTO = "cancelamento", _("Cancelamento")
        FEEDBACK = "feedback", _("Pedido de avaliação")
        # Vao pro profissional. Esses dois nasceram do questionario:
        # quem atende tambem quer ser avisado, principalmente quando o
        # cliente desmarca.
        NOVO_AGENDAMENTO = "novo_agendamento", _("Novo agendamento recebido")
        CANCELAMENTO_CLIENTE = (
            "cancelamento_cliente",
            _("Cancelamento feito pelo cliente"),
        )

    class Destinatario(models.TextChoices):
        CLIENTE = "cliente", _("Cliente")
        PROFISSIONAL = "profissional", _("Profissional")

    class Situacao(models.TextChoices):
        # As quatro primeiras sao uma escada: a mensagem nasce pendente,
        # vira enviada quando sai daqui, e depois o webhook avisa quando
        # ela chegou no celular e quando foi lida.
        PENDENTE = "pendente", _("Aguardando envio")
        ENVIADA = "enviada", _("Enviada")
        ENTREGUE = "entregue", _("Entregue")
        LIDA = "lida", _("Lida")
        # Essas duas sao saidas do caminho normal.
        FALHOU = "falhou", _("Falhou")
        CANCELADA = "cancelada", _("Cancelada")

    class Canal(models.TextChoices):
        EVOLUTION = "evolution", _("Evolution API")
        # Nao e um canal de verdade: e o link wa.me que a tela oferece
        # pro profissional mandar na mao quando o automatico falhou.
        LINK_MANUAL = "link_manual", _("Link wa.me para envio manual")

    agendamento = models.ForeignKey(
        "agenda.Agendamento",
        on_delete=models.CASCADE,
        related_name="notificacoes",
        verbose_name=_("agendamento"),
    )
    tipo = models.CharField(_("tipo"), max_length=24, choices=Tipo.choices)
    destinatario = models.CharField(
        _("destinatário"),
        max_length=16,
        choices=Destinatario.choices,
        default=Destinatario.CLIENTE,
    )

    # Copio o telefone pra dentro da notificacao em vez de sempre buscar
    # no cliente. Se ele trocar de numero depois, o historico continua
    # mostrando pra onde a mensagem realmente foi.
    telefone = models.CharField(
        _("telefone"),
        max_length=16,
        help_text=_("Copiado no momento do envio, para preservar o histórico."),
    )
    conteudo = models.TextField(
        _("conteúdo"), help_text=_("Texto reconstruído da mensagem enviada.")
    )

    situacao = models.CharField(
        _("situação"),
        max_length=16,
        choices=Situacao.choices,
        default=Situacao.PENDENTE,
    )

    # Fica em branco enquanto nao saiu. Depois guarda por onde saiu: se
    # a mensagem foi automatica ou se o profissional mandou na mao pelo
    # link wa.me.
    canal = models.CharField(
        _("canal"), max_length=16, choices=Canal.choices, blank=True
    )

    # Esse campo e o que transforma a tabela em fila. O lembrete de
    # amanha ja nasce gravado, com a data de amanha aqui, e fica quieto
    # ate chegar a hora.
    agendada_para = models.DateTimeField(
        _("agendada para"),
        help_text=_("Momento a partir do qual a mensagem pode ser enviada."),
    )
    enviada_em = models.DateTimeField(_("enviada em"), null=True, blank=True)

    # E por esse identificador que o webhook consegue achar a mensagem
    # aqui quando o provedor avisa "essa foi entregue" ou "essa foi
    # lida". Por isso ele tem indice proprio la embaixo.
    id_mensagem = models.CharField(
        _("id da mensagem"),
        max_length=128,
        blank=True,
        help_text=_("Identificador devolvido pela Evolution, usado no webhook."),
    )
    tentativas = models.PositiveSmallIntegerField(_("tentativas"), default=0)
    erro = models.TextField(_("erro"), blank=True)

    criada_em = models.DateTimeField(_("criada em"), auto_now_add=True)

    class Meta:
        verbose_name = _("notificação")
        verbose_name_plural = _("notificações")
        ordering = ["-agendada_para"]
        indexes = [
            # Esse indice e exatamente a consulta que o enviar_pendentes
            # faz: filtra por situacao e por data. Sem ele, o banco
            # varreria a tabela inteira toda vez que o comando rodasse.
            models.Index(fields=["situacao", "agendada_para"]),
            # Esse aqui e pro webhook, que busca pelo identificador que
            # a Evolution manda.
            models.Index(fields=["id_mensagem"]),
        ]
        constraints = [
            # Impede mandar o mesmo aviso duas vezes pro mesmo
            # agendamento. E essa restricao que o criar_notificacao usa:
            # em vez de consultar antes pra ver se ja existe, ele tenta
            # gravar e trata o erro. Consultar antes nao resolveria,
            # porque duas execucoes ao mesmo tempo passariam as duas.
            models.UniqueConstraint(
                fields=["agendamento", "tipo"],
                name="notificacao_unica_por_tipo",
            )
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.telefone}"

    @property
    def para_o_profissional(self):
        # Uso isso no template do historico pra separar visualmente o
        # que foi pro cliente do que foi pro profissional.
        return self.destinatario == self.Destinatario.PROFISSIONAL

    @property
    def esta_vencida(self):
        # Deveria ter saido e nao saiu. Se aparecer muita coisa assim, e
        # sinal de que o comando agendado parou de rodar.
        return (
            self.situacao == self.Situacao.PENDENTE
            and self.agendada_para <= timezone.now()
        )

    def marcar_enviada(self, canal, id_mensagem=""):
        self.situacao = self.Situacao.ENVIADA
        self.canal = canal
        self.id_mensagem = id_mensagem
        self.enviada_em = timezone.now()
        # Limpo o erro: se tinha falhado antes e agora foi, a mensagem
        # de erro velha so ia confundir.
        self.erro = ""
        self.tentativas += 1
        # update_fields pra o UPDATE mexer so no que mudou, em vez de
        # reescrever a linha toda (inclusive o conteudo, que e grande).
        self.save(
            update_fields=[
                "situacao",
                "canal",
                "id_mensagem",
                "enviada_em",
                "erro",
                "tentativas",
            ]
        )

    def marcar_falha(self, mensagem):
        # Corto o erro em 2000 caracteres. A Evolution as vezes devolve
        # uma pagina de erro inteira, e nao preciso guardar tudo isso.
        self.erro = str(mensagem)[:2000]
        self.tentativas += 1

        # Enquanto ainda sobra tentativa, a mensagem continua PENDENTE:
        # a proxima execucao do comando pega ela de novo. So marco
        # FALHOU quando esgotou.
        #
        # Antes eu marcava FALHOU ja na primeira falha, e como a fila so
        # busca o que esta PENDENTE, a mensagem nunca mais era tentada -
        # o contador de tentativas existia mas nao servia pra nada. Se a
        # Evolution caisse por um minuto, o lembrete se perdia de vez.
        if self.tentativas >= self.MAXIMO_TENTATIVAS:
            self.situacao = self.Situacao.FALHOU

        self.save(update_fields=["situacao", "erro", "tentativas"])

    def cancelar(self):
        # So cancela o que ainda esta pendente. Se ja saiu, nao tem como
        # voltar atras - a mensagem ja esta no celular da pessoa.
        if self.situacao == self.Situacao.PENDENTE:
            self.situacao = self.Situacao.CANCELADA
            self.save(update_fields=["situacao"])