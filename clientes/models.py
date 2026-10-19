"""
Os clientes e a parte da LGPD.

Esse app guarda quem e atendido e a autorizacao pra mandar mensagem pra
essa pessoa. Deixei separado do resto de proposito: e aqui que fica o
dado pessoal do sistema, entao concentrar tudo num lugar so facilita na
hora de explicar o que eu guardo e por que.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from contas.models import Profissional, validador_e164


class Cliente(models.Model):
    """Quem e atendido pelo profissional.

    Cliente nao e usuario do sistema: ele nao cria conta, nao tem senha,
    nao faz login. Marca o horario so com nome e telefone e pronto.

    O cadastro e amarrado a um profissional, e nao solto no sistema. Isso
    tem dois motivos. O primeiro e que cada um so enxerga a propria
    carteira, que foi o que o pessoal pediu no questionario. O segundo e
    que, se um dia vazar alguma coisa, vaza a lista de um profissional e
    nao a base inteira.
    """

    profissional = models.ForeignKey(
        Profissional,
        on_delete=models.CASCADE,
        related_name="clientes",
        verbose_name=_("profissional"),
    )
    nome = models.CharField(_("nome"), max_length=120)

    # Guardo sempre no formato internacional (+55 na frente). E chato
    # de digitar, mas e assim que a mensagem sai no WhatsApp - por isso
    # a view publica converte o que o cliente escreve antes de gravar.
    telefone = models.CharField(
        _("telefone"),
        max_length=16,
        validators=[validador_e164],
        help_text=_("Formato internacional, como +5577999998888."),
    )
    email = models.EmailField(_("e-mail"), blank=True)

    # Esse aviso no help_text nao e enfeite. Fisioterapeuta, psicologo,
    # nutricionista - se eu deixasse sem avisar, ia ter gente anotando
    # informacao de saude aqui. Dado de saude e dado sensivel na LGPD e
    # cai numa regra muito mais pesada. Deixei isso fora do escopo de
    # proposito e escrevi na tela pra ficar claro.
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
            # O telefone e unico dentro de cada profissional, nao no
            # sistema todo. Assim a mesma pessoa pode ser cliente do
            # barbeiro e da manicure ao mesmo tempo, com um cadastro
            # separado em cada um.
            models.UniqueConstraint(
                fields=["profissional", "telefone"],
                name="cliente_telefone_unico_por_profissional",
            )
        ]

    def __str__(self):
        return self.nome

    @property
    def pode_receber_mensagem(self):
        # So pode receber quem tem um consentimento sem data de
        # revogacao. Quem respondeu SAIR no WhatsApp ganha essa data
        # preenchida e cai fora daqui automaticamente.
        return self.consentimentos.filter(revogado_em__isnull=True).exists()


class PerfilCliente(models.Model):
    """A conta de quem contrata servico.

    Ate agora o cliente nao tinha conta: ele marcava pelo link do
    profissional so com nome e telefone, e continua podendo (e o RNF04).
    Essa conta e um caminho a mais, pra quem quer navegar entre varios
    prestadores em vez de guardar link de cada um.

    Repare que isso NAO substitui o modelo Cliente. Sao coisas
    diferentes:

    - PerfilCliente e o login da pessoa no sistema, um so;
    - Cliente e o cadastro dela dentro da carteira de UM profissional.

    Quando alguem com conta marca horario, o sistema cria (ou
    reaproveita) o Cliente daquele profissional pelo telefone. Assim
    cada profissional continua enxergando so a propria carteira, que e
    o que protege os dados de todo mundo.
    """

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil_cliente",
        verbose_name=_("usuário"),
    )
    nome = models.CharField(_("nome"), max_length=120)

    # O telefone e a chave que liga essa conta aos cadastros espalhados
    # pelos profissionais. E por ele que o sistema acha os agendamentos
    # da pessoa em todos eles.
    #
    # E por ser a chave que ele precisa ser unico. Sem isso, duas
    # contas podiam se cadastrar com o mesmo numero e cada uma passava
    # a enxergar os agendamentos da outra em "Meus agendamentos" - em
    # todos os prestadores de uma vez.
    telefone = models.CharField(
        _("telefone"),
        max_length=16,
        unique=True,
        validators=[validador_e164],
        help_text=_("Formato internacional, como +5577999998888."),
    )
    criado_em = models.DateTimeField(_("criado em"), auto_now_add=True)

    class Meta:
        verbose_name = _("perfil de cliente")
        verbose_name_plural = _("perfis de cliente")
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class ConsentimentoOptIn(models.Model):
    """A autorizacao do cliente pra receber mensagem.

    Essa tabela resolve duas coisas de uma vez. A Meta so deixa mandar
    mensagem pra quem autorizou antes, e a LGPD exige que o
    consentimento seja informado e que dê pra cancelar depois.

    O detalhe que eu acho mais importante e o texto_apresentado: eu
    gravo o texto exato que apareceu na tela na hora do aceite. Se eu
    guardasse so um "sim", daqui a seis meses eu teria mudado a redacao
    do formulario e nao teria como provar com o que a pessoa concordou
    de verdade.
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

    # Registro de onde veio o aceite. Se um dia alguem reclamar de
    # mensagem, da pra saber se foi a propria pessoa que marcou o
    # horario ou se o profissional cadastrou ela na mao.
    origem = models.CharField(
        _("origem"), max_length=32, choices=Origem.choices
    )
    texto_apresentado = models.TextField(
        _("texto apresentado"),
        help_text=_("Redação exata do aviso exibido no momento do aceite."),
    )
    concedido_em = models.DateTimeField(_("concedido em"), auto_now_add=True)

    # Revogar nao apaga o registro, so preenche essa data. Se eu
    # deletasse a linha, eu perderia a prova de que um dia teve
    # autorizacao - e e justamente esse historico que a LGPD quer ver.
    revogado_em = models.DateTimeField(_("revogado em"), null=True, blank=True)

    class Meta:
        verbose_name = _("consentimento")
        verbose_name_plural = _("consentimentos")
        ordering = ["-concedido_em"]

    def __str__(self):
        situacao = _("vigente") if self.revogado_em is None else _("revogado")
        return f"{self.cliente} — {situacao}"

    def revogar(self):
        # O if evita sobrescrever a data se alguem mandar SAIR duas
        # vezes. A data que vale e a da primeira vez que a pessoa
        # pediu pra sair.
        if self.revogado_em is None:
            self.revogado_em = timezone.now()
            self.save(update_fields=["revogado_em"])


class Feedback(models.Model):
    """A avaliacao que o cliente da depois do atendimento (RF13).

    Os 11 respondentes do questionario pediram isso, sem excecao. Foi o
    unico item em que deu unanimidade, entao virou requisito.
    """

    # OneToOne e nao ForeignKey: um atendimento rende uma avaliacao so.
    # Se fosse ForeignKey, dava pra responder o mesmo formulario varias
    # vezes e distorcer a media do profissional.
    agendamento = models.OneToOneField(
        "agenda.Agendamento",
        on_delete=models.CASCADE,
        related_name="feedback",
        verbose_name=_("agendamento"),
    )

    # Nota de 1 a 5, montada com o range pra nao ter que escrever as
    # cinco opcoes na mao.
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