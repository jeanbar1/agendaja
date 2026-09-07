from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

# Telefone no formato E.164, exigido pela API oficial do WhatsApp.
# Ex.: +5577999998888
validador_e164 = RegexValidator(
    regex=r"^\+[1-9]\d{7,14}$",
    message=_("Informe o telefone no formato internacional, como +5577999998888."),
)


class UsuarioManager(BaseUserManager):
    """Gerenciador de usuários que identifica a conta pelo e-mail.

    Substitui o gerenciador padrão do Django, que exige o campo ``username``.
    """

    use_in_migrations = True

    def _criar_usuario(self, email, password, **campos_extras):
        if not email:
            raise ValueError(_("O e-mail é obrigatório."))
        email = self.normalize_email(email)
        usuario = self.model(email=email, **campos_extras)
        usuario.set_password(password)
        usuario.save(using=self._db)
        return usuario

    def create_user(self, email, password=None, **campos_extras):
        campos_extras.setdefault("is_staff", False)
        campos_extras.setdefault("is_superuser", False)
        return self._criar_usuario(email, password, **campos_extras)

    def create_superuser(self, email, password=None, **campos_extras):
        campos_extras.setdefault("is_staff", True)
        campos_extras.setdefault("is_superuser", True)

        if campos_extras.get("is_staff") is not True:
            raise ValueError(_("Superusuário precisa ter is_staff=True."))
        if campos_extras.get("is_superuser") is not True:
            raise ValueError(_("Superusuário precisa ter is_superuser=True."))

        return self._criar_usuario(email, password, **campos_extras)


class Usuario(AbstractUser):
    """Identidade de autenticação do sistema.

    Apenas profissionais e administradores possuem conta. O cliente final
    agenda sem cadastro (RNF04), por isso não é representado aqui — ele é
    uma entidade de domínio, definida no app ``clientes``.
    """

    username = None
    email = models.EmailField(_("e-mail"), unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UsuarioManager()

    class Meta:
        verbose_name = _("usuário")
        verbose_name_plural = _("usuários")
        ordering = ["email"]

    def __str__(self):
        return self.get_full_name() or self.email


class AreaAtuacao(models.Model):
    """Área de atuação do profissional.

    Modelada como tabela, e não como lista fixa no código, porque o sistema
    atende profissionais autônomos de qualquer segmento. Incluir uma área
    nova é operação de cadastro, não alteração de código nem migração.
    """

    nome = models.CharField(_("nome"), max_length=80, unique=True)
    slug = models.SlugField(_("identificador"), max_length=80, unique=True)
    ativo = models.BooleanField(_("ativo"), default=True)

    class Meta:
        verbose_name = _("área de atuação")
        verbose_name_plural = _("áreas de atuação")
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Profissional(models.Model):
    """Profissional autônomo — titular da agenda.

    Separado de ``Usuario`` de propósito: ``Usuario`` responde por
    autenticação, ``Profissional`` por domínio. A separação permite que
    administradores do sistema existam sem serem prestadores de serviço.
    """

    usuario = models.OneToOneField(
        Usuario,
        on_delete=models.CASCADE,
        related_name="profissional",
        verbose_name=_("usuário"),
    )
    nome_publico = models.CharField(
        _("nome público"),
        max_length=120,
        help_text=_("Nome exibido ao cliente na página de agendamento."),
    )
    slug = models.SlugField(
        _("identificador do link"),
        max_length=120,
        unique=True,
        help_text=_("Define o endereço público: /agendar/<identificador>"),
    )
    area = models.ForeignKey(
        AreaAtuacao,
        on_delete=models.PROTECT,
        related_name="profissionais",
        verbose_name=_("área de atuação"),
    )
    telefone = models.CharField(
        _("telefone"), max_length=16, validators=[validador_e164]
    )
    apresentacao = models.TextField(_("apresentação"), blank=True)

    granularidade_minutos = models.PositiveSmallIntegerField(
        _("intervalo entre horários oferecidos"),
        default=30,
        validators=[MinValueValidator(5)],
        help_text=_("De quantos em quantos minutos os horários são ofertados."),
    )
    antecedencia_minima_horas = models.PositiveSmallIntegerField(
        _("antecedência mínima (horas)"),
        default=2,
        help_text=_("Impede agendamento em cima da hora."),
    )
    antecedencia_maxima_dias = models.PositiveSmallIntegerField(
        _("antecedência máxima (dias)"),
        default=60,
        help_text=_("Limite de quanto tempo à frente o cliente pode agendar."),
    )

    ativo = models.BooleanField(_("ativo"), default=True)
    criado_em = models.DateTimeField(_("criado em"), auto_now_add=True)

    class Meta:
        verbose_name = _("profissional")
        verbose_name_plural = _("profissionais")
        ordering = ["nome_publico"]

    def __str__(self):
        return self.nome_publico


class Local(models.Model):
    """Local de atendimento.

    Atende ao RF14. No questionário aplicado, 10 dos 11 respondentes
    declararam trabalhar em mais de um lugar.
    """

    profissional = models.ForeignKey(
        Profissional,
        on_delete=models.CASCADE,
        related_name="locais",
        verbose_name=_("profissional"),
    )
    nome = models.CharField(_("nome"), max_length=100)
    endereco = models.CharField(_("endereço"), max_length=255, blank=True)
    principal = models.BooleanField(_("principal"), default=False)
    ativo = models.BooleanField(_("ativo"), default=True)

    class Meta:
        verbose_name = _("local de atendimento")
        verbose_name_plural = _("locais de atendimento")
        ordering = ["-principal", "nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["profissional", "nome"],
                name="local_nome_unico_por_profissional",
            ),
            # Garante no máximo um local principal por profissional.
            models.UniqueConstraint(
                fields=["profissional"],
                condition=models.Q(principal=True),
                name="local_principal_unico_por_profissional",
            ),
        ]

    def __str__(self):
        return self.nome
