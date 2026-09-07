from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from contas.models import Profissional, validador_e164


class Cliente(models.Model):
    """Cliente atendido por um profissional.

    Não é um usuário do sistema: o cliente agenda sem criar conta (RNF04).
    O vínculo é com o profissional, não global — cada profissional enxerga
    apenas a própria carteira, o que também limita o alcance de um eventual
    vazamento.
    """

    profissional = models.ForeignKey(
        Profissional,
        on_delete=models.CASCADE,
        related_name="clientes",
        verbose_name=_("profissional"),
    )
    nome = models.CharField(_("nome"), max_length=120)
    telefone = models.CharField(
        _("telefone"),
        max_length=16,
        validators=[validador_e164],
        help_text=_("Formato internacional, como +5577999998888."),
    )
    email = models.EmailField(_("e-mail"), blank=True)
    observacoes = models.TextField(
        _("observações"),
        blank=True,
        help_text=_(
            "Anotações operacionais sobre o atendimento. "
            "Não registre informação de saúde: o sistema não trata dado sensível."
        ),
    )
    criado_em = models.DateTimeField(_("criado em"), auto_now_add=True)

    class Meta:
        verbose_name = _("cliente")
        verbose_name_plural = _("clientes")
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["profissional", "telefone"],
                name="cliente_telefone_unico_por_profissional",
            )
        ]

    def __str__(self):
        return self.nome

    @property
    def pode_receber_mensagem(self):
        """Indica se há consentimento vigente para envio de mensagens."""
        return self.consentimentos.filter(revogado_em__isnull=True).exists()


class ConsentimentoOptIn(models.Model):
    """Registro do consentimento para recebimento de mensagens.

    Atende simultaneamente a duas exigências: a política da Meta, que
    condiciona o envio de mensagens ao aceite prévio do destinatário, e a
    LGPD, que exige consentimento informado e a possibilidade de revogação.

    O texto apresentado é gravado junto para que o consentimento continue
    demonstrável mesmo depois de a redação do formulário mudar.
    """

    class Origem(models.TextChoices):
        AGENDAMENTO_ONLINE = "agendamento_online", _("Agendamento online")
        CADASTRO_MANUAL = "cadastro_manual", _("Cadastro manual pelo profissional")
        IMPORTACAO = "importacao", _("Importação de contatos")

    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,
        related_name="consentimentos",
        verbose_name=_("cliente"),
    )
    origem = models.CharField(
        _("origem"), max_length=32, choices=Origem.choices
    )
    texto_apresentado = models.TextField(
        _("texto apresentado"),
        help_text=_("Redação exata do aviso exibido no momento do aceite."),
    )
    concedido_em = models.DateTimeField(_("concedido em"), auto_now_add=True)
    revogado_em = models.DateTimeField(_("revogado em"), null=True, blank=True)

    class Meta:
        verbose_name = _("consentimento")
        verbose_name_plural = _("consentimentos")
        ordering = ["-concedido_em"]

    def __str__(self):
        situacao = _("vigente") if self.revogado_em is None else _("revogado")
        return f"{self.cliente} — {situacao}"

    def revogar(self):
        if self.revogado_em is None:
            self.revogado_em = timezone.now()
            self.save(update_fields=["revogado_em"])


class Feedback(models.Model):
    """Avaliação do cliente após o atendimento (RF13).

    Solicitado por 11 dos 11 respondentes do questionário.
    """

    agendamento = models.OneToOneField(
        "agenda.Agendamento",
        on_delete=models.CASCADE,
        related_name="feedback",
        verbose_name=_("agendamento"),
    )
    nota = models.PositiveSmallIntegerField(
        _("nota"),
        choices=[(n, str(n)) for n in range(1, 6)],
    )
    comentario = models.TextField(_("comentário"), blank=True)
    respondido_em = models.DateTimeField(_("respondido em"), auto_now_add=True)

    class Meta:
        verbose_name = _("feedback")
        verbose_name_plural = _("feedbacks")
        ordering = ["-respondido_em"]

    def __str__(self):
        return f"{self.agendamento} — nota {self.nota}"
