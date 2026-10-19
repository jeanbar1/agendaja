import uuid
from datetime import timedelta

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import (
    DateTimeRangeField,
    RangeBoundary,
    RangeOperators,
)
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from clientes.models import Cliente
from contas.models import Local, Profissional

# So esses dois status seguram o horario na agenda. Cancelado, concluido
# e faltou deixam o horario livre de novo.
#
# Fica solto aqui fora e nao dentro do Agendamento porque a restricao de
# exclusao precisa dele, e o Meta nao enxerga o que esta declarado na
# classe de fora. Se eu repetisse a lista nos dois lugares, um dia eu
# acrescentaria um status novo em um so - e a trava contra horario
# duplicado deixaria de valer pra ele calada.
STATUS_QUE_OCUPAM_AGENDA = ["solicitado", "confirmado"]


class IntervaloAgendamento(models.Func):
    """Monta um tstzrange juntando o inicio e o fim.

    Serve so pra restricao de exclusao la embaixo. O Postgres precisa ver
    o periodo como intervalo pra conseguir comparar se dois se cruzam.
    """

    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class Servico(models.Model):
    """O que o profissional oferece.

    Cada servico tem sua propria duracao. Foi assim que consegui atender
    areas diferentes com o mesmo sistema: corte de cabelo 30 min,
    fisioterapia 50, tatuagem 4 horas.
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
    # Liga o questionario de triagem so neste servico. Quem faz obra
    # nunca precisa disso; quem aplica botox precisa sempre. Por isso e
    # opcional e fica no servico, nao no profissional - o mesmo
    # profissional pode ter um servico que pergunta e outro que nao.
    exige_perguntas = models.BooleanField(
        _("fazer perguntas ao cliente"),
        default=False,
        help_text=_(
            "O cliente responde um questionário ao agendar, e você decide "
            "se confirma ou não depois de ler as respostas."
        ),
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
        # Quanto o servico ocupa de verdade na agenda: o atendimento mais
        # o intervalo depois. O cliente ve so a duracao do atendimento.
        return timedelta(minutes=self.duracao_minutos + self.intervalo_apos_minutos)


class DiaSemana(models.IntegerChoices):
    """Numeracao igual a do date.isoweekday(), pra nao ter que converter."""

    SEGUNDA = 1, _("Segunda-feira")
    TERCA = 2, _("Terça-feira")
    QUARTA = 3, _("Quarta-feira")
    QUINTA = 4, _("Quinta-feira")
    SEXTA = 5, _("Sexta-feira")
    SABADO = 6, _("Sábado")
    DOMINGO = 7, _("Domingo")


class Disponibilidade(models.Model):
    """Os horarios que o profissional atende toda semana.

    Guarda a rotina ("atendo terca das 8h as 12h"), nao datas especificas.
    Folga e ferias eu trato no Bloqueio.
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
            # Nao deixa cadastrar janela terminando antes de comecar.
            models.CheckConstraint(
                condition=models.Q(hora_fim__gt=models.F("hora_inicio")),
                name="disponibilidade_fim_apos_inicio",
            )
        ]

    def __str__(self):
        return (
            f"{self.get_dia_semana_display()} "
            f"{self.hora_inicio:%H:%M}–{self.hora_fim:%H:%M}"
        )


class Bloqueio(models.Model):
    """Quando o profissional nao vai atender: folga, ferias, compromisso.

    Tem prioridade sobre a disponibilidade da semana.
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
    """O horario marcado entre o cliente e o profissional.

    E a tabela principal do sistema.
    """

    class Status(models.TextChoices):
        SOLICITADO = "solicitado", _("Solicitado")
        CONFIRMADO = "confirmado", _("Confirmado")
        CONCLUIDO = "concluido", _("Concluído")
        CANCELADO = "cancelado", _("Cancelado")
        FALTOU = "faltou", _("Cliente não compareceu")

    STATUS_OCUPAM_AGENDA = STATUS_QUE_OCUPAM_AGENDA

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
            # Essa aqui e a parte mais importante do sistema.
            #
            # Ela impede que o mesmo profissional tenha dois horarios
            # marcados em cima um do outro. Deixei no banco e nao na view
            # de proposito: se dois clientes clicarem em "confirmar" no
            # mesmo segundo, os dois passam pela verificacao em Python e
            # os dois gravam. So o banco consegue barrar isso de verdade.
            #
            # O "condition" no final e o que faz o horario voltar a ficar
            # livre quando o agendamento e cancelado.
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
                condition=models.Q(status__in=STATUS_QUE_OCUPAM_AGENDA),
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
        # Calculo o fim a partir da duracao do servico em vez de deixar
        # alguem preencher na mao. Assim nao tem risco do que foi marcado
        # ficar diferente do que foi reservado na agenda.
        #
        # Recalculo sempre, e nao so quando o fim esta vazio. Antes,
        # remarcar um agendamento mudava o inicio e deixava o fim velho
        # la atras: o atendimento das 14h com fim as 10h30. Isso passava
        # batido na trava do banco, que so olha o intervalo gravado.
        if self.inicio and self.servico_id:
            self.fim = self.inicio + self.servico.duracao_total
        super().save(*args, **kwargs)

    @property
    def ocupa_agenda(self):
        return self.status in self.STATUS_OCUPAM_AGENDA


class Pergunta(models.Model):
    """Uma pergunta que o cliente responde antes de o horario ser aceito.

    Chamei de "pergunta" e nao de anamnese de proposito. Anamnese e
    palavra da area da saude; o sistema atende barbeiro, eletricista e
    fisioterapeuta, e os dois primeiros nao teriam ideia do que isso
    significa. Na monografia eu descrevo como questionario de triagem
    previa, mas na tela usa a palavra que todo mundo entende.

    Quem monta as perguntas e o profissional, e elas sao do servico -
    nao do profissional. Assim o tatuador pergunta sobre alergia a tinta
    no servico de tatuagem e nao pergunta nada no de orcamento.
    """

    class Tipo(models.TextChoices):
        SIM_NAO = "sim_nao", _("Sim ou não")
        TEXTO = "texto", _("Resposta escrita")

    class Alerta(models.TextChoices):
        NENHUM = "", _("Nunca alertar")
        SIM = "sim", _("Alertar se responder SIM")
        NAO = "nao", _("Alertar se responder NÃO")

    servico = models.ForeignKey(
        Servico,
        on_delete=models.CASCADE,
        related_name="perguntas",
        verbose_name=_("serviço"),
    )
    texto = models.CharField(_("pergunta"), max_length=200)
    tipo = models.CharField(
        _("tipo"), max_length=8, choices=Tipo.choices, default=Tipo.SIM_NAO
    )

    # Marca qual resposta acende o sinal vermelho pro profissional. Sem
    # isso ele teria que ler resposta por resposta procurando o que
    # importa; com isso o problema salta na tela.
    alerta_se = models.CharField(
        _("quando alertar"),
        max_length=4,
        choices=Alerta.choices,
        blank=True,
        default="",
        help_text=_("Destaca a resposta em vermelho para você conferir."),
    )

    obrigatoria = models.BooleanField(_("obrigatória"), default=True)
    ordem = models.PositiveSmallIntegerField(_("ordem"), default=0)
    ativa = models.BooleanField(_("ativa"), default=True)

    class Meta:
        verbose_name = _("pergunta")
        verbose_name_plural = _("perguntas")
        ordering = ["ordem", "pk"]

    def __str__(self):
        return self.texto


class Resposta(models.Model):
    """O que o cliente respondeu, guardado junto do agendamento.

    Gravo o texto da pergunta junto da resposta. Parece repeticao, mas
    nao e: se o profissional editar a pergunta depois, a resposta antiga
    continuaria apontando pra uma pergunta que nao e mais a que foi
    feita. Do jeito que esta, o que ficou registrado e o que a pessoa
    realmente leu e respondeu.
    """

    agendamento = models.ForeignKey(
        Agendamento,
        on_delete=models.CASCADE,
        related_name="respostas",
        verbose_name=_("agendamento"),
    )
    pergunta = models.ForeignKey(
        Pergunta,
        on_delete=models.SET_NULL,
        null=True,
        related_name="respostas",
        verbose_name=_("pergunta"),
    )
    texto_pergunta = models.CharField(_("pergunta feita"), max_length=200)
    resposta = models.TextField(_("resposta"))

    # Copio tambem a regra de alerta, pelo mesmo motivo do texto: se a
    # regra mudar depois, o que vale e a que estava valendo na hora.
    alerta_se = models.CharField(_("quando alertar"), max_length=4, blank=True)

    class Meta:
        verbose_name = _("resposta")
        verbose_name_plural = _("respostas")
        ordering = ["pk"]

    def __str__(self):
        return f"{self.texto_pergunta}: {self.resposta}"

    @property
    def merece_atencao(self):
        """Diz se e a resposta que o profissional marcou como alerta."""
        if not self.alerta_se:
            return False
        return self.resposta.strip().lower() == self.alerta_se