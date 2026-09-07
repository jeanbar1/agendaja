import uuid
from datetime import timedelta

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from clientes.models import Cliente
from contas.models import Local, Profissional


class IntervaloAgendamento(models.Func):
    """Constrói um ``tstzrange`` a partir dos campos início e fim.

    Usada apenas dentro da restrição de exclusão: o PostgreSQL precisa
    enxergar o período como um tipo de intervalo para aplicar o operador
    de sobreposição.
    """

    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class Servico(models.Model):
    """Serviço oferecido por um profissional.

    A duração pertence ao serviço, e não ao sistema — é o que permite
    atender áreas distintas com o mesmo software: 30 minutos para um corte,
    50 para uma sessão de fisioterapia, 240 para uma tatuagem.
    """

    class Modalidade(models.TextChoices):
        PRESENCIAL = "presencial", _("No local de atendimento")
        DOMICILIO = "domicilio", _("A domicílio")
        ONLINE = "online", _("Online")

    profissional = models.ForeignKey(
        Profissional,
        on_delete=models.CASCADE,
        related_name="servicos",
        verbose_name=_("profissional"),
    )
    nome = models.CharField(_("nome"), max_length=120)
    descricao = models.TextField(_("descrição"), blank=True)
    duracao_minutos = models.PositiveSmallIntegerField(
        _("duração (minutos)"), validators=[MinValueValidator(5)]
    )
    intervalo_apos_minutos = models.PositiveSmallIntegerField(
        _("intervalo após o atendimento (minutos)"),
        default=0,
        help_text=_("Tempo reservado para limpeza, preparo ou deslocamento."),
    )
    preco = models.DecimalField(
        _("preço"), max_digits=9, decimal_places=2, null=True, blank=True
    )
    modalidade = models.CharField(
        _("modalidade"),
        max_length=16,
        choices=Modalidade.choices,
        default=Modalidade.PRESENCIAL,
    )
    ativo = models.BooleanField(_("ativo"), default=True)

    class Meta:
        verbose_name = _("serviço")
        verbose_name_plural = _("serviços")
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["profissional", "nome"],
                name="servico_nome_unico_por_profissional",
            )
        ]

    def __str__(self):
        return f"{self.nome} ({self.duracao_minutos} min)"

    @property
    def duracao_total(self):
        """Duração ocupada na agenda, incluindo o intervalo posterior."""
        return timedelta(minutes=self.duracao_minutos + self.intervalo_apos_minutos)


class DiaSemana(models.IntegerChoices):
    """Segue a convenção ISO-8601, igual à de ``date.isoweekday()``."""

    SEGUNDA = 1, _("Segunda-feira")
    TERCA = 2, _("Terça-feira")
    QUARTA = 3, _("Quarta-feira")
    QUINTA = 4, _("Quinta-feira")
    SEXTA = 5, _("Sexta-feira")
    SABADO = 6, _("Sábado")
    DOMINGO = 7, _("Domingo")


class Disponibilidade(models.Model):
    """Janela recorrente de atendimento.

    Representa a rotina semanal ("atendo terças das 8h às 12h"), e não datas
    específicas. Ausências pontuais são tratadas por ``Bloqueio``.
    """

    profissional = models.ForeignKey(
        Profissional,
        on_delete=models.CASCADE,
        related_name="disponibilidades",
        verbose_name=_("profissional"),
    )
    local = models.ForeignKey(
        Local,
        on_delete=models.CASCADE,
        related_name="disponibilidades",
        null=True,
        blank=True,
        verbose_name=_("local"),
        help_text=_("Deixe vazio para atendimento online."),
    )
    dia_semana = models.PositiveSmallIntegerField(
        _("dia da semana"), choices=DiaSemana.choices
    )
    hora_inicio = models.TimeField(_("início"))
    hora_fim = models.TimeField(_("fim"))
    ativo = models.BooleanField(_("ativo"), default=True)

    class Meta:
        verbose_name = _("disponibilidade")
        verbose_name_plural = _("disponibilidades")
        ordering = ["dia_semana", "hora_inicio"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(hora_fim__gt=models.F("hora_inicio")),
                name="disponibilidade_fim_apos_inicio",
            )
        ]

    def __str__(self):
        return f"{self.get_dia_semana_display()} {self.hora_inicio:%H:%M}–{self.hora_fim:%H:%M}"


class Bloqueio(models.Model):
    """Indisponibilidade pontual: folga, férias ou compromisso pessoal.

    Sobrepõe-se à disponibilidade recorrente.
    """

    profissional = models.ForeignKey(
        Profissional,
        on_delete=models.CASCADE,
        related_name="bloqueios",
        verbose_name=_("profissional"),
    )
    inicio = models.DateTimeField(_("início"))
    fim = models.DateTimeField(_("fim"))
    motivo = models.CharField(_("motivo"), max_length=120, blank=True)

    class Meta:
        verbose_name = _("bloqueio")
        verbose_name_plural = _("bloqueios")
        ordering = ["-inicio"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(fim__gt=models.F("inicio")),
                name="bloqueio_fim_apos_inicio",
            )
        ]

    def __str__(self):
        return f"{self.inicio:%d/%m/%Y %H:%M} – {self.fim:%d/%m/%Y %H:%M}"


class Agendamento(models.Model):
    """Compromisso entre profissional e cliente — entidade central do sistema."""

    class Status(models.TextChoices):
        SOLICITADO = "solicitado", _("Solicitado")
        CONFIRMADO = "confirmado", _("Confirmado")
        CONCLUIDO = "concluido", _("Concluído")
        CANCELADO = "cancelado", _("Cancelado")
        FALTOU = "faltou", _("Cliente não compareceu")

    # Estados que efetivamente ocupam a agenda.
    STATUS_OCUPAM_AGENDA = [Status.SOLICITADO, Status.CONFIRMADO]

    codigo = models.UUIDField(
        _("código"),
        default=uuid.uuid4,
        editable=False,
        unique=True,
        help_text=_("Identificador do link público de confirmação e cancelamento."),
    )
    profissional = models.ForeignKey(
        Profissional,
        on_delete=models.CASCADE,
        related_name="agendamentos",
        verbose_name=_("profissional"),
    )
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name="agendamentos",
        verbose_name=_("cliente"),
    )
    servico = models.ForeignKey(
        Servico,
        on_delete=models.PROTECT,
        related_name="agendamentos",
        verbose_name=_("serviço"),
    )
    local = models.ForeignKey(
        Local,
        on_delete=models.SET_NULL,
        related_name="agendamentos",
        null=True,
        blank=True,
        verbose_name=_("local"),
    )

    inicio = models.DateTimeField(_("início"))
    fim = models.DateTimeField(_("fim"))

    status = models.CharField(
        _("situação"),
        max_length=16,
        choices=Status.choices,
        default=Status.SOLICITADO,
    )
    observacoes = models.TextField(_("observações"), blank=True)

    criado_em = models.DateTimeField(_("criado em"), auto_now_add=True)
    atualizado_em = models.DateTimeField(_("atualizado em"), auto_now=True)
    confirmado_em = models.DateTimeField(_("confirmado em"), null=True, blank=True)
    cancelado_em = models.DateTimeField(_("cancelado em"), null=True, blank=True)
    motivo_cancelamento = models.CharField(
        _("motivo do cancelamento"), max_length=160, blank=True
    )

    class Meta:
        verbose_name = _("agendamento")
        verbose_name_plural = _("agendamentos")
        ordering = ["-inicio"]
        indexes = [
            models.Index(fields=["profissional", "inicio"]),
            models.Index(fields=["status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(fim__gt=models.F("inicio")),
                name="agendamento_fim_apos_inicio",
            ),
            # Impede que o mesmo profissional tenha dois compromissos
            # sobrepostos. A verificação ocorre no banco, e não na aplicação,
            # porque duas requisições simultâneas podem passar juntas por
            # qualquer checagem feita em Python.
            ExclusionConstraint(
                name="agendamento_sem_sobreposicao",
                expressions=[
                    ("profissional", RangeOperators.EQUAL),
                    (
                        IntervaloAgendamento(
                            "inicio", "fim", RangeBoundary(inclusive_lower=True)
                        ),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=models.Q(status__in=["solicitado", "confirmado"]),
                violation_error_message=_(
                    "Já existe um agendamento para este profissional neste horário."
                ),
            ),
        ]

    def __str__(self):
        return f"{self.cliente} — {self.inicio:%d/%m/%Y %H:%M}"

    def clean(self):
        if self.inicio and self.fim and self.fim <= self.inicio:
            raise ValidationError(
                {"fim": _("O término precisa ser posterior ao início.")}
            )

    def save(self, *args, **kwargs):
        # O fim é sempre derivado da duração do serviço: evita divergência
        # entre o que foi contratado e o que foi reservado na agenda.
        if self.inicio and self.servico_id and not self.fim:
            self.fim = self.inicio + self.servico.duracao_total
        super().save(*args, **kwargs)

    @property
    def ocupa_agenda(self):
        return self.status in self.STATUS_OCUPAM_AGENDA
