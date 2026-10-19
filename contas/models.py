"""
Quem entra no sistema e quem e dono da agenda.

Esse app tem duas coisas que parecem a mesma mas nao sao: o Usuario, que
e a conta de login, e o Profissional, que e quem tem agenda. Explico o
motivo de estarem separados la embaixo.

E aqui tambem que fica a AreaAtuacao, que foi o que resolveu o pedido da
banca de o sistema servir pra qualquer profissional autonomo, e nao so
pra area de estetica.
"""

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

# Telefone tem que estar no formato E.164 (+55 na frente, so numero
# depois). Nao inventei esse formato: e o que o WhatsApp exige pra
# aceitar o envio. Se gravar de outro jeito, a mensagem nao sai.
# Exemplo: +5577999998888
validador_e164 = RegexValidator(
    regex=r"^\+[1-9]\d{7,14}$",
    message=_("Informe o telefone no formato internacional, como +5577999998888."),
)


def normalizar_telefone(texto):
    """Transforma o que a pessoa digitou em E.164, ou devolve vazio.

    Ninguem escreve telefone no formato que o WhatsApp quer. O pessoal
    digita (77) 99999-8888, 77999998888, +55 77 99999-8888... Entao eu
    jogo fora tudo que nao e numero e monto pelo tamanho: 10 ou 11
    digitos e numero daqui sem o codigo do pais, 12 ou 13 comecando com
    55 ja veio completo.

    Fica aqui porque tres telas precisam da mesma regra: o agendamento
    publico, o cadastro do profissional e o cadastro de cliente no
    painel. Se cada uma tivesse a sua copia, uma hora as tres ficariam
    diferentes.
    """
    digitos = "".join(c for c in (texto or "") if c.isdigit())
    if len(digitos) in (10, 11):
        return f"+55{digitos}"
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        return f"+{digitos}"
    return ""


class UsuarioManager(BaseUserManager):
    """O jeito de criar usuario quando o login e por e-mail.

    O manager que vem no Django exige um campo username. Como eu tirei
    esse campo, tive que reescrever esse pedaco - senao o
    createsuperuser quebra na hora de criar a conta.
    """

    # Isso aqui precisa ser True pra migracao conseguir usar o manager.
    # Sem essa linha, criar usuario dentro de uma migracao quebra.
    use_in_migrations = True

    def _criar_usuario(self, email, password, **campos_extras):
        if not email:
            raise ValueError(_("O e-mail é obrigatório."))

        # normalize_email deixa a parte do dominio em minusculo. Sem
        # isso, Joao@Gmail.com e joao@gmail.com virariam duas contas
        # diferentes, e o campo e unique - ia dar erro estranho depois.
        email = self.normalize_email(email)

        usuario = self.model(email=email, **campos_extras)

        # set_password gera o hash. Nunca atribuir direto no campo
        # password: assim a senha ficaria salva em texto puro no banco.
        usuario.set_password(password)

        usuario.save(using=self._db)
        return usuario

    def create_user(self, email, password=None, **campos_extras):
        # Usuario comum entra sem nenhum poder. Uso o setdefault pra
        # nao sobrescrever, caso alguem passe outra coisa de proposito.
        campos_extras.setdefault("is_staff", False)
        campos_extras.setdefault("is_superuser", False)
        return self._criar_usuario(email, password, **campos_extras)

    def create_superuser(self, email, password=None, **campos_extras):
        campos_extras.setdefault("is_staff", True)
        campos_extras.setdefault("is_superuser", True)

        # Confiro de novo depois do setdefault porque da pra chamar esse
        # metodo passando is_staff=False na mao. Ai sairia um
        # "superusuario" que nao consegue nem abrir o admin.
        if campos_extras.get("is_staff") is not True:
            raise ValueError(_("Superusuário precisa ter is_staff=True."))
        if campos_extras.get("is_superuser") is not True:
            raise ValueError(_("Superusuário precisa ter is_superuser=True."))

        return self._criar_usuario(email, password, **campos_extras)


class Usuario(AbstractUser):
    """A conta de login do sistema.

    So profissional e administrador tem conta aqui. O cliente nao entra
    nessa tabela: ele marca horario sem cadastro (RNF04) e fica no app
    clientes, como dado do negocio e nao como usuario.

    Troquei o login de username pra e-mail porque ninguem lembra de
    usuario inventado, mas todo mundo sabe o proprio e-mail.

    Aviso pra mim mesmo: esse modelo tem que existir ANTES do primeiro
    migrate. Se o banco ja tiver sido criado com o usuario padrao do
    Django, nao adianta trocar depois - tem que derrubar o banco e
    comecar de novo. Aprendi na pratica.
    """

    # Mato o username e ponho o e-mail como unico no lugar dele.
    username = None
    email = models.EmailField(_("e-mail"), unique=True)

    # Essas duas linhas sao o que faz o Django logar por e-mail.
    # REQUIRED_FIELDS fica vazio porque e-mail e senha ja bastam - o
    # createsuperuser nao vai pedir mais nada.
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UsuarioManager()

    class Meta:
        verbose_name = _("usuário")
        verbose_name_plural = _("usuários")
        ordering = ["email"]

    def __str__(self):
        # Mostro o nome quando tem, senao o e-mail. Fica melhor de ler
        # nas listas do admin.
        return self.get_full_name() or self.email


class AreaAtuacao(models.Model):
    """A area em que o profissional trabalha.

    Esse modelo e a resposta pro que a banca pediu. O projeto comecou so
    pra area de estetica e teve que passar a atender qualquer
    profissional autonomo.

    Podia ter feito uma lista fixa no codigo com as areas, mas ai toda
    vez que aparecesse uma profissao nova eu teria que mexer no codigo
    e rodar migracao. Como tabela, incluir area virou cadastro: quem
    administra adiciona pelo admin e acabou.
    """

    nome = models.CharField(_("nome"), max_length=80, unique=True)

    # O slug e a versao da area pra usar em URL, sem acento e sem
    # espaco. "Estética Automotiva" vira "estetica-automotiva".
    slug = models.SlugField(_("identificador"), max_length=80, unique=True)

    # Prefiro desativar a apagar: se apagasse uma area, os profissionais
    # ligados nela ficariam sem referencia.
    ativo = models.BooleanField(_("ativo"), default=True)

    class Meta:
        verbose_name = _("área de atuação")
        verbose_name_plural = _("áreas de atuação")
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Profissional(models.Model):
    """O profissional autonomo, dono da agenda.

    Separei de proposito do Usuario. O Usuario cuida de login e senha, o
    Profissional cuida do negocio - nome que aparece pro cliente, area,
    como a agenda funciona.

    Se eu jogasse tudo no Usuario, todo administrador do sistema viraria
    prestador de servico obrigatoriamente, com agenda e link publico que
    ele nunca vai usar. Do jeito que esta, quem administra tem conta e
    ponto; quem atende tem conta e mais esse registro aqui.
    """

    # OneToOne porque uma conta e um profissional so. Se fosse
    # ForeignKey, a mesma pessoa poderia ter varias agendas separadas e
    # eu nao ia saber qual mostrar depois do login.
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

    # Esse slug e o link que o profissional divulga. E unico no sistema
    # inteiro, porque ele e o endereco da pagina publica - nao pode ter
    # dois "joao".
    slug = models.SlugField(
        _("identificador do link"),
        max_length=120,
        unique=True,
        help_text=_("Define o endereço público: /agendar/<identificador>"),
    )

    # PROTECT aqui: nao deixa apagar uma area que tem profissional
    # dentro. Se fosse CASCADE, apagar "Barbearia" sem querer levaria
    # todos os barbeiros junto.
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

    # Os tres campos abaixo sao as regras da agenda. Cada profissional
    # ajusta do jeito dele, e o horarios.py le tudo daqui na hora de
    # montar a grade que o cliente ve.

    # De quantos em quantos minutos os horarios aparecem. Com 30, sai
    # 08:00, 08:30, 09:00... Quem atende rapido bota 15, quem faz
    # servico longo bota 60.
    granularidade_minutos = models.PositiveSmallIntegerField(
        _("intervalo entre horários oferecidos"),
        default=30,
        validators=[MinValueValidator(5)],
        help_text=_("De quantos em quantos minutos os horários são ofertados."),
    )

    # Evita o cliente marcar pra daqui a dez minutos. Duas horas de
    # folga da tempo do profissional ver a notificacao e se preparar.
    antecedencia_minima_horas = models.PositiveSmallIntegerField(
        _("antecedência mínima (horas)"),
        default=2,
        help_text=_("Impede agendamento em cima da hora."),
    )

    # E o contrario: impede marcar pra daqui a dois anos. Tambem serve
    # pra grade nao ficar gigante na tela do celular.
    antecedencia_maxima_dias = models.PositiveSmallIntegerField(
        _("antecedência máxima (dias)"),
        default=60,
        help_text=_("Limite de quanto tempo à frente o cliente pode agendar."),
    )

    # O nome da sessao de WhatsApp desse profissional dentro da
    # Evolution. Cada um tem a sua, porque o cliente tem que receber a
    # mensagem do numero de quem vai atender ele - nao de um numero
    # generico do sistema.
    #
    # Fica em branco ate ele conectar pela primeira vez. Dai em diante
    # nao muda mais, nem se ele trocar o slug: se o nome fosse calculado
    # na hora, trocar o link derrubaria a sessao dele sem motivo.
    instancia_whatsapp = models.CharField(
        _("sessão do WhatsApp"),
        max_length=64,
        blank=True,
        default="",
        help_text=_("Preenchido sozinho quando o profissional conecta o WhatsApp."),
    )

    # Desativar aqui derruba a pagina publica dele de uma vez, sem
    # precisar apagar nada.
    ativo = models.BooleanField(_("ativo"), default=True)
    criado_em = models.DateTimeField(_("criado em"), auto_now_add=True)

    class Meta:
        verbose_name = _("profissional")
        verbose_name_plural = _("profissionais")
        ordering = ["nome_publico"]
        constraints = [
            # Duas sessoes com o mesmo nome seriam a mesma sessao: o
            # segundo profissional a conectar roubaria o WhatsApp do
            # primeiro. Entao o nome e unico.
            #
            # A condicao e o que faz isso funcionar: a restricao so vale
            # nas linhas que ja tem instancia. Sem ela, o segundo
            # profissional que se cadastrasse dava erro, porque os dois
            # estariam com o campo em branco. E o mesmo indice parcial
            # que eu uso no Local principal.
            models.UniqueConstraint(
                fields=["instancia_whatsapp"],
                condition=~models.Q(instancia_whatsapp=""),
                name="instancia_whatsapp_unica",
            )
        ]

    def __str__(self):
        return self.nome_publico

    def garantir_instancia_whatsapp(self):
        """Devolve o nome da sessao de WhatsApp dele, criando se preciso.

        Na primeira chamada eu monto o nome a partir do slug e gravo.
        Das proximas em diante devolvo o que esta gravado, sem recalcular
        - e isso que protege o profissional de perder o WhatsApp se ele
        mudar o link depois.
        """
        if not self.instancia_whatsapp:
            prefixo = getattr(settings, "EVOLUTION_PREFIXO", "agendaja")
            self.instancia_whatsapp = f"{prefixo}-{self.slug}"[:64]
            self.save(update_fields=["instancia_whatsapp"])
        return self.instancia_whatsapp


class Local(models.Model):
    """Onde o profissional atende (RF14).

    Achei que ia ser detalhe e nao era: no questionario, 10 dos 11
    responderam que trabalham em mais de um lugar. Salao de manha,
    domicilio de tarde, coisa assim. Por isso local virou tabela
    separada, ligada tanto ao agendamento quanto a grade da semana.
    """

    profissional = models.ForeignKey(
        Profissional,
        on_delete=models.CASCADE,
        related_name="locais",
        verbose_name=_("profissional"),
    )
    nome = models.CharField(_("nome"), max_length=100)
    endereco = models.CharField(_("endereço"), max_length=255, blank=True)

    # O principal e o que entra por padrao quando o cliente marca pela
    # pagina publica, ja que la ele nao escolhe o lugar.
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
            # Essa aqui e uma restricao unica com condicao (o Postgres
            # chama de indice parcial). Ela so vale nas linhas em que
            # principal=True, entao o profissional pode ter quantos
            # locais quiser, mas so um marcado como principal.
            #
            # Se fosse unique normal no campo profissional, ele so
            # poderia cadastrar um local no total - que e o oposto do
            # que eu queria.
            models.UniqueConstraint(
                fields=["profissional"],
                condition=models.Q(principal=True),
                name="local_principal_unico_por_profissional",
            ),
        ]

    def __str__(self):
        return self.nome