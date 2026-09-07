from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Notificacao(models.Model):
    """Registro de uma mensagem enviada, ou a enviar.

    Funciona como fila e como log ao mesmo tempo: a notificação nasce
    pendente com uma data de disparo, e o comando enviar_lembretes
    processa o que já venceu.

    O conteúdo enviado é gravado junto porque a API da Meta não devolve o
    texto renderizado do template. Sem esse registro não haveria como
    demonstrar depois o que foi enviado a cada destinatário — necessário
    tanto para depuração quanto para conformidade com a LGPD.

    O texto das mensagens fica em notificacoes/mensagens.py, e não aqui:
    o modelo guarda o resultado, não a regra de formatação.
    """

    class Tipo(models.TextChoices):
        # Dirigidas ao cliente
        CONFIRMACAO = "confirmacao", _("Confirmação do agendamento")
        LEMBRETE = "lembrete", _("Lembrete")
        CANCELAMENTO = "cancelamento", _("Cancelamento")
        FEEDBACK = "feedback", _("Pedido de avaliação")
        # Dirigidas ao profissional
        NOVO_AGENDAMENTO = "novo_agendamento", _("Novo agendamento recebido")
        CANCELAMENTO_CLIENTE = (
            "cancelamento_cliente",
            _("Cancelamento feito pelo cliente"),
        )

    class Destinatario(models.TextChoices):
        CLIENTE = "cliente", _("Cliente")
        PROFISSIONAL = "profissional", _("Profissional")

    class Situacao(models.TextChoices):
        PENDENTE = "pendente", _("Aguardando envio")
        ENVIADA = "enviada", _("Enviada")
        ENTREGUE = "entregue", _("Entregue")
        LIDA = "lida", _("Lida")
        FALHOU = "falhou", _("Falhou")
        CANCELADA = "cancelada", _("Cancelada")

    class Canal(models.TextChoices):
        META = "meta", _("API oficial da Meta")
        EVOLUTION = "evolution", _("Evolution API")
        SIMULADO = "simulado", _("Simulado")
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

    telefone = models.CharField(
        _("telefone"),
        max_length=16,
        help_text=_("Copiado no momento do envio, para preservar o histórico."),
    )
    template = models.CharField(_("template"), max_length=64)
    conteudo = models.TextField(
        _("conteúdo"), help_text=_("Texto reconstruído da mensagem enviada.")
    )

    situacao = models.CharField(
        _("situação"),
        max_length=16,
        choices=Situacao.choices,
        default=Situacao.PENDENTE,
    )
    canal = models.CharField(
        _("canal"), max_length=16, choices=Canal.choices, blank=True
    )

    agendada_para = models.DateTimeField(
        _("agendada para"),
        help_text=_("Momento a partir do qual a mensagem pode ser enviada."),
    )
    enviada_em = models.DateTimeField(_("enviada em"), null=True, blank=True)

    id_mensagem = models.CharField(
        _("id da mensagem"),
        max_length=128,
        blank=True,
        help_text=_("Identificador devolvido pelo provedor, usado no webhook."),
    )
    tentativas = models.PositiveSmallIntegerField(_("tentativas"), default=0)
    erro = models.TextField(_("erro"), blank=True)

    criada_em = models.DateTimeField(_("criada em"), auto_now_add=True)

    class Meta:
        verbose_name = _("notificação")
        verbose_name_plural = _("notificações")
        ordering = ["-agendada_para"]
        indexes = [
            models.Index(fields=["situacao", "agendada_para"]),
            models.Index(fields=["id_mensagem"]),
        ]
        constraints = [
            # Impede duplicar o mesmo aviso para o mesmo agendamento.
            models.UniqueConstraint(
                fields=["agendamento", "tipo"],
                name="notificacao_unica_por_tipo",
            )
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.telefone}"

    @property
    def para_o_profissional(self):
        return self.destinatario == self.Destinatario.PROFISSIONAL

    @property
    def esta_vencida(self):
        return (
            self.situacao == self.Situacao.PENDENTE
            and self.agendada_para <= timezone.now()
        )

    def marcar_enviada(self, canal, id_mensagem=""):
        self.situacao = self.Situacao.ENVIADA
        self.canal = canal
        self.id_mensagem = id_mensagem
        self.enviada_em = timezone.now()
        self.erro = ""
        self.tentativas += 1
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
        self.situacao = self.Situacao.FALHOU
        self.erro = str(mensagem)[:2000]
        self.tentativas += 1
        self.save(update_fields=["situacao", "erro", "tentativas"])

    def cancelar(self):
        if self.situacao == self.Situacao.PENDENTE:
            self.situacao = self.Situacao.CANCELADA
            self.save(update_fields=["situacao"])